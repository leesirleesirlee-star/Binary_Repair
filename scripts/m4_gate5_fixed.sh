#!/usr/bin/env bash
# M4-owned fixed launcher for M5's routeb/jobwrap_gate5.sh.
#
# GUEST ONLY — never run on the host. Invocation (from the Lead/M5 handoff 44):
#
#   GATE5_SCRIPT=/path/to/repo/scripts/m4_gate5_fixed.sh \
#     jobwrap gate5 -- <repo>/routeb/jobwrap_gate5.sh <repo> /var/tmp/gate5-work
#
# The caller must verify this file's SHA-256 against the pinned value in
# docs/handoffs/50 §3a before the run and must chmod +x the guest copy. The script
# does no work of its own: it execs the M4 probe, which refuses to run unless
# euid == 1003 (jobwrap drops privileges before this point).
set -euo pipefail
ROOT=${1:?repo root}
WORK=${2:?work dir}
exec python3 "$ROOT/scripts/m4_gate5_probe.py" "$ROOT" "$WORK"
