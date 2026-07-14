param(
    [Parameter(Position=0, Mandatory=$true)][ValidateSet("propose","negotiate","accept","materialize")][string]$Action,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)
python "$PSScriptRoot/teamloop-core.py" "team-$Action" @Args
