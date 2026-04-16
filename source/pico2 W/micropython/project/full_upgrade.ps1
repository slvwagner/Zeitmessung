[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetScript = Join-Path $scriptDir "full_update.ps1"

if (-not (Test-Path $targetScript)) {
    throw "Target script not found: $targetScript"
}

& $targetScript @RemainingArgs
exit $LASTEXITCODE
