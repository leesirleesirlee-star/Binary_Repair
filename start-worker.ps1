# Run the queued-run worker for the integrated controller.
# Usage: .\start-worker.ps1
# Optional env:
#   BINARYREPAIR_DATA_DIR  controller data dir (default: <repo>\var)
#   BINARYREPAIR_ATTEMPT_CMD  executor wrapper for an attempt
#   BINARYREPAIR_WORKER_POLL  poll seconds (default: 2)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataDir = if ($env:BINARYREPAIR_DATA_DIR) { $env:BINARYREPAIR_DATA_DIR } else { Join-Path $root "var" }
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Write-Host "BinaryRepair worker" -ForegroundColor Cyan
Write-Host "  repo: $root"
Write-Host "  data: $dataDir"
Write-Host "  poll: $($env:BINARYREPAIR_WORKER_POLL ?? '2')s"
Write-Host ""

& $python -m app.services.worker `
    --data-dir $dataDir `
    --poll ([double]($env:BINARYREPAIR_WORKER_POLL ?? "2"))
