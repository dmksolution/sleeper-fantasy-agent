<#
.SYNOPSIS
    Register the fantasy agent's recurring jobs as Windows Scheduled Tasks.

.DESCRIPTION
    scripts/crontab only runs inside the Linux container. On Windows nothing
    was scheduled, which meant the Tuesday waiver brief and the Sunday lineup
    check never fired. A correct recommendation you never read is worth
    nothing, so this is the script that actually makes the tool useful.

    Mirrors the container schedule, in local time:

      every 6 hours   sync            keep the cache warm
      Tue 07:00       digest --notify the brief that matters, before claims
      Wed 07:00       digest --notify did the claims land, what changed
      Sun 11:00       digest --notify 90 min before the 1pm ET lock
      Sun 12:00,13:00 startsit        late scratches, pushes only if it matters
      Mon 08:00       sync --full     post-game reset

    Tasks are registered under the \SleeperFantasyAgent\ folder so they are
    easy to find in Task Scheduler and easy to remove in one call.

.PARAMETER Unregister
    Remove every task this script creates, then exit.

.PARAMETER NoNotify
    Register the digest jobs without --notify (log to file only).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\schedule_tasks.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\schedule_tasks.ps1 -Unregister
#>

[CmdletBinding()]
param(
    [switch]$Unregister,
    [switch]$NoNotify
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TaskPath    = '\SleeperFantasyAgent\'
$LogDir      = Join-Path $ProjectRoot 'logs'

# Mapped network drives are per-logon-session and do NOT exist in the session a
# Scheduled Task runs in, so a task pointed at S:\... fails instantly with
# result 1 and no output. Resolve to the UNC path and let cmd's `pushd`
# temporarily map a drive letter, which also gives cmd a working directory it
# will accept (it refuses a bare UNC path as the current directory).
$UseUnc  = $false
$RunRoot = $ProjectRoot
if ($ProjectRoot -match '^[A-Za-z]:') {
    $drive = Get-PSDrive -Name $ProjectRoot.Substring(0, 1) -ErrorAction SilentlyContinue
    if ($drive -and $drive.DisplayRoot -like '\\*') {
        $RunRoot = $drive.DisplayRoot + $ProjectRoot.Substring(2)
        $UseUnc  = $true
    }
}

# Prefer the project virtualenv; fall back to whatever python is on PATH.
# pythonw.exe keeps a console window from flashing on every run.
$Python = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Python)) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path $Python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { throw "No Python found. Create .venv or put python on PATH." }
    $Python = $cmd.Source
} else {
    $Python = $RunRoot + $Python.Substring($ProjectRoot.Length)
}

$Jobs = @(
    @{ Name = 'Sync';           Args = 'sync';               Trigger = 'Every6Hours' }
    @{ Name = 'DigestTuesday';  Args = 'digest --notify';    Trigger = 'Weekly'; Day = 'Tuesday';   At = '07:00' }
    @{ Name = 'DigestWednesday';Args = 'digest --notify';    Trigger = 'Weekly'; Day = 'Wednesday'; At = '07:00' }
    @{ Name = 'DigestSunday';   Args = 'digest --notify';    Trigger = 'Weekly'; Day = 'Sunday';    At = '11:00' }
    @{ Name = 'StartSitNoon';   Args = 'startsit --notify';  Trigger = 'Weekly'; Day = 'Sunday';    At = '12:00' }
    @{ Name = 'StartSitOne';    Args = 'startsit --notify';  Trigger = 'Weekly'; Day = 'Sunday';    At = '13:00' }
    @{ Name = 'SyncMondayFull'; Args = 'sync --full';        Trigger = 'Weekly'; Day = 'Monday';    At = '08:00' }
)

function Remove-AgentTasks {
    $existing = Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Host "No existing tasks under $TaskPath"
        return
    }
    foreach ($t in $existing) {
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $TaskPath -Confirm:$false
        Write-Host "removed $($t.TaskName)"
    }
}

if ($Unregister) {
    Remove-AgentTasks
    Write-Host "`nDone. All scheduled tasks removed."
    return
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# Start clean so re-running this script is idempotent rather than additive.
Remove-AgentTasks

Write-Host "`nProject : $ProjectRoot"
if ($UseUnc) { Write-Host "Run as  : $RunRoot  (mapped drive resolved to UNC for the scheduler)" }
Write-Host "Python  : $Python"
Write-Host "Logs    : $LogDir`n"

foreach ($job in $Jobs) {
    $cliArgs = $job.Args
    if ($NoNotify) { $cliArgs = $cliArgs -replace '\s*--notify', '' }

    $log = "$RunRoot\logs\$($job.Name).log"

    # cmd.exe wraps the call so stdout and stderr can be appended to a log.
    # Scheduled tasks have no console, so without this the output is discarded.
    # pushd/popd both establishes a working directory and, on a UNC path, maps
    # a temporary drive letter for the duration of the run.
    $inner  = "pushd `"$RunRoot`" && `"$Python`" cli.py $cliArgs >> `"$log`" 2>&1 & popd"
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $inner"

    if ($job.Trigger -eq 'Every6Hours') {
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
            -RepetitionInterval (New-TimeSpan -Hours 6)
    } else {
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $job.Day -At $job.At
    }

    # Run whether or not the user is logged on would need stored credentials,
    # so these run in the interactive session and fire at next logon if the
    # machine was asleep.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
    $settingsObj = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $job.Name -TaskPath $TaskPath `
        -Action $action -Trigger $trigger -Principal $principal -Settings $settingsObj | Out-Null

    $when = if ($job.Trigger -eq 'Every6Hours') { 'every 6 hours' } else { "$($job.Day) $($job.At)" }
    Write-Host ("  {0,-16} {1,-22} {2}" -f $job.Name, $when, "cli.py $cliArgs")
}

Write-Host "`nRegistered $($Jobs.Count) tasks under $TaskPath"
Write-Host "Verify : Get-ScheduledTask -TaskPath '$TaskPath'"
Write-Host "Test   : Start-ScheduledTask -TaskPath '$TaskPath' -TaskName 'DigestTuesday'"
Write-Host "Remove : .\scripts\schedule_tasks.ps1 -Unregister"
