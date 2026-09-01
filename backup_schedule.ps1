# Run backup.py every day at 07:00. The time is not load-bearing - updates run
# every ten minutes and the round-ups are written in the first slot after
# midnight, so by 07:00 the previous day is long settled.
#
#   powershell -ExecutionPolicy Bypass -File backup_schedule.ps1
#   powershell -ExecutionPolicy Bypass -File backup_schedule.ps1 -Remove
#
# It needs no elevation: the task runs as you, when you are logged in, which
# is also when flyctl has your credentials.

param([switch]$Remove, [string]$Time = "07:00")

$name = "WOM Tracker backup"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    "Removed '$name' (if it existed)."
    return
}

$python = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "py launcher not found" }

$action  = New-ScheduledTaskAction -Execute $python `
    -Argument "backup.py" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Missed runs matter more than punctual ones: a laptop that was asleep at 07:00
# should still take the copy when it wakes.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Settings $settings -Description "Pulls a verified copy of the hosted database into .\backups" `
    -Force | Out-Null

"Scheduled '$name' daily at $Time."
"Run it now with:  Start-ScheduledTask -TaskName '$name'"
