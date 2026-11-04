<#
.SYNOPSIS
  Windows Task Scheduler tasks for the chain_v2 live test (dry run unless -Register).

.DESCRIPTION
  Reads the week plan from the schedule (scripts/livetest_week.py plan --json,
  which uses dim_game kickoff_utc and the live nflverse schedule) and, for each
  upcoming test week, defines:

    ffml\livetest-<S>w<WW>-run       at first kickoff - 9 h, repeated every 30 min
                                     until kickoff - 30 min; ends at kickoff
    ffml\livetest-<S>w<WW>-watchdog  at first kickoff - 1 h; alerts if nothing is pushed
    ffml\livetest-daily-check        daily 15:00 local: stale predictions, schedule
                                     drift, MISSED markers for weeks that kicked off

  Weeks whose kickoff times are still tentative (all games at one time, as week 18
  is until the NFL sets it) are scheduled 24 hours early. The daily check alerts
  when kickoff times change after registration; then re-run this script with
  -Register (it replaces the tasks).

  Without -Register nothing is registered: the script prints what it would do.

.PARAMETER Weeks
  Test window override for the plan (e.g. "10-18"); default: data\livetest\config.json
  written by `livetest_week.py init`.

.PARAMETER LogonMode
  Interactive (default): runs only while the user is logged on (locked screen is
  fine). Git and gh credentials from Windows Credential Manager work.
  Password: runs whether or not the user is logged on; asks for the Windows
  password once (Get-Credential) and stores it with the task. Credentials work.
  S4U: runs whether or not the user is logged on without a stored password, but
  the task has no access to Credential Manager, so git push and gh fail. Not
  recommended.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1
  powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Register -LogonMode Password
  powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Python = "",
    [ValidateSet("Interactive", "Password", "S4U")]
    [string]$LogonMode = "Interactive",
    [string]$TaskFolder = "\ffml\",
    [string]$DailyCheckTime = "15:00",
    [string]$PlanJson = "",
    [string]$Weeks = "",
    [int]$Season = 0,
    [switch]$Register,
    [switch]$Unregister,
    [switch]$NoWake
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
if (-not $Python) { $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe" }
$Runner = Join-Path $RepoRoot "scripts\livetest_week.py"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

function Get-Plan {
    if ($PlanJson) { return (Get-Content -Raw -Path $PlanJson | ConvertFrom-Json) }
    $env:PYTHONUTF8 = "1"
    $planArgs = @("plan", "--json")
    if ($Weeks) { $planArgs += @("--weeks", $Weeks) }
    if ($Season -gt 0) { $planArgs += @("--season", "$Season") }
    $out = & $Python $Runner @planArgs
    if ($LASTEXITCODE -ne 0) { throw "livetest_week.py plan failed (exit $LASTEXITCODE)" }
    return (($out -join "`n") | ConvertFrom-Json)
}

function To-Local([string]$iso) {
    return [DateTime]::Parse($iso, [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind).ToLocalTime()
}

function New-TaskSpec([string]$Name, [string]$Arguments, $Trigger, [string]$When) {
    return [pscustomobject]@{ Name = $Name; Arguments = $Arguments; Trigger = $Trigger; When = $When }
}

$rows = @(Get-Plan)
if ($rows.Count -eq 0) { Write-Warning "the plan is empty: run 'livetest_week.py init' first, or check dim_game" }
$now = Get-Date
$specs = @()
foreach ($r in $rows) {
    $kickoff = To-Local $r.first_kickoff_utc
    $start = To-Local $r.run_start
    $cutoff = To-Local $r.cutoff
    $watch = To-Local $r.watchdog
    if ($cutoff -le $now) { continue }
    $runArgs = "`"$Runner`" run --season $($r.season) --week $($r.week)"
    $trig = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration ($cutoff - $start)
    $trig.EndBoundary = $kickoff.ToString("s")
    $tent = ""
    if ($r.tentative) { $tent = " (kickoff times tentative: scheduled 24 h early)" }
    $specs += New-TaskSpec "livetest-$($r.key)-run" $runArgs $trig `
        ("{0:yyyy-MM-dd HH:mm} local ({1}) every 30 min until {2:yyyy-MM-dd HH:mm}; first kickoff {3:yyyy-MM-dd HH:mm} local ({4}){5}" -f `
            $start, $r.run_start, $cutoff, $kickoff, $r.first_kickoff_utc, $tent)
    if ($watch -gt $now) {
        $wtrig = New-ScheduledTaskTrigger -Once -At $watch
        $wtrig.EndBoundary = $kickoff.ToString("s")
        $specs += New-TaskSpec "livetest-$($r.key)-watchdog" "`"$Runner`" watchdog --season $($r.season) --week $($r.week)" $wtrig `
            ("{0:yyyy-MM-dd HH:mm} local ({1})" -f $watch, $r.watchdog)
    }
}
$daily = New-ScheduledTaskTrigger -Daily -At $DailyCheckTime
$specs += New-TaskSpec "livetest-daily-check" "`"$Runner`" check" $daily "daily at $DailyCheckTime local"

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10) -WakeToRun:(-not $NoWake)

Write-Host "Repository : $RepoRoot"
Write-Host "Python     : $Python"
Write-Host "Logon mode : $LogonMode (user $UserId)"
Write-Host "Settings   : StartWhenAvailable, WakeToRun=$(-not $NoWake), AllowStartIfOnBatteries, IgnoreNew, limit 5 h, 2 restarts/10 min"
Write-Host "Folder     : $TaskFolder"
Write-Host ""

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskPath $TaskFolder -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like "livetest-*" }
    foreach ($t in $existing) {
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $TaskFolder -Confirm:$false
        Write-Host "unregistered $($t.TaskPath)$($t.TaskName)"
    }
    exit 0
}

foreach ($s in $specs) {
    Write-Host ("{0}{1}" -f $TaskFolder, $s.Name)
    Write-Host ("    when   : {0}" -f $s.When)
    Write-Host ("    action : `"{0}`" {1}" -f $Python, $s.Arguments)
    Write-Host ("    cwd    : {0}" -f $RepoRoot)
}

if (-not $Register) {
    Write-Host ""
    Write-Host "DRY RUN: nothing was registered. Re-run with -Register after the owner signs off on the test design."
    exit 0
}

$credential = $null
if ($LogonMode -eq "Password") {
    $credential = Get-Credential -UserName $UserId -Message "Windows password for the live-test tasks (stored by Task Scheduler)"
}
$principal = $null
if ($LogonMode -eq "Interactive") {
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
} elseif ($LogonMode -eq "S4U") {
    Write-Warning "S4U tasks cannot read Windows Credential Manager: git push and gh will fail."
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType S4U -RunLevel Limited
}

$old = Get-ScheduledTask -TaskPath $TaskFolder -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like "livetest-*" }
foreach ($t in $old) { Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $TaskFolder -Confirm:$false }

foreach ($s in $specs) {
    $action = New-ScheduledTaskAction -Execute $Python -Argument $s.Arguments -WorkingDirectory $RepoRoot
    if ($credential) {
        Register-ScheduledTask -TaskName $s.Name -TaskPath $TaskFolder -Action $action -Trigger $s.Trigger `
            -Settings $settings -User $credential.UserName -Password $credential.GetNetworkCredential().Password `
            -RunLevel Limited -Description "ffml chain_v2 live test (D-098). See docs/RUNBOOK.md." | Out-Null
    } else {
        Register-ScheduledTask -TaskName $s.Name -TaskPath $TaskFolder -Action $action -Trigger $s.Trigger `
            -Settings $settings -Principal $principal `
            -Description "ffml chain_v2 live test (D-098). See docs/RUNBOOK.md." | Out-Null
    }
    Write-Host "registered $TaskFolder$($s.Name)"
}
$env:PYTHONUTF8 = "1"
& $Python $Runner plan --save | Out-Null
Write-Host "saved the registered plan to data\livetest\schedule_plan.json (used by the daily drift check)"
