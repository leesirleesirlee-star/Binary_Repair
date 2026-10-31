#!/usr/bin/env bash
# Run M4's fixed Gate 5 script inside the guest/jobwrap.
# Invocation:
#   GATE5_SCRIPT=/path/M4-gate5-fixed.sh \
#   GATE5_SCRIPT_SHA256=<64 lowercase hex> \
#     jobwrap gate5 -- <repo>/routeb/jobwrap_gate5.sh <repo> <work-dir>
#
# This wrapper never calls sudo/unshare and never runs on the host.
set -euo pipefail
ROOT=${1:-$PWD}
WORK=${2:-$PWD/gate5-evidence}
SCRIPT=${GATE5_SCRIPT:-}
EXPECTED=${GATE5_SCRIPT_SHA256:-}
if [[ -z "$SCRIPT" || ! -x "$SCRIPT" ]]; then
  echo "GATE5_SCRIPT must point to an executable M4 Gate 5 script" >&2
  exit 2
fi
if [[ ! "$EXPECTED" =~ ^[0-9a-f]{64}$ ]]; then
  echo "GATE5_SCRIPT_SHA256 must be 64 lowercase hex characters" >&2
  exit 2
fi
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL=$(sha256sum "$SCRIPT" | awk '{print $1}')
else
  ACTUAL=$(shasum -a 256 "$SCRIPT" | awk '{print $1}')
fi
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  echo "GATE5_SCRIPT_SHA256 mismatch: expected $EXPECTED got $ACTUAL" >&2
  exit 1
fi
mkdir -p "$WORK"
export HOME="$WORK/home"
export TMPDIR="$WORK/tmp"
mkdir -p "$HOME" "$TMPDIR"
cd "$ROOT"
exec "$SCRIPT" "$ROOT" "$WORK"
