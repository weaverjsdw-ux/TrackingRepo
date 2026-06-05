# Runs one pull+write cycle and appends timestamped output to logs/run.log.
# Invoked by the "EngagementTracker" scheduled task (every 4 minutes).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot           # ...\engagement-tracker
$py   = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "run.log"

Set-Location $root
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"`n===== $stamp  cli run =====" | Add-Content $log
try {
    & $py -m tracking.cli run *>&1 | Add-Content $log
} catch {
    "ERROR: $_" | Add-Content $log
}
