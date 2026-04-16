# PowerShell script to build MicroPython firmware for Pico 2 W
# using the Raspberry Pi VS Code extension toolchain (cmake + ninja).

[CmdletBinding()]
param(
    [string]$Board = 'RPI_PICO2_W',
    [switch]$NoClean,
    [switch]$UseRepoPicoSdk,
    [string]$BuildRoot = '',
    [int]$Jobs = [System.Environment]::ProcessorCount
)

$ErrorActionPreference = 'Stop'

function Resolve-SingleVersionPath {
    param(
        [Parameter(Mandatory = $true)][string]$BaseDir,
        [Parameter(Mandatory = $true)][string]$PreferredName
    )

    $preferred = Join-Path $BaseDir $PreferredName
    if (Test-Path $preferred) {
        return $preferred
    }

    if (-not (Test-Path $BaseDir)) {
        throw "Required Pico tool directory not found: $BaseDir"
    }

    $candidates = Get-ChildItem -Path $BaseDir -Directory | Sort-Object Name -Descending
    if (-not $candidates) {
        throw "No installed versions found in $BaseDir"
    }

    return $candidates[0].FullName
}

function Get-GitDescribe {
    param([string]$Dir)
    try {
        git -C $Dir describe --tags --always 2>$null
    } catch {
        'unknown'
    }
}

function Get-GitCommit {
    param([string]$Dir)
    try {
        git -C $Dir rev-parse --short=12 HEAD 2>$null
    } catch {
        'unknown'
    }
}

function Get-GitStableBase {
    param([string]$Dir)
    try {
        $tags = git -C $Dir tag --merged HEAD 2>$null |
            Select-String -Pattern '^v[0-9]+\.[0-9]+\.[0-9]+$' |
            ForEach-Object { $_.Line }
        if ($tags) {
            $tags | Sort-Object { [version]($_ -replace '^v', '') } | Select-Object -Last 1
        } else {
            'unknown'
        }
    } catch {
        'unknown'
    }
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter()][string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        $renderedArgs = ($Arguments | ForEach-Object {
            if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ }
        }) -join ' '
        throw ("Command failed with exit code {0}: {1} {2}" -f $LASTEXITCODE, $FilePath, $renderedArgs)
    }
}

function New-TemporarySubstDrive {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath
    )

    $used = @((Get-PSDrive -PSProvider FileSystem).Name)
    $preferred = @('M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z')
    $candidates = $preferred | Where-Object { $_ -notin $used }

    foreach ($drive in $candidates) {
        try {
            Invoke-External -FilePath "$env:SystemRoot\System32\subst.exe" -Arguments @("${drive}:", $RootPath)
            return "${drive}:"
        } catch {
            continue
        }
    }

    throw "No usable drive letter available for temporary SUBST mapping."
}

function Remove-TemporarySubstDrive {
    param([string]$Drive)

    if ($Drive) {
        & "$env:SystemRoot\System32\subst.exe" $Drive '/D' | Out-Null
    }
}

function Remove-DirectoryWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Attempts = 5,
        [int]$DelayMs = 750
    )

    if (-not (Test-Path $Path)) {
        return $true
    }

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            Remove-Item $Path -Recurse -Force -ErrorAction Stop
            return $true
        } catch {
            if ($attempt -eq $Attempts) {
                return $false
            }
            Start-Sleep -Milliseconds $DelayMs
        }
    }

    return $false
}

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RP2_DIR = Join-Path $SCRIPT_DIR 'micropython\ports\rp2'
$MP_GIT_DIR = Join-Path $SCRIPT_DIR 'micropython'
$MPY_CROSS_DIR = Join-Path $MP_GIT_DIR 'mpy-cross'
$MPY_CROSS_VCXPROJ = Join-Path $MPY_CROSS_DIR 'mpy-cross.vcxproj'
$MPY_CROSS_EXE = Join-Path $MPY_CROSS_DIR 'build\mpy-cross.exe'
$LOCAL_SDK_DIR = Join-Path $MP_GIT_DIR 'lib\pico-sdk'
$FIRMWARE_DIR = Join-Path $SCRIPT_DIR 'firmware'
$BOARD_DIR = Join-Path $RP2_DIR "boards\$Board"
$USER_C_MODULES = Join-Path $SCRIPT_DIR 'native_modules\micropython.cmake'
$CMAKE_HELPER = Join-Path $SCRIPT_DIR 'cmake\prebuilt-pico-tools.cmake'
$WINDOWS_TOOLS_DIR = Join-Path $SCRIPT_DIR 'tools-win'

if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $BuildRoot = Join-Path $env:TEMP 'zeitmessung-micropython-rp2'
}
$DEFAULT_BUILD_DIR = Join-Path $BuildRoot $Board
$BUILD_DIR = $DEFAULT_BUILD_DIR

if (-not (Test-Path $RP2_DIR)) {
    throw "MicroPython RP2 port not found: $RP2_DIR"
}
if (-not (Test-Path $BOARD_DIR)) {
    throw "Board definition not found: $BOARD_DIR"
}
if (-not (Test-Path $USER_C_MODULES)) {
    throw "User C modules definition not found: $USER_C_MODULES"
}

$PICO_HOME = Join-Path $env:USERPROFILE '.pico-sdk'
$TOOLCHAIN_DIR = Resolve-SingleVersionPath -BaseDir (Join-Path $PICO_HOME 'toolchain') -PreferredName '14_2_Rel1'
$CMAKE_DIR = Resolve-SingleVersionPath -BaseDir (Join-Path $PICO_HOME 'cmake') -PreferredName 'v3.31.5'
$NINJA_DIR = Resolve-SingleVersionPath -BaseDir (Join-Path $PICO_HOME 'ninja') -PreferredName 'v1.12.1'
$PYTHON_DIR = Resolve-SingleVersionPath -BaseDir (Join-Path $PICO_HOME 'python') -PreferredName '3.13.7'
$USER_SDK_DIR = Resolve-SingleVersionPath -BaseDir (Join-Path $PICO_HOME 'sdk') -PreferredName '2.2.0'
$PICO_TOOLS_DIR = Resolve-SingleVersionPath -BaseDir (Join-Path $PICO_HOME 'tools') -PreferredName '2.2.0'
$PICOTOOL_DIR = Resolve-SingleVersionPath -BaseDir (Join-Path $PICO_HOME 'picotool') -PreferredName '2.2.0-a4'
$MSBUILD_EXE = 'C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe'

$CMAKE_EXE = Join-Path $CMAKE_DIR 'bin\cmake.exe'
$NINJA_EXE = Join-Path $NINJA_DIR 'ninja.exe'
$PYTHON_EXE = Join-Path $PYTHON_DIR 'python.exe'
$GCC_EXE = Join-Path $TOOLCHAIN_DIR 'bin\arm-none-eabi-gcc.exe'
$PIOASM_EXE = Join-Path $PICO_TOOLS_DIR 'pioasm\pioasm.exe'
$PICOTOOL_EXE = Join-Path $PICOTOOL_DIR 'picotool\picotool.exe'

foreach ($tool in @($CMAKE_EXE, $NINJA_EXE, $PYTHON_EXE, $GCC_EXE, $PIOASM_EXE, $PICOTOOL_EXE, $CMAKE_HELPER, $WINDOWS_TOOLS_DIR, $MPY_CROSS_VCXPROJ, $MSBUILD_EXE)) {
    if (-not (Test-Path $tool)) {
        throw "Required tool not found: $tool"
    }
}

$SDK_DIR = $USER_SDK_DIR
if ($UseRepoPicoSdk) {
    $SDK_DIR = $LOCAL_SDK_DIR
}
if (-not (Test-Path (Join-Path $SDK_DIR 'pico_sdk_init.cmake'))) {
    throw "Pico SDK not found or incomplete: $SDK_DIR"
}

$env:PICO_TOOLCHAIN_PATH = $TOOLCHAIN_DIR
$env:PICO_SDK_PATH = $SDK_DIR
$env:Path = @(
    (Join-Path $TOOLCHAIN_DIR 'bin')
    (Join-Path $CMAKE_DIR 'bin')
    $NINJA_DIR
    $WINDOWS_TOOLS_DIR
    $env:Path
) -join ';'

$substDrive = $null
$mpSubstDrive = $null
if (-not (Test-Path $MPY_CROSS_EXE)) {
    Write-Host "Building host mpy-cross with MSBuild..."
    $mpSubstDrive = New-TemporarySubstDrive -RootPath $MP_GIT_DIR
    try {
        Invoke-External -FilePath $MSBUILD_EXE -Arguments @(
            (Join-Path $mpSubstDrive 'mpy-cross\mpy-cross.vcxproj')
            '/t:Build'
            '/p:Configuration=Release'
            '/p:Platform=x64'
            "/p:PyBaseDir=$mpSubstDrive\"
            '/m'
        )
    } finally {
        Remove-TemporarySubstDrive -Drive $mpSubstDrive
    }
}
if (-not (Test-Path $MPY_CROSS_EXE)) {
    throw "mpy-cross.exe was not produced at $MPY_CROSS_EXE"
}
$env:MICROPY_MPYCROSS = $MPY_CROSS_EXE

$substDrive = New-TemporarySubstDrive -RootPath $SCRIPT_DIR
$RP2_DIR_SHORT = Join-Path $substDrive 'micropython\ports\rp2'
$MP_GIT_DIR_SHORT = Join-Path $substDrive 'micropython'
$BOARD_DIR_SHORT = Join-Path $RP2_DIR_SHORT "boards\$Board"
$USER_C_MODULES_SHORT = Join-Path $substDrive 'native_modules\micropython.cmake'
$CMAKE_HELPER_SHORT = Join-Path $substDrive 'cmake\prebuilt-pico-tools.cmake'
$MPY_CROSS_EXE_SHORT = Join-Path $substDrive 'micropython\mpy-cross\build\mpy-cross.exe'

$MP_DESCRIBE_RAW = Get-GitDescribe $MP_GIT_DIR
$MP_COMMIT_RAW = Get-GitCommit $MP_GIT_DIR
$MP_STABLE_BASE_RAW = Get-GitStableBase $MP_GIT_DIR

Write-Host "Building MicroPython firmware for $Board..."
Write-Host "  RP2 dir: $RP2_DIR"
Write-Host "  Board dir: $BOARD_DIR"
Write-Host "  Build dir: $BUILD_DIR"
Write-Host "  Pico SDK: $SDK_DIR"
Write-Host "  Toolchain: $TOOLCHAIN_DIR"
Write-Host "  CMake: $CMAKE_EXE"
Write-Host "  Ninja: $NINJA_EXE"
Write-Host "  Python: $PYTHON_EXE"
Write-Host "  Pioasm: $PIOASM_EXE"
Write-Host "  Picotool: $PICOTOOL_EXE"
Write-Host "  Mpy-cross: $MPY_CROSS_EXE"
Write-Host "  Source drive: $substDrive"
Write-Host "  Build jobs: $Jobs"
Write-Host "  MicroPython describe: $MP_DESCRIBE_RAW"
Write-Host "  MicroPython commit: $MP_COMMIT_RAW"
Write-Host "  MicroPython stable base: $MP_STABLE_BASE_RAW"

if (-not $NoClean -and (Test-Path $DEFAULT_BUILD_DIR)) {
    if (-not (Remove-DirectoryWithRetry -Path $DEFAULT_BUILD_DIR)) {
        $BUILD_DIR = Join-Path $BuildRoot ("{0}-{1}" -f $Board, (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Write-Host "Existing build dir is locked; using a fresh build dir instead: $BUILD_DIR"
    }
}

if (-not (Test-Path $FIRMWARE_DIR)) {
    New-Item -ItemType Directory -Path $FIRMWARE_DIR | Out-Null
}
if (-not (Test-Path $BuildRoot)) {
    New-Item -ItemType Directory -Path $BuildRoot | Out-Null
}

$configureArgs = @(
    '-S', '.'
    '-B', $BUILD_DIR
    '-G', 'Ninja'
    '-DPICO_BUILD_DOCS=0'
    "-DPICO_SDK_PATH_OVERRIDE=$SDK_DIR"
    "-DMICROPY_BOARD=$Board"
    "-DMICROPY_BOARD_DIR=$BOARD_DIR_SHORT"
    "-DUSER_C_MODULES=$USER_C_MODULES_SHORT"
    "-DPython3_EXECUTABLE=$PYTHON_EXE"
    "-DCMAKE_MAKE_PROGRAM=$NINJA_EXE"
    "-DCMAKE_C_COMPILER=$GCC_EXE"
    "-DCMAKE_CXX_COMPILER=$(Join-Path $TOOLCHAIN_DIR 'bin\arm-none-eabi-g++.exe')"
    "-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=$CMAKE_HELPER_SHORT"
    "-DPICO_PREBUILT_PIOASM=$PIOASM_EXE"
    "-DPICO_PREBUILT_PICOTOOL=$PICOTOOL_EXE"
    "-DMICROPY_DIR=$MP_GIT_DIR_SHORT"
)

Push-Location $RP2_DIR_SHORT
try {
    Invoke-External -FilePath $CMAKE_EXE -Arguments $configureArgs
    Invoke-External -FilePath $CMAKE_EXE -Arguments @('--build', $BUILD_DIR, '--parallel', "$Jobs")
} finally {
    Pop-Location
    Remove-TemporarySubstDrive -Drive $substDrive
}

$uf2 = Join-Path $BUILD_DIR 'firmware.uf2'
$bin = Join-Path $BUILD_DIR 'firmware.bin'
$hex = Join-Path $BUILD_DIR 'firmware.hex'

if (Test-Path $uf2) { Copy-Item $uf2 (Join-Path $FIRMWARE_DIR "firmware-$Board.uf2") -Force }
if (Test-Path $bin) { Copy-Item $bin (Join-Path $FIRMWARE_DIR "firmware-$Board.bin") -Force }
if (Test-Path $hex) { Copy-Item $hex (Join-Path $FIRMWARE_DIR "firmware-$Board.hex") -Force }

if (-not (Test-Path $uf2)) {
    throw "Build finished without producing firmware.uf2 in $BUILD_DIR"
}

Write-Host "Firmware build complete."
Write-Host "  UF2: $(Join-Path $FIRMWARE_DIR "firmware-$Board.uf2")"
Write-Host "  BIN: $(Join-Path $FIRMWARE_DIR "firmware-$Board.bin")"
Write-Host "  HEX: $(Join-Path $FIRMWARE_DIR "firmware-$Board.hex")"
Write-Host ""
Write-Host "To flash: Hold BOOTSEL and plug in Pico 2 W, then copy the UF2 file to the USB drive."
Write-Host ""
Write-Host "REPL banner will show device name plus a build timestamp from CMake"
