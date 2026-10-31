#!/usr/bin/env bash
# BINARYREPAIR_ATTEMPT_CMD wrapper for app.services.worker.
set -euo pipefail
exec "${PYTHON:-python3}" -m routeb.attempt_writer --from-env
