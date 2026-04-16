[CmdletBinding()]
param(
    [string]$Port = $(if ($env:PICO_PORT) { $env:PICO_PORT } else { "auto" }),
    [switch]$AllPy,
    [switch]$Core,
    [switch]$Clean,
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
    .\sync_pico.ps1 [--all-py|--core] [--clean] [--port=auto|COM3]
    .\sync_pico.ps1 [-AllPy | -Core] [-Clean] [-Port auto|COM3]

Default behavior:
    Upload all top-level *.py files from the project root.

Options:
    --all-py, -AllPy    Upload all top-level *.py files from project root.
    --core, -Core       Upload only DMX_controller.py and DMX_native_wrapper.py.
    --clean, -Clean     Delete all files on Pico before upload.
    --port=..., -Port   Serial device or auto (default: auto or `$env:PICO_PORT).
    --help, -Help       Show this help.

Notes:
    - Make sure VS Code Pico extension / serial monitor is disconnected.
    - This script does not flash UF2; it syncs Python files to board FS.
"@
}

function ConvertFrom-LegacyArgs {
    param([string[]]$LegacyArgs)

    foreach ($arg in $LegacyArgs) {
        switch -Regex ($arg) {
            '^--all-py$' { $script:AllPy = $true; continue }
            '^--core$' { $script:Core = $true; continue }
            '^--clean$' { $script:Clean = $true; continue }
            '^--help$' { $script:Help = $true; continue }
            '^-h$' { $script:Help = $true; continue }
            '^--port=(.+)$' { $script:Port = $Matches[1]; continue }
            default { throw "Unknown option: $arg`nRun '.\sync_pico.ps1 --help'" }
        }
    }
}

function Resolve-Mode {
    if ($AllPy -and $Core) {
        throw "Use either -AllPy/--all-py or -Core/--core, not both."
    }

    if ($Core) {
        return "core"
    }

    return "all"
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
            Label   = "mpremote"
        }
    }

    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $candidates += @{
            Command = $pyCmd.Source
            Prefix  = @("-m", "mpremote")
            Label   = "py -m mpremote"
        }
    }

    $pythonCandidates = @()

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and $pythonCmd.Source -notmatch '\\WindowsApps\\') {
        $pythonCandidates += $pythonCmd.Source
    }

    $bundledPython = Get-ChildItem -Path (Join-Path $env:USERPROFILE ".pico-sdk\python") -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -ExpandProperty FullName
    $pythonCandidates += $bundledPython

    foreach ($pythonExe in ($pythonCandidates | Select-Object -Unique)) {
        $candidates += @{
            Command = $pythonExe
            Prefix  = @("-m", "mpremote")
            Label   = "$pythonExe -m mpremote"
        }
    }

    foreach ($candidate in $candidates) {
        if ($candidate.Prefix.Count -eq 0) {
            if (Test-MpRemoteCandidate -Command $candidate.Command) {
                return $candidate
            }
        }
        else {
            if (Test-MpRemoteCandidate -Command $candidate.Command -Prefix $candidate.Prefix) {
                return $candidate
            }
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
        if ($message -match 'failed to access (COM\d+)') {
            throw "Port '$($Matches[1])' is busy or inaccessible. Disconnect the Pico serial monitor / MicroPico vREPL and try again."
        }
        if ($message -match 'could not enter raw repl') {
            throw "Connected to '$($Arguments[1])', but the device did not enter MicroPython raw REPL. Make sure the board is running MicroPython and no serial monitor is attached."
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
        throw "No serial ports found."
    }

    if ($found.Count -eq 1) {
        return $found[0]
    }

    $working = @()
    foreach ($candidate in $found) {
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

    throw "Multiple serial ports found ($($found -join ', ')), and none could be confirmed as a Pico. Specify one with -Port or --port=..."
}

function Get-SelectedFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Mode
    )

    if ($Mode -eq "core") {
        return @(
            (Join-Path $script:ScriptDir "DMX_controller.py"),
            (Join-Path $script:ScriptDir "DMX_native_wrapper.py")
        )
    }

    return @(Get-ChildItem -Path $script:ScriptDir -File -Filter "*.py" |
        Sort-Object Name |
        Select-Object -ExpandProperty FullName)
}

ConvertFrom-LegacyArgs -LegacyArgs $RemainingArgs

if ($Port -in @("--all-py", "--core", "--clean", "--help", "-h")) {
    ConvertFrom-LegacyArgs -LegacyArgs @($Port)
    $Port = if ($env:PICO_PORT) { $env:PICO_PORT } else { "auto" }
}
elseif ($Port -like "--port=*") {
    ConvertFrom-LegacyArgs -LegacyArgs @($Port)
    $Port = $script:Port
}

if ($Help) {
    Show-Usage
    exit 0
}

try {
    $mode = Resolve-Mode
    $mpremote = Find-MpRemote
    $resolvedPort = Resolve-Port -Requested $Port -MpRemote $mpremote

    if (-not (Test-PortAvailable -PortName $resolvedPort)) {
        throw "Port '$resolvedPort' is busy. Disconnect Pico extension/REPL first."
    }

    Write-Host "Connecting to Pico on $resolvedPort ..."
    Invoke-MpRemote -MpRemote $mpremote -Arguments @("connect", $resolvedPort, "fs", "ls") | Out-Null

    if ($Clean) {
        Write-Host "Deleting all files on Pico..."
        $filesOnPico = Invoke-MpRemote -MpRemote $mpremote -Arguments @("connect", $resolvedPort, "fs", "ls")

        foreach ($entry in $filesOnPico) {
            $line = "$entry".Trim()
            if (-not $line) {
                continue
            }

            $name = ($line -split '\s+')[-1]
            if (-not $name) {
                continue
            }

            Write-Host "  Removing: $name"
            Invoke-MpRemote -MpRemote $mpremote -Arguments @("connect", $resolvedPort, "fs", "rm", ":$name") -IgnoreErrors | Out-Null
        }
    }

    $files = @(Get-SelectedFiles -Mode $mode)
    if ($files.Count -eq 0) {
        throw "No files selected for upload."
    }

    Write-Host "Uploading $($files.Count) file(s)..."
    foreach ($file in $files) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            Write-Warning "Missing file, skipping: $file"
            continue
        }

        $base = Split-Path -Leaf $file
        Write-Host "  -> $base"
        Invoke-MpRemote -MpRemote $mpremote -Arguments @("connect", $resolvedPort, "fs", "cp", $file, ":$base") | Out-Null
    }

    Write-Host "Soft reset..."
    Invoke-MpRemote -MpRemote $mpremote -Arguments @("connect", $resolvedPort, "soft-reset") | Out-Null

    Write-Host "Verifying DMX native API..."
    Invoke-MpRemote -MpRemote $mpremote -Arguments @(
        "connect",
        $resolvedPort,
        "exec",
        "import dmx_native; s=dmx_native.status(); print('status_has_start_code=', 'start_code' in s, 'start_code=', s.get('start_code'))"
    ) | ForEach-Object {
        if ($_ -ne $null) {
            Write-Host $_
        }
    }

    Write-Host "Done."
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
