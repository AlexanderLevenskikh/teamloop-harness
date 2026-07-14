param(
    [Alias("w")][string]$Workspace = ".teamloop",
    [string]$Action,
    [Alias("tid")][string]$TaskId,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)

$passThru = @()
if ($Workspace -ne ".teamloop") { $passThru += "--workspace"; $passThru += $Workspace }
if ($Action) { $passThru += "--action"; $passThru += $Action }
if ($TaskId) { $passThru += "--task-id"; $passThru += $TaskId }
$passThru += $Args

python "$PSScriptRoot/teamloop-core.py" apply-transition @passThru
