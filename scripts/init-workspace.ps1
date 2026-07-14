param(
    [Alias("w")][string]$Workspace = ".teamloop",
    [Alias("p")][string]$Profile,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)

$passThru = @()
if ($Workspace -ne ".teamloop") { $passThru += "--workspace"; $passThru += $Workspace }
if ($Profile) { $passThru += "--profile"; $passThru += $Profile }
$passThru += $Args

python "$PSScriptRoot/teamloop-core.py" init-workspace @passThru
