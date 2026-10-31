#!/usr/bin/env bash
# Route B executor under jobwrap (decision B). No sudo/unshare here.
# Invocation:
#   jobwrap routeb -- /path/repo/routeb/jobwrap_routeb.sh <repo-root> <work-dir>
set -euo pipefail
ROOT=${1:-$PWD}
WORK=${2:-$PWD/routeb-evidence}
FUNC=${FUNC:-CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_51b_badSink}
PYTHON=${PYTHON:-/opt/angr-venv/bin/python}
ORIGINAL=${ORIGINAL:-$ROOT/fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf}
C_SOURCE=${C_SOURCE:-$ROOT/routeb/experiment/samples/juliet_badSink.normalized.c}
TESTS=${TESTS:-$ROOT/fixtures/public/CWE121_src_char_declare_cpy_51b/tests}
MODE=${MODE:-roundtrip}
OUT=${OUT:-$WORK/routeb}

mkdir -p "$WORK" "$OUT"
cd "$ROOT"
"$PYTHON" -m routeb.executor \
  --original "$ORIGINAL" \
  --func "$FUNC" \
  --c-source "$C_SOURCE" \
  --mode "$MODE" \
  --tests "$TESTS" \
  --run-manifest \
  --out-dir "$OUT"
