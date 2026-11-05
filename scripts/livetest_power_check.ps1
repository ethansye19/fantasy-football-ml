<#
.SYNOPSIS
  Read-only check of the Windows power settings the live-test tasks depend on (D-107).

.DESCRIPTION
  Prints the settings that decide whether a scheduled task can wake the laptop and run:
  wake timers, sleep and hibernate timeouts, the lid action, the available sleep states
  and Fast Startup. It runs only powercfg /q, powercfg /a and reg query. It changes
  nothing; each line that needs attention starts with CHECK.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\livetest_power_check.ps1
#>
$ErrorActionPreference = "Continue"

function Get-Index([string[]]$lines, [string]$label) {
    $line = $lines | Select-String -SimpleMatch $label | Select-Object -First 1
    if (-not $line) { return $null }
    return [Convert]::ToInt32((($line.ToString() -split ":")[-1]).Trim(), 16)
}

function Query([string]$sub, [string]$setting) {
    $out = & powercfg /q SCHEME_CURRENT $sub $setting 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return $out
}

function Name($map, $idx) {
    if ($null -eq $idx) { return "unknown" }
    if ($map.ContainsKey([int]$idx)) { return $map[[int]$idx] }
    return "value $idx"
}

function Describe-Minutes([int]$seconds) {
    if ($seconds -eq 0) { return "never" }
    if ($seconds -ge 0x7fffffff) { return "not set" }
    return ("{0} min" -f [math]::Round($seconds / 60))
}

Write-Host "Power settings relevant to the live-test tasks (read only; nothing is changed)"
Write-Host ""
& powercfg /getactivescheme
Write-Host ""

$wake = Query SUB_SLEEP RTCWAKE
$wakeNames = @{ 0 = "Disable"; 1 = "Enable"; 2 = "Important wake timers only" }
if ($wake) {
    $ac = Get-Index $wake "Current AC Power Setting Index"
    $dc = Get-Index $wake "Current DC Power Setting Index"
    Write-Host ("Allow wake timers        AC: {0}   DC: {1}" -f (Name $wakeNames $ac), (Name $wakeNames $dc))
    if ($ac -ne 1) { Write-Host "  CHECK: set 'Allow wake timers' to Enable when plugged in (Power Options > Change plan settings > Change advanced power settings > Sleep > Allow wake timers). The run task cannot wake the laptop otherwise." }
    if ($dc -ne 1) { Write-Host "  Note: on battery wake timers are '$(Name $wakeNames $dc)'; the tasks start only on AC power, so keep the laptop plugged in." }
}

$sleep = Query SUB_SLEEP STANDBYIDLE
if ($sleep) {
    Write-Host ("Sleep after              AC: {0}   DC: {1}" -f (Describe-Minutes (Get-Index $sleep "Current AC Power Setting Index")), (Describe-Minutes (Get-Index $sleep "Current DC Power Setting Index")))
    Write-Host "  Fine either way: WakeToRun wakes the laptop from sleep when wake timers are enabled."
}

$hib = Query SUB_SLEEP HIBERNATEIDLE
if ($hib) {
    $hac = Get-Index $hib "Current AC Power Setting Index"
    Write-Host ("Hibernate after          AC: {0}   DC: {1}" -f (Describe-Minutes $hac), (Describe-Minutes (Get-Index $hib "Current DC Power Setting Index")))
    if ($hac -gt 0 -and $hac -lt 0x7fffffff) { Write-Host "  CHECK: hibernation on AC is on. Wake timers do not fire from hibernation; set 'Hibernate after' on AC to Never during the test." }
}

$lid = Query SUB_BUTTONS LIDACTION
$lidNames = @{ 0 = "Do nothing"; 1 = "Sleep"; 2 = "Hibernate"; 3 = "Shut down" }
if ($lid) {
    $lac = Get-Index $lid "Current AC Power Setting Index"
    $ldc = Get-Index $lid "Current DC Power Setting Index"
    Write-Host ("Closing the lid          AC: {0}   DC: {1}" -f (Name $lidNames $lac), (Name $lidNames $ldc))
    if ($null -eq $lac) { Write-Host "  Note: powercfg does not expose the lid setting on this machine; check Settings > System > Power > Lid, cover and power button controls (or Control Panel > Power Options > Choose what closing the lid does). On AC it must be Sleep or Do nothing, not Hibernate or Shut down." }
    if ($null -ne $lac -and $lac -ge 2) { Write-Host "  CHECK: closing the lid on AC hibernates or shuts down; wake timers do not fire then. Set it to Sleep or Do nothing." }
}

Write-Host ""
Write-Host "Sleep states (powercfg /a):"
& powercfg /a | Select-String -SimpleMatch "available on this system", "Standby", "Hibernate", "Fast Startup" | ForEach-Object { "  " + $_.ToString().Trim() }

$fast = $null
try { $fast = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" -Name HiberbootEnabled -ErrorAction Stop).HiberbootEnabled } catch {}
if ($null -ne $fast) {
    Write-Host ("Fast Startup (HiberbootEnabled): {0}" -f $fast)
    if ($fast -eq 1) { Write-Host "  CHECK: with Fast Startup on, 'Shut down' hibernates the kernel and wake timers do NOT fire. On kickoff days use Sleep (or leave the laptop on), never Shut down; or turn Fast Startup off (Control Panel > Power Options > Choose what the power buttons do > Change settings that are currently unavailable)." }
}

Write-Host ""
Write-Host "Optional, from an elevated (administrator) prompt, read only:"
Write-Host "  powercfg /waketimers     # the wake timers currently armed (the registered task should appear the day before kickoff)"
Write-Host "  powercfg /lastwake       # what woke the machine last"
Write-Host "  powercfg /requests       # what is keeping it awake"
Write-Host ""
Write-Host "Nothing was changed."
