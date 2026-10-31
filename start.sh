#!/usr/bin/env bash
# BinaryRepair GUI launcher for Ubuntu 24.04 / Python 3.12.
#
# UNVERIFIED ON UBUNTU until this exact script is run on the target host.
# It starts the mock GUI by default (synthetic data; demo:true).
#
# HTTP controller mode:
#   BINARYREPAIR_BACKEND=http BINARYREPAIR_API_BASE=http://127.0.0.1:8000 ./start.sh
#
# Optional:
#   BINARYREPAIR_SESSION_TOKEN=... ./start.sh
#   BINARYREPAIR_PORT=8123 ./start.sh
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv}"
PORT="${BINARYREPAIR_PORT:-8123}"

if [ ! -d "$VENV" ]; then
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

exec python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
