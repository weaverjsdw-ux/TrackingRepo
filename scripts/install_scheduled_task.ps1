# Registers the local unattended runner as a hidden Windows scheduled task.
# Core business logic stays in `python -m tracking.cli run`; this only wraps it.
param(
    [string]$TaskName = "EngagementTracker",
    [int]$IntervalMinutes = 4,
    [switch]$Drafts
)

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot
$runner = Join-Path $scriptDir "run_scheduled_hidden.vbs"
if (-not (Test-Path $runner)) {
    throw "Runner not found: $runner"
}

$argument = "`"$runner`""
if ($Drafts) {
    $argument = "$argument -Drafts"
}

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Runs engagement tracking automation every $IntervalMinutes minutes." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' every $IntervalMinutes minutes."
Write-Host "Action: wscript.exe $argument"
