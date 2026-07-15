[CmdletBinding()]
param(
    [string]$ProjectRoot = ".",
    [switch]$NoCli,
    [ValidateSet("", "inherit", "chatgpt")][string]$FixModels = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$RemainingArgs
)
$ErrorActionPreference = "Stop"
$argsList = @("$PSScriptRoot/teamloop-core.py", "team-codex-doctor", "--project-root", $ProjectRoot)
if ($NoCli) { $argsList += "--no-cli" }
if ($FixModels) { $argsList += @("--fix-models", $FixModels) }
if ($RemainingArgs) { $argsList += $RemainingArgs }
& python @argsList
exit $LASTEXITCODE
