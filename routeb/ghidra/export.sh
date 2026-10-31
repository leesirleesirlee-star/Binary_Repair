#!/usr/bin/env bash
# Export one decompiled function from an ELF using Ghidra headless.
#
#   export.sh <elf> [function] [outdir]
#
# Requires Ghidra 11.x and JDK 21. Set GHIDRA_HOME if it is not ~/ghidra_11.4_PUBLIC.
set -euo pipefail

GHIDRA_HOME=${GHIDRA_HOME:-$HOME/ghidra_11.4_PUBLIC}
BIN=${1:?usage: export.sh <elf> [function] [outdir]}
FUNC=${2:-copy_name}
OUT=${3:-$(cd "$(dirname "$0")/.." && pwd)/out}

if [[ ! -x "$GHIDRA_HOME/support/analyzeHeadless" ]]; then
  echo "analyzeHeadless not found under GHIDRA_HOME=$GHIDRA_HOME" >&2
  exit 1
fi

if ! command -v java >/dev/null 2>&1; then
  echo "java not found. Ghidra 11.x needs JDK 21 (sudo apt install openjdk-21-jdk)." >&2
  exit 1
fi

java -version 2>&1 | head -1
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

PROJ=$(mktemp -d "${TMPDIR:-/tmp}/br-ghidra.XXXXXX")
trap 'rm -rf "$PROJ"' EXIT

"$GHIDRA_HOME/support/analyzeHeadless" "$PROJ" brproj \
  -import "$(cd "$(dirname "$BIN")" && pwd)/$(basename "$BIN")" \
  -overwrite \
  -scriptPath "$(cd "$(dirname "$0")" && pwd)" \
  -postScript ExportFunction.java "$FUNC" "$OUT/$FUNC.c" \
  -deleteProject

echo
echo "--- $OUT/$FUNC.c ---"
sed -n '1,120p' "$OUT/$FUNC.c"
