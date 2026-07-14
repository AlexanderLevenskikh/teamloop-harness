param(
    [Alias("w")][string]$Workspace = ".teamloop",
    [Parameter(Mandatory=$true)][string]$Type,
    [Parameter(Mandatory=$true)][string]$Actor,
    [Parameter(Mandatory=$true)][string]$Summary,
    [string]$RunId,
    [Alias("tid")][string]$TaskId,
    [string]$Data,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)

$passThru = @()
if ($Workspace -ne ".teamloop") { $passThru += "--workspace"; $passThru += $Workspace }
$passThru += "--type"; $passThru += $Type
$passThru += "--actor"; $passThru += $Actor
$passThru += "--summary"; $passThru += $Summary
if ($RunId) { $passThru += "--run-id"; $passThru += $RunId }
if ($TaskId) { $passThru += "--task-id"; $passThru += $TaskId }
if ($Data) { $passThru += "--data"; $passThru += $Data }
$passThru += $Args

python "$PSScriptRoot/teamloop-core.py" write-event @passThru
