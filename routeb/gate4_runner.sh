#!/usr/bin/env bash
# Gate 4 runner for jobwrap (uid 1003, no sudo, no unshare).
#
# Invocation (jobwrap already applied setuid 1003 and no-egress netns):
#   routeb/gate4_runner.sh <repo-root> <writable-work-dir>
#
# The script never calls sudo/unshare and never needs network. It verifies the
# frozen ELF, runs Ghidra + Patcherex2 unchanged round trip, compares the six
# manifest cases, and writes gate4-status.json.
set -uo pipefail

ROOT=${1:-$PWD}
WORK=${2:-$PWD/gate4-evidence}
FUNC=CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_51b_badSink
EXPECTED_ELF=aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393
EXPECTED_UID=${EXPECTED_UID:-1003}
PYTHON=${PYTHON:-/opt/angr-venv/bin/python}
CLANG=${CLANG:-clang}
LLD=${LLD:-ld.lld}

ORIG_HOME=${HOME:-}
mkdir -p "$WORK" || { echo "cannot create $WORK" >&2; exit 2; }
export HOME="$WORK/home"
export TMPDIR="$WORK/tmp"
mkdir -p "$HOME" "$TMPDIR"
cd "$ROOT" || exit 2

STATUS="$WORK/gate4-status.json"
RESULT=fail
REASON=""

json_escape() { "$PYTHON" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

write_status() {
  cat > "$STATUS" <<EOF
{"gate4":"$RESULT",
 "reason":$(json_escape "$REASON"),
 "uid":$(id -u),
 "expected_uid":$EXPECTED_UID,
 "elf_sha256":$(json_escape "$(sha256sum "$WORK/original.elf" 2>/dev/null | cut -d' ' -f1)"),
 "patched_sha256":$(json_escape "$(sha256sum "$WORK/roundtrip.elf" 2>/dev/null | cut -d' ' -f1)"),
 "manifest_compare":$(json_escape "$WORK/manifest-compare.json"),
 "environment":$(json_escape "$WORK/environment.txt")}
EOF
  cat "$STATUS"
}

record_env() {
  {
    echo "uname: $(uname -a)"
    echo "uid: $(id -u) gid: $(id -g)"
    echo "cap_eff: $(awk '/CapEff/ {print $2}' /proc/self/status 2>/dev/null)"
    echo "no_new_privs: $(awk '/NoNewPrivs/ {print $2}' /proc/self/status 2>/dev/null)"
    echo "netns: $(readlink /proc/self/ns/net 2>/dev/null)"
    echo "route: $(ip route show default 2>/dev/null || echo 'ip-unavailable')"
    echo "os: $(cat /etc/os-release 2>/dev/null | tr '\n' ' ')"
    echo "java: $(java -version 2>&1 | head -1)"
    echo "ghidra_home: ${GHIDRA_HOME:-unset}"
    echo "ghidra_version: $(grep -h '^application.version=' "${GHIDRA_HOME:-/nonexistent}"/Ghidra/application.properties 2>/dev/null || echo unknown)"
    echo "python: $PYTHON $("$PYTHON" --version 2>&1)"
    echo "clang: $(command -v "$CLANG" || echo missing) $("$CLANG" --version 2>&1 | head -1)"
    echo "ld.lld: $(command -v "$LLD" || echo missing) $("$LLD" --version 2>&1 | head -1)"
    echo "shim clang-15: $(readlink -f "$WORK/toolchain-shims/clang-15" 2>/dev/null || echo missing)"
    echo "shim ld.lld-15: $(readlink -f "$WORK/toolchain-shims/ld.lld-15" 2>/dev/null || echo missing)"
    "$PYTHON" - <<'PY' 2>/dev/null || true
import importlib.metadata as m
for p in ("patcherex2", "angr", "z3-solver", "lief"):
    try:
        print(f"{p}: {m.version(p)}")
    except Exception as e:
        print(f"{p}: missing ({e})")
PY
    echo "clang-15 resolved: $(command -v clang-15 || echo missing)"
    echo "ld.lld-15 resolved: $(command -v ld.lld-15 || echo missing)"
  } > "$WORK/environment.txt" 2>&1
}

if [[ ! -f "fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf" ]]; then
  RESULT=not_run; REASON="fixture ELF missing in checkout"; record_env; write_status; exit 2
fi
cp "fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf" "$WORK/original.elf"
chmod +x "$WORK/original.elf"
GOT=$(sha256sum "$WORK/original.elf" | cut -d' ' -f1)
record_env
if [[ "$GOT" != "$EXPECTED_ELF" ]]; then
  RESULT=fail; REASON="ELF hash mismatch: $GOT"; write_status; exit 3
fi
if [[ "$(id -u)" != "$EXPECTED_UID" ]]; then
  RESULT=not_run; REASON="runner uid $(id -u) != expected $EXPECTED_UID (jobwrap must setuid 1003)"; write_status; exit 2
fi

# Resolve Ghidra without network or sudo.
if [[ ! -x "$PYTHON" ]]; then
  RESULT=not_run; REASON="python interpreter not found: $PYTHON (set PYTHON)"; write_status; exit 2
fi
if [[ -z "${GHIDRA_HOME:-}" ]]; then
  for d in /opt/ghidra* /usr/local/ghidra* "${ORIG_HOME}"/ghidra*; do
    if [[ -x "$d/support/analyzeHeadless" ]]; then GHIDRA_HOME=$d; export GHIDRA_HOME; break; fi
  done
fi
if [[ -z "${GHIDRA_HOME:-}" || ! -x "$GHIDRA_HOME/support/analyzeHeadless" ]]; then
  RESULT=not_run; REASON="Ghidra analyzeHeadless not found (set GHIDRA_HOME)"; write_status; exit 2
fi
CLANG_BIN=$(command -v "$CLANG" || true)
LLD_BIN=$(command -v "$LLD" || true)
if [[ -z "$CLANG_BIN" || -z "$LLD_BIN" ]]; then
  RESULT=not_run; REASON="clang/ld.lld missing (set CLANG/LLD)"; write_status; exit 2
fi
# Patcherex2 0.2.14 invokes the literal names clang-15 and ld.lld-15.  Provide
# per-job shims to the snapshot's actual clang/ld.lld (clang 18 is supported);
# no sudo and no system-wide install is required.
SHIMDIR="$WORK/toolchain-shims"
mkdir -p "$SHIMDIR"
ln -sf "$CLANG_BIN" "$SHIMDIR/clang-15"
ln -sf "$LLD_BIN" "$SHIMDIR/ld.lld-15"
export PATH="$SHIMDIR:$PATH"
record_env

echo "== Ghidra export =="
"$ROOT/routeb/ghidra/export.sh" "$WORK/original.elf" "$FUNC" "$WORK/ghidra" > "$WORK/ghidra.log" 2>&1 || {
  RESULT=fail; REASON="Ghidra export failed; see ghidra.log"; write_status; exit 3; }

echo "== Patcherex2 unchanged round trip =="
"$PYTHON" "$ROOT/routeb/probe_patcherex2.py" --mode roundtrip \
  --binary "$WORK/original.elf" --func "$FUNC" \
  --c-source "$ROOT/routeb/experiment/samples/juliet_badSink.normalized.c" \
  --out "$WORK/roundtrip.elf" --skip-verify > "$WORK/patcherex2.log" 2>&1 || {
  RESULT=fail; REASON="Patcherex2 patch failed; see patcherex2.log"; write_status; exit 3; }
chmod +x "$WORK/roundtrip.elf" 2>/dev/null || true

echo "== six-case manifest compare =="
"$PYTHON" "$ROOT/routeb/run_manifest_compare.py" \
  --original "$WORK/original.elf" --patched "$WORK/roundtrip.elf" \
  --tests "$ROOT/fixtures/public/CWE121_src_char_declare_cpy_51b/tests" \
  --out "$WORK/manifest-compare.json" > "$WORK/compare.log" 2>&1 || true
readelf -lW "$WORK/roundtrip.elf" > "$WORK/readelf.txt" 2>&1 || true

if [[ ! -s "$WORK/manifest-compare.json" ]]; then
  RESULT=fail; REASON="manifest comparator produced no JSON"; write_status; exit 3
fi
PASS=$("$PYTHON" -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["gate4_pass"]).lower())' "$WORK/manifest-compare.json" 2>/dev/null || echo false)
if [[ "$PASS" == "true" ]]; then
  RESULT=pass; REASON="6/6 behaviour-identical"
else
  RESULT=fail; REASON="manifest comparison not 6/6"
fi
write_status
[[ "$RESULT" == "pass" ]] && exit 0 || exit 3
