param(
    [Parameter(Position=0, Mandatory=$true)][ValidateSet("propose","negotiate","accept","materialize","codex-doctor","codex-smoke")][string]$Action,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)
python "$PSScriptRoot/teamloop-core.py" "team-$Action" @Args
