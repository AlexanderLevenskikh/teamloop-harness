param(
    [Alias("r")][string]$Root = "",
    [switch]$Json,
    [switch]$RequireShells,
    [switch]$RequireExecutable,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)

$passThru = @()
if (-not [string]::IsNullOrWhiteSpace($Root)) { $passThru += "--root"; $passThru += $Root }
if ($Json) { $passThru += "--json" }
if ($RequireShells) { $passThru += "--require-shells" }
if ($RequireExecutable) { $passThru += "--require-executable" }
$passThru += $Args

python "$PSScriptRoot/validate_scripts.py" @passThru
exit $LASTEXITCODE
