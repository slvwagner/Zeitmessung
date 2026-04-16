[CmdletBinding()]
param(
    [string]$Port = $(if ($env:PICO_PORT) { $env:PICO_PORT } else { "auto" }),
    [switch]$AllPy,
    [switch]$Core,
    [switch]$Clean,
    [switch]$NoFlash,
    [Alias("h")]
    [switch]$Help,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Show-Usage {
    @"
Usage:
    .\full_update.ps1 [--all-py|--core] [--clean] [--no-flash] [--port=auto|COM3]
    .\full_update.ps1 [-AllPy | -Core] [-Clean] [-NoFlash] [-Port auto|COM3]

What it does (default):
    1) Builds firmware (build_firmware.ps1)
    2) Flashes UF2 to Pico (machine.bootloader() + USB mass storage copy)
    3) Uploads Python files to board filesystem (sync_pico.ps1)

Options:
    --no-flash, -NoFlash  Skip firmware flash step (only build + sync Python files).
    --all-py, -AllPy      Upload all top-level *.py files after flash (default).
    --core, -Core         Upload only DMX_controller.py and DMX_native_wrapper.py.
    --clean, -Clean       Delete all files on Pico before upload.
    --port=..., -Port     Serial device or auto (default: auto or `$env:PICO_PORT).
    --help, -Help         Show this help.

Note:
    Disconnect MicroPico vREPL/extension before running; the port must be free.
"@
}

function ConvertFrom-LegacyArgs {
    param([string[]]$LegacyArgs)

    foreach ($arg in $LegacyArgs) {
        switch -Regex ($arg) {
            '^--all-py$' { $script:AllPy = $true; continue }
            '^--core$' { $script:Core = $true; continue }
            '^--clean$' { $script:Clean = $true; continue }
            '^--no-flash$' { $script:NoFlash = $true; continue }
            '^--help$' { $script:Help = $true; continue }
            '^-h$' { $script:Help = $true; continue }
            '^--port=(.+)$' { $script:Port = $Matches[1]; continue }
            default { throw "Unknown option: $arg`nRun '.\full_update.ps1 --help'" }
        }
    }
}

function Normalize-PortValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "auto"
    }

    return $Value.Trim()
}

function Test-ShouldUseBootselFallback {
    param([string]$Message)

    if ([string]::IsNullOrWhiteSpace($Message)) {
        return $true
    }

    $nonFallbackPatterns = @(
        "busy. Disconnect Pico extension/REPL first",
        "could not be confirmed as a Pico",
        "Multiple Pico-compatible serial ports found",
        "not found. Available ports",
        "A Pico USB device is connected, but no serial COM port is currently available"
    )

    foreach ($pattern in $nonFallbackPatterns) {
        if ($Message -like "*$pattern*") {
            return $false
        }
    }

    return $true
}

function Test-MpRemoteCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [string[]]$Prefix = @()
    )

    try {
        $probeArgs = @($Prefix + @("--help"))
        & $Command @probeArgs *> $null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Find-MpRemote {
    $candidates = @()

    $mpremoteCmd = Get-Command mpremote -ErrorAction SilentlyContinue
    if ($mpremoteCmd) {
        $candidates += @{
            Command = $mpremoteCmd.Source
            Prefix  = @()
        }
    }

    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $candidates += @{
            Command = $pyCmd.Source
            Prefix  = @("-m", "mpremote")
        }
    }

    $pythonCandidates = @()
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and $pythonCmd.Source -notmatch '\\WindowsApps\\') {
        $pythonCandidates += $pythonCmd.Source
    }

    $bundledPythonRoot = Join-Path $env:USERPROFILE ".pico-sdk\python"
    if (Test-Path $bundledPythonRoot) {
        $pythonCandidates += Get-ChildItem -Path $bundledPythonRoot -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -ExpandProperty FullName
    }

    foreach ($pythonExe in ($pythonCandidates | Select-Object -Unique)) {
        $candidates += @{
            Command = $pythonExe
            Prefix  = @("-m", "mpremote")
        }
    }

    foreach ($candidate in $candidates) {
        if (Test-MpRemoteCandidate -Command $candidate.Command -Prefix $candidate.Prefix) {
            return $candidate
        }
    }

    $hint = "mpremote not found. Install it into a real Python environment, e.g. '$env:USERPROFILE\.pico-sdk\python\3.13.7\python.exe -m pip install mpremote'"
    throw $hint
}

function Get-SerialPorts {
    @([System.IO.Ports.SerialPort]::GetPortNames()) |
        Sort-Object `
            @{ Expression = { if ($_ -match '^COM(\d+)$') { [int]$Matches[1] } else { [int]::MaxValue } } },
            @{ Expression = { $_ } }
}

function Get-WindowsPortMetadata {
    $ports = @{}

    try {
        $output = & pnputil /enum-devices /class Ports 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $output) {
            return $ports
        }

        $current = @{}
        foreach ($line in $output) {
            if ($line -match '^Instance ID:\s+(.+)$') {
                if ($current.PortName) {
                    $ports[$current.PortName] = [pscustomobject]$current
                }
                $current = @{
                    InstanceId = $Matches[1].Trim()
                }
                continue
            }

            if (-not $current.Count) {
                continue
            }

            if ($line -match '^Device Description:\s+(.+)$') {
                $current.Description = $Matches[1].Trim()
                if ($current.Description -match '\((COM\d+)\)') {
                    $current.PortName = $Matches[1]
                }
                continue
            }

            if ($line -match '^Manufacturer Name:\s+(.+)$') {
                $current.Manufacturer = $Matches[1].Trim()
                continue
            }

            if ($line -match '^Status:\s+(.+)$') {
                $current.Status = $Matches[1].Trim()
                continue
            }
        }

        if ($current.PortName) {
            $ports[$current.PortName] = [pscustomobject]$current
        }
    }
    catch {
        return @{}
    }

    return $ports
}

function Get-ConnectedPicoUsbDevices {
    $devices = @()

    try {
        $output = & pnputil /enum-devices /connected 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $output) {
            return $devices
        }

        $current = @{}
        foreach ($line in $output) {
            if ($line -match '^Instance ID:\s+(.+)$') {
                if ($current.InstanceId -and $current.InstanceId -match 'VID_2E8A') {
                    $devices += [pscustomobject]$current
                }
                $current = @{
                    InstanceId = $Matches[1].Trim()
                }
                continue
            }

            if (-not $current.Count) {
                continue
            }

            if ($line -match '^Device Description:\s+(.+)$') {
                $current.Description = $Matches[1].Trim()
                if ($current.Description -match '\((COM\d+)\)') {
                    $current.PortName = $Matches[1]
                }
                continue
            }

            if ($line -match '^Class Name:\s+(.+)$') {
                $current.ClassName = $Matches[1].Trim()
                continue
            }

            if ($line -match '^Status:\s+(.+)$') {
                $current.Status = $Matches[1].Trim()
                continue
            }
        }

        if ($current.InstanceId -and $current.InstanceId -match 'VID_2E8A') {
            $devices += [pscustomobject]$current
        }
    }
    catch {
        return @()
    }

    return $devices
}

function Test-PortAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PortName
    )

    $serial = [System.IO.Ports.SerialPort]::new($PortName)
    try {
        $serial.Open()
        $serial.Close()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $serial) {
            if ($serial.IsOpen) {
                $serial.Close()
            }
            $serial.Dispose()
        }
    }
}

function Invoke-MpRemote {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$MpRemote,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [switch]$IgnoreErrors
    )

    if ($MpRemote.Prefix.Count -eq 0) {
        $allArgs = @($Arguments)
    }
    else {
        $allArgs = @($MpRemote.Prefix + $Arguments)
    }

    $output = & $MpRemote.Command @allArgs 2>&1
    $exitCode = $LASTEXITCODE

    if (-not $IgnoreErrors -and $exitCode -ne 0) {
        $message = ($output | Out-String).Trim()
        if (-not $message) {
            $message = "mpremote exited with code $exitCode."
        }
        throw $message
    }

    return @($output)
}

function Test-MpRemotePort {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$MpRemote,

        [Parameter(Mandatory = $true)]
        [string]$PortName
    )

    try {
        Invoke-MpRemote -MpRemote $MpRemote -Arguments @("connect", $PortName, "fs", "ls") | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Resolve-Port {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Requested,

        [Parameter(Mandatory = $true)]
        [hashtable]$MpRemote
    )

    $found = @(Get-SerialPorts)
    $portMetadata = Get-WindowsPortMetadata
    $connectedPicoDevices = @(Get-ConnectedPicoUsbDevices)
    $connectedPicoPorts = @(
        $connectedPicoDevices |
            Where-Object { $_.PortName } |
            Select-Object -ExpandProperty PortName -Unique
    )

    if ($Requested -ne "auto") {
        if ($found -contains $Requested) {
            return $Requested
        }

        if ($found.Count -gt 0) {
            throw "Port '$Requested' not found. Available ports: $($found -join ', ')"
        }

        throw "Port '$Requested' not found and no serial ports are currently available."
    }

    if ($found.Count -eq 0) {
        if ($connectedPicoDevices.Count -gt 0) {
            throw "A Pico USB device is connected, but no serial COM port is currently available."
        }

        throw "No serial ports found, and no connected Pico USB device was detected."
    }

    if ($found.Count -eq 1) {
        return $found[0]
    }

    $knownPicoPorts = @(
        foreach ($candidate in $found) {
            $meta = $portMetadata[$candidate]
            if ($candidate -in $connectedPicoPorts) {
                $candidate
                continue
            }

            if ($meta -and $meta.InstanceId -match 'VID_2E8A' -and $meta.Status -eq 'Started') {
                $candidate
            }
        }
    )

    if ($knownPicoPorts.Count -eq 1) {
        Write-Host "Auto-detected Pico USB serial port on $($knownPicoPorts[0])"
        return $knownPicoPorts[0]
    }

    $working = @()
    $candidatesToProbe = if ($knownPicoPorts.Count -gt 0) { $knownPicoPorts } else { $found }

    foreach ($candidate in $candidatesToProbe) {
        if (-not (Test-PortAvailable -PortName $candidate)) {
            continue
        }

        if (Test-MpRemotePort -MpRemote $MpRemote -PortName $candidate) {
            $working += $candidate
        }
    }

    if ($working.Count -eq 1) {
        Write-Host "Auto-detected Pico on $($working[0])"
        return $working[0]
    }

    if ($working.Count -gt 1) {
        throw "Multiple Pico-compatible serial ports found: $($working -join ', '). Specify one with -Port or --port=..."
    }

    if ($connectedPicoDevices.Count -eq 0) {
        throw "Multiple serial ports found ($($found -join ', ')), but no connected Pico USB device was detected."
    }

    throw "Multiple serial ports found ($($found -join ', ')), and none could be confirmed as a Pico. Specify one with -Port or --port=..."
}

function Get-BootloaderDrive {
    $labelPatterns = @("RP2350", "RPI-RP2", "RPI-RP2350", "RPI*")
    $volumes = Get-CimInstance Win32_LogicalDisk |
        Where-Object {
            $volumeName = $_.VolumeName
            $_.DeviceID -and
            $_.DriveType -in @(2, 3) -and
            $volumeName -and
            (@($labelPatterns | Where-Object { $volumeName -like $_ }).Count -gt 0)
        }

    foreach ($volume in $volumes) {
        $root = "$($volume.DeviceID)\"
        if (Test-Path $root) {
            return $root
        }
    }

    return $null
}

function Wait-ForBootloaderDrive {
    param([int]$TimeoutSeconds = 20)

    Write-Host "Waiting for USB mass storage..."
    for ($elapsed = 0; $elapsed -lt $TimeoutSeconds; $elapsed++) {
        $drive = Get-BootloaderDrive
        if ($drive) {
            return $drive
        }

        Write-Host ("  BOOTSEL mount: {0,2}s / {1,2}s" -f $elapsed, $TimeoutSeconds)
        Start-Sleep -Seconds 1
    }

    Write-Host ("  BOOTSEL mount: {0,2}s / {1,2}s" -f $TimeoutSeconds, $TimeoutSeconds)
    return $null
}

function Wait-ForSerialReconnect {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RequestedPort,

        [int]$TimeoutSeconds = 20
    )

    Write-Host "Waiting for Pico serial device..."
    for ($elapsed = 0; $elapsed -lt $TimeoutSeconds; $elapsed++) {
        $ports = @(Get-SerialPorts)
        if ($RequestedPort -eq "auto") {
            if ($ports.Count -gt 0) {
                Start-Sleep -Seconds 2
                Write-Host "Serial device detected: $($ports -join ', ')"
                return
            }
        }
        elseif ($ports -contains $RequestedPort) {
            Start-Sleep -Seconds 2
            Write-Host "Pico is back on $RequestedPort."
            return
        }

        Write-Host ("  Serial reconnect: {0,2}s / {1,2}s" -f $elapsed, $TimeoutSeconds)
        Start-Sleep -Seconds 1
    }

    if ($RequestedPort -eq "auto") {
        Write-Warning "No serial port detected after $TimeoutSeconds seconds; Pico may still be booting or the port may enumerate later."
    }
    else {
        Write-Warning "Port '$RequestedPort' not back after $TimeoutSeconds seconds; Pico may still be booting or the port name may have changed."
    }
}

function Flash-Uf2 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uf2Path,

        [Parameter(Mandatory = $true)]
        [string]$PortName,

        [Parameter(Mandatory = $true)]
        [hashtable]$MpRemote
    )

    if (-not (Test-PortAvailable -PortName $PortName)) {
        throw "Port '$PortName' is busy. Disconnect Pico extension/REPL first."
    }

    Write-Host "Rebooting Pico into bootloader mode..."
    Invoke-MpRemote -MpRemote $MpRemote -Arguments @("connect", $PortName, "exec", "import machine; machine.bootloader()") -IgnoreErrors | Out-Null
    Start-Sleep -Seconds 1

    $drive = Wait-ForBootloaderDrive
    if (-not $drive) {
        throw "Pico USB mass storage not found. Is the Pico connected via USB?"
    }

    Write-Host "Copying $(Split-Path -Leaf $Uf2Path) to $drive ..."
    Copy-Item -LiteralPath $Uf2Path -Destination (Join-Path $drive (Split-Path -Leaf $Uf2Path)) -Force
    Wait-ForSerialReconnect -RequestedPort $PortName
}

function Flash-Uf2Direct {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uf2Path
    )

    $drive = Wait-ForBootloaderDrive
    if (-not $drive) {
        throw "Pico USB mass storage not found. Put Pico into BOOTSEL mode, then retry."
    }

    Write-Host "Copying $(Split-Path -Leaf $Uf2Path) to $drive ..."
    Copy-Item -LiteralPath $Uf2Path -Destination (Join-Path $drive (Split-Path -Leaf $Uf2Path)) -Force
}

ConvertFrom-LegacyArgs -LegacyArgs $RemainingArgs

if ($Port -in @("--all-py", "--core", "--clean", "--no-flash", "--help", "-h")) {
    ConvertFrom-LegacyArgs -LegacyArgs @($Port)
    $Port = if ($env:PICO_PORT) { $env:PICO_PORT } else { "auto" }
}
elseif ($Port -like "--port=*") {
    ConvertFrom-LegacyArgs -LegacyArgs @($Port)
    $Port = $script:Port
}

$Port = Normalize-PortValue -Value $Port

if ($Help) {
    Show-Usage
    exit 0
}

if ($AllPy -and $Core) {
    throw "Use either -AllPy/--all-py or -Core/--core, not both."
}

$buildScript = Join-Path $script:ScriptDir "build_firmware.ps1"
$syncScript = Join-Path $script:ScriptDir "sync_pico.ps1"
$uf2Path = Join-Path $script:ScriptDir "firmware\firmware-RPI_PICO2_W.uf2"

if (-not (Test-Path $buildScript)) {
    throw "Build script not found: $buildScript"
}
if (-not (Test-Path $syncScript)) {
    throw "Sync script not found: $syncScript"
}

Write-Host "==> Building firmware"
& $buildScript
if ($LASTEXITCODE -ne 0) {
    throw "Firmware build failed."
}

if (-not (Test-Path $uf2Path)) {
    throw "UF2 not found at $uf2Path"
}

if (-not $NoFlash) {
    Write-Host "==> Flashing firmware to Pico"
    $mpremote = Find-MpRemote

    try {
        $resolvedPort = Resolve-Port -Requested $Port -MpRemote $mpremote
        Flash-Uf2 -Uf2Path $uf2Path -PortName $resolvedPort -MpRemote $mpremote
    }
    catch {
        $flashMessage = $_.Exception.Message
        Write-Warning $flashMessage

        if (-not (Test-ShouldUseBootselFallback -Message $flashMessage)) {
            throw
        }

        Write-Host "No usable serial port available; trying direct BOOTSEL mass-storage flash..."
        Flash-Uf2Direct -Uf2Path $uf2Path
        Wait-ForSerialReconnect -RequestedPort $Port
    }
}

Write-Host "==> Syncing Python files to Pico"
$syncParams = @{}
if ($Core) {
    $syncParams.Core = $true
}
else {
    $syncParams.AllPy = $true
}
if ($Clean) {
    $syncParams.Clean = $true
}
$syncParams.Port = $Port

& $syncScript @syncParams
if ($LASTEXITCODE -ne 0) {
    throw "Python file sync failed."
}

Write-Host "==> Full update finished"
