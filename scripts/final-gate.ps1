param(
    [Alias("w")][string]$Workspace = ".teamloop",
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)

$passThru = @()
if ($Workspace -ne ".teamloop") { $passThru += "--workspace"; $passThru += $Workspace }
$passThru += $Args

python "$PSScriptRoot/teamloop-core.py" final-gate @passThru
