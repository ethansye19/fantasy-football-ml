<#
.SYNOPSIS
  Windows Task Scheduler tasks for the chain_v2 live test (dry run unless -Register). D-106, D-107.

.DESCRIPTION
  Reads the week plan (scripts/livetest_week.py plan --json: dim_game kickoff_utc, cross-checked
  with the live nflverse schedule) and, for each upcoming test week, defines:

    \ffml\livetest-<S>w<WW>-run       first kickoff - 24 h 15 min, repeated every 30 min until
                                       kickoff - 5 min (the runner itself stops building at
                                       kickoff - 30 min and pushing at kickoff - 5 min)
    \ffml\livetest-<S>w<WW>-watchdog  first kickoff - 1 h: alert if nothing is pushed
    \ffml\livetest-daily-check        daily: stale predictions, schedule drift, MISSED markers
    \ffml\livetest-weekly-score       Fridays from 13:00 UTC (D-116, A3): the week's data cutoff, a
                                       refresh at or after it, the Evaluator's scorer for the oldest
                                       unsealed committed week, then the sealed hashes pushed to the
                                       branch; repeats every 2 h for 12 h

  Weeks whose kickoff times are still tentative (every game at one time, as week 18 is until
  the NFL sets it) are scheduled 24 hours earlier again. The daily check alerts when kickoff
  times change after registration; then re-run this script with -Register (it replaces the
  weekly tasks).

  With -DryRunBranch it defines the two logged-off dry-run tasks instead (docs/RUNBOOK.md
  section 13): \ffml\livetest-dryrun-run (at -DryRunAt, every 30 min for 3 h) and
  \ffml\livetest-dryrun-watchdog (75 min after -DryRunAt). They push to the dry-run branch
  only, never to livetest-2026.

  Least privilege (D-107): the tasks run as the current user with -RunLevel Limited (no
  administrator rights), start only this repository's runner, and start only on AC power.
  -LogonMode Password asks for the Windows password through Get-Credential at registration;
  the password is handed to Task Scheduler, which keeps it in the system credential store,
  and is written to no file, script or log. StartWhenAvailable catches up after a missed
  start, WakeToRun wakes the laptop (unless -NoWake), no overlapping runs, 5-hour limit.

  Without -Register nothing is registered: the script prints what it would do.

.PARAMETER LogonMode
  Password (default): runs whether or not the user is logged on; the password is prompted.
  Interactive: runs only while the user is logged on (a locked screen is fine); no password.
  S4U: no stored password, but the task cannot read Windows Credential Manager, so git push
  and gh fail; not usable for the live test.

.PARAMETER Weeks
  Test window override for the plan (e.g. "11-18"); default: data\livetest\config.json
  written by `livetest_week.py init`.

.PARAMETER DryRunBranch
  Define the logged-off dry-run tasks for this branch (must not be livetest-2026).

.PARAMETER ForceFail
  Dry run only: refresh, build or push. The runner fails that step on purpose and alerts.

.PARAMETER DryRunStubScore
  Dry run only: use scripts/livetest_stub_score.py for the pre-build check (until the scorer CLI is on main).

.PARAMETER DryRunAt
  Dry run only: when the first attempt starts (default: 10 minutes from now).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1
  powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Register
  powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -DryRunBranch livetest-dryrun -ForceFail push -Register
  powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Python = "",
    [ValidateSet("Interactive", "Password", "S4U")]
    [string]$LogonMode = "Password",
    [string]$TaskFolder = "\ffml\",
    [string]$DailyCheckTime = "15:00",
    [string]$ScoreTimeUtc = "13:00",
    [string]$PlanJson = "",
    [string]$Weeks = "",
    [int]$Season = 0,
    [switch]$Register,
    [switch]$Unregister,
    [switch]$NoWake,
    [string]$DryRunBranch = "",
    [ValidateSet("", "refresh", "prebuild", "build", "push")]
    [string]$ForceFail = "",
    [datetime]$DryRunAt = [datetime]::MinValue,
    [int]$DryRunWeek = 0,
    [switch]$DryRunStub,
    [switch]$DryRunStubScore,
    [switch]$DryRunSkipRefresh
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
if (-not $Python) { $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe" }
$Runner = Join-Path $RepoRoot "scripts\livetest_week.py"
$UserId = "$env:USERDOMAIN\$env:USERNAME"
$DryRun = [bool]$DryRunBranch

if ($DryRun -and ($DryRunBranch -eq "livetest-2026" -or $DryRunBranch -like "livetest-2026*" -or @("main", "master") -contains $DryRunBranch)) {
    throw "-DryRunBranch must be a separate branch such as livetest-dryrun, never livetest-2026 or main"
}
if ($ForceFail -and -not $DryRun) { throw "-ForceFail is only for -DryRunBranch" }

function Get-WindowArgs {
    $extra = @()
    if ($Weeks) { $extra += @("--weeks", $Weeks) }
    if ($Season -gt 0) { $extra += @("--season", "$Season") }
    return $extra
}

function Get-Plan {
    if ($PlanJson) { return (Get-Content -Raw -Path $PlanJson | ConvertFrom-Json) }
    $env:PYTHONUTF8 = "1"
    $planArgs = @("plan", "--json") + (Get-WindowArgs)
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

if ($DryRun) {
    if ($DryRunWeek -gt 0) { $wk = $rows | Where-Object { $_.week -eq $DryRunWeek } | Select-Object -First 1 }
    else { $wk = $rows | Where-Object { $_.status -eq "upcoming" } | Select-Object -First 1 }
    if (-not $wk) { throw "no week for the dry run (use -DryRunWeek)" }
    if ($DryRunAt -eq [datetime]::MinValue) { $DryRunAt = $now.AddMinutes(10) }
    $dryArgs = "`"$Runner`" run --dry-run-branch $DryRunBranch --season $($wk.season) --week $($wk.week)"
    if ($DryRunStub) { $dryArgs += " --stub-build" }
    if ($DryRunStubScore) { $dryArgs += " --stub-score" }
    if ($DryRunSkipRefresh) { $dryArgs += " --skip-refresh" }
    if ($ForceFail) { $dryArgs += " --force-fail $ForceFail" }
    $forced = "none"
    if ($ForceFail) { $forced = $ForceFail }
    $trig = New-ScheduledTaskTrigger -Once -At $DryRunAt -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Hours 3)
    $specs += New-TaskSpec "livetest-dryrun-run" $dryArgs $trig `
        ("{0:yyyy-MM-dd HH:mm} local, every 30 min for 3 h; week {1}; branch {2}; forced failure: {3}" -f $DryRunAt, $wk.key, $DryRunBranch, $forced)
    $watchAt = $DryRunAt.AddMinutes(75)
    $wtrig = New-ScheduledTaskTrigger -Once -At $watchAt
    $specs += New-TaskSpec "livetest-dryrun-watchdog" "`"$Runner`" watchdog --dry-run-branch $DryRunBranch --season $($wk.season) --week $($wk.week)" $wtrig `
        ("{0:yyyy-MM-dd HH:mm} local" -f $watchAt)
} else {
    foreach ($r in $rows) {
        $kickoff = To-Local $r.first_kickoff_utc
        $start = To-Local $r.run_start
        $pushDeadline = To-Local $r.push_deadline
        $watch = To-Local $r.watchdog
        if ($pushDeadline -le $now) { continue }
        $runArgs = "`"$Runner`" run --season $($r.season) --week $($r.week)"
        $trig = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 30) `
            -RepetitionDuration ($pushDeadline - $start)
        $trig.EndBoundary = $kickoff.ToString("s")
        $tent = ""
        if ($r.tentative) { $tent = " (kickoff times tentative: scheduled a day earlier)" }
        $specs += New-TaskSpec "livetest-$($r.key)-run" $runArgs $trig `
            ("{0:yyyy-MM-dd HH:mm} local ({1}) every 30 min until {2:yyyy-MM-dd HH:mm}; first kickoff {3:yyyy-MM-dd HH:mm} local ({4}){5}" -f `
                $start, $r.run_start, $pushDeadline, $kickoff, $r.first_kickoff_utc, $tent)
        if ($watch -gt $now) {
            $wtrig = New-ScheduledTaskTrigger -Once -At $watch
            $wtrig.EndBoundary = $kickoff.ToString("s")
            $specs += New-TaskSpec "livetest-$($r.key)-watchdog" "`"$Runner`" watchdog --season $($r.season) --week $($r.week)" $wtrig `
                ("{0:yyyy-MM-dd HH:mm} local ({1})" -f $watch, $r.watchdog)
        }
    }
    $daily = New-ScheduledTaskTrigger -Daily -At $DailyCheckTime
    $specs += New-TaskSpec "livetest-daily-check" "`"$Runner`" check" $daily "daily at $DailyCheckTime local"
    # Fridays at a fixed UTC time: the StartBoundary carries "Z" (synchronize across time zones), so the
    # task stays at 13:00 UTC when daylight saving time changes the local clock.
    $hm = $ScoreTimeUtc.Split(":")
    $utcNow = (Get-Date).ToUniversalTime()
    $firstUtc = [DateTime]::SpecifyKind($utcNow.Date.AddHours([int]$hm[0]).AddMinutes([int]$hm[1]), [DateTimeKind]::Utc)
    while ($firstUtc.DayOfWeek -ne [DayOfWeek]::Friday -or $firstUtc -le $utcNow) { $firstUtc = $firstUtc.AddDays(1) }
    $score = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At $firstUtc.ToLocalTime()
    $score.Repetition = (New-ScheduledTaskTrigger -Once -At $firstUtc.ToLocalTime() -RepetitionInterval (New-TimeSpan -Hours 2) `
        -RepetitionDuration (New-TimeSpan -Hours 12)).Repetition
    $score.StartBoundary = $firstUtc.ToString("yyyy-MM-ddTHH:mm:ss") + "Z"
    $specs += New-TaskSpec "livetest-weekly-score" "`"$Runner`" score-week" $score `
        ("Fridays at {0} UTC ({1:HH:mm} local now), every 2 h for 12 h; first {2:yyyy-MM-dd}" -f $ScoreTimeUtc, $firstUtc.ToLocalTime(), $firstUtc)
}

# AC power only to start (no -AllowStartIfOnBatteries); a run that is already going is not killed on unplugging.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10) -WakeToRun:(-not $NoWake)

Write-Host "Repository    : $RepoRoot"
Write-Host "Python        : $Python"
Write-Host "Runs as       : $UserId, logon mode $LogonMode, run level Limited (no administrator rights)"
Write-Host "Settings      : start only on AC power; StartWhenAvailable (catch up); WakeToRun=$(-not $NoWake); IgnoreNew; limit 5 h; 2 restarts 10 min apart"
Write-Host "Password      : prompted by Get-Credential only with -Register and -LogonMode Password; stored by Task Scheduler, written to no file or log"
Write-Host "Folder        : $TaskFolder"
if ($DryRun) { Write-Host "Mode          : DRY-RUN TASKS for branch $DryRunBranch (never livetest-2026)" }
Write-Host ""

if ($Unregister) {
    $pattern = "livetest-*"
    if ($DryRun) { $pattern = "livetest-dryrun-*" }
    $existing = Get-ScheduledTask -TaskPath $TaskFolder -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like $pattern }
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
    Write-Host "DRY RUN: nothing was registered. Re-run with -Register (the owner runs it; docs/RUNBOOK.md section 14)."
    exit 0
}

$credential = $null
if ($LogonMode -eq "Password") {
    $credential = Get-Credential -UserName $UserId -Message "Windows password for the live-test tasks. It goes to Task Scheduler only; this script writes it nowhere."
    if (-not $credential) { throw "no credential given; nothing registered" }
}
$principal = $null
if ($LogonMode -eq "Interactive") {
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
} elseif ($LogonMode -eq "S4U") {
    Write-Warning "S4U tasks cannot read Windows Credential Manager: git push and gh will fail."
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType S4U -RunLevel Limited
}

$replacePattern = "livetest-*"
if ($DryRun) { $replacePattern = "livetest-dryrun-*" }
$old = Get-ScheduledTask -TaskPath $TaskFolder -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like $replacePattern }
if (-not $DryRun) { $old = $old | Where-Object { $_.TaskName -notlike "livetest-dryrun-*" } }
foreach ($t in $old) { Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $TaskFolder -Confirm:$false }

$description = "ffml chain_v2 live test (D-106, D-107). Runs scripts/livetest_week.py as the owner, no elevation. See docs/RUNBOOK.md."
foreach ($s in $specs) {
    $action = New-ScheduledTaskAction -Execute $Python -Argument $s.Arguments -WorkingDirectory $RepoRoot
    if ($credential) {
        $plain = $credential.GetNetworkCredential().Password
        try {
            Register-ScheduledTask -TaskName $s.Name -TaskPath $TaskFolder -Action $action -Trigger $s.Trigger `
                -Settings $settings -User $credential.UserName -Password $plain -RunLevel Limited `
                -Description $description | Out-Null
        } finally {
            $plain = $null
            Remove-Variable plain -ErrorAction SilentlyContinue
        }
    } else {
        Register-ScheduledTask -TaskName $s.Name -TaskPath $TaskFolder -Action $action -Trigger $s.Trigger `
            -Settings $settings -Principal $principal -Description $description | Out-Null
    }
    Write-Host "registered $TaskFolder$($s.Name)"
}
$credential = $null
if (-not $DryRun) {
    $env:PYTHONUTF8 = "1"
    $saveArgs = @("plan", "--save") + (Get-WindowArgs)
    & $Python $Runner @saveArgs | Out-Null
    Write-Host "saved the registered plan to data\livetest\schedule_plan.json (used by the daily drift check)"
}
