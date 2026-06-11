# Runs one pull+write cycle and appends timestamped output to logs/run.log.
# Invoked by the "EngagementTracker" scheduled task (every 4 minutes).
param(
    [switch]$Drafts
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot           # ...\engagement-tracker
$py   = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "run.log"
$maxBytes = 2MB
if ($env:TRACKING_RUN_LOG_MAX_BYTES) {
    $maxBytes = [int64]$env:TRACKING_RUN_LOG_MAX_BYTES
}
if ((Test-Path $log) -and ((Get-Item $log).Length -gt $maxBytes)) {
    $archive = Join-Path $logDir "run.log.1"
    if (Test-Path $archive) { Remove-Item -LiteralPath $archive -Force }
    Move-Item -LiteralPath $log -Destination $archive
}

Set-Location $root
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$mode = if ($Drafts) { "cli run --drafts" } else { "cli run" }
"`n===== $stamp  $mode =====" | Add-Content $log
try {
    $cliArgs = @("-m", "tracking.cli", "run")
    if ($Drafts) { $cliArgs += "--drafts" }
    & $py @cliArgs *>&1 | Add-Content $log
} catch {
    "ERROR: $_" | Add-Content $log
}
