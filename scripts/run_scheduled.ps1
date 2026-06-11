# Runs one pull+write cycle and appends timestamped output to logs/run.log.
# Invoked by the "EngagementTracker" scheduled task (every 4 minutes).
param(
    [switch]$Drafts
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot           # ...\engagement-tracker
if ($env:TRACKING_PYTHON_EXE) {
    $py = $env:TRACKING_PYTHON_EXE
} else {
    $py = Join-Path $root ".venv\Scripts\python.exe"
}
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "run.log"
$maxBytes = 2MB
$logCapWarning = $null
if ($env:TRACKING_RUN_LOG_MAX_BYTES) {
    $parsedMaxBytes = 0
    if ([int64]::TryParse($env:TRACKING_RUN_LOG_MAX_BYTES, [ref]$parsedMaxBytes) -and $parsedMaxBytes -gt 0) {
        $maxBytes = $parsedMaxBytes
    } else {
        $logCapWarning = "invalid TRACKING_RUN_LOG_MAX_BYTES='$($env:TRACKING_RUN_LOG_MAX_BYTES)'; using default $maxBytes bytes"
    }
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
if ($logCapWarning) {
    "WARN: $logCapWarning" | Add-Content $log
}
try {
    $cliArgs = @("-m", "tracking.cli", "run")
    if ($Drafts) { $cliArgs += "--drafts" }
    $output = & $py @cliArgs *>&1
    $exitCode = $LASTEXITCODE
    $output | Add-Content $log
    if ($null -eq $exitCode) { $exitCode = 0 }
    if ($exitCode -ne 0) {
        "ERROR: CLI exited with code $exitCode" | Add-Content $log
    }
    exit $exitCode
} catch {
    "ERROR: $_" | Add-Content $log
    exit 1
}
