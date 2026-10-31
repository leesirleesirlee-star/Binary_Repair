#!/usr/bin/env bash
# Run the queued-run worker for the integrated controller.
# Optional env:
#   BINARYREPAIR_DATA_DIR   controller data dir (default: <repo>/var)
#   BINARYREPAIR_ATTEMPT_CMD executor wrapper for an attempt
#   BINARYREPAIR_WORKER_POLL poll seconds (default: 2)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${BINARYREPAIR_DATA_DIR:-$ROOT/var}"
PYTHON="${PYTHON:-python3}"
POLL="${BINARYREPAIR_WORKER_POLL:-2}"

echo "BinaryRepair worker"
echo "  repo: $ROOT"
echo "  data: $DATA_DIR"
echo "  poll: ${POLL}s"

exec "$PYTHON" -m app.services.worker --data-dir "$DATA_DIR" --poll "$POLL"
