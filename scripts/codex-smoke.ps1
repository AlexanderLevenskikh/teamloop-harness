[CmdletBinding()]
param(
    [string]$ProjectRoot = ".",
    [string]$Role = "",
    [int]$Timeout = 240,
    [switch]$Json,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$RemainingArgs
)
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$argsList = @("$scriptDir/codex_support.py", "--project-root", $ProjectRoot, "--live-smoke", "--timeout", "$Timeout")
if ($Role) { $argsList += @("--smoke-role", $Role) }
if ($Json) { $argsList += "--json" }
if ($RemainingArgs) { $argsList += $RemainingArgs }
& python @argsList
exit $LASTEXITCODE
