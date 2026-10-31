#!/usr/bin/env python3
"""M4-authored Gate 5 Route A runtime placement probe — GUEST ONLY.

Never run this on the host: it executes the repaired candidate. Run it inside the
disposable guest under the jobwrap/isolation controls (uid 1003, CapEff=0,
NoNewPrivs=1, no egress, evaluator unreadable):

    jobwrap ... -- python3 /path/to/repo/scripts/m4_gate5_probe.py /path/to/repo /var/tmp/m4-gate5-<ts>

The script refuses to run unless euid == 1003 (jobwrap must setuid/drop first). It
writes raw evidence plus a sorted SHA256SUMS under the work directory; M4 classifies
the result PASS / FAIL / NOT VERIFIED. The script does not call sudo, does not use
the network, and makes no pass/fail claim of its own (its CHECK lines are inputs for
M4, not a gate result).

Subject (must be the frozen Route A candidate, not R3B/probe artifacts):
  candidate  routea/out/cave-probe.elf  sha256 c4a159…aa82
  original   fixtures/.../original.elf   sha256 aa428157…8393
  tests      fixtures/.../tests/{valid,invalid}.json (six frozen cases)
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EXPECT_ORIG = "aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393"
EXPECT_CAND = "c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82"
CASE_FILES = ("valid.json", "invalid.json")
CAVE_START = 0x4018E9
CAVE_END = 0x402000  # exclusive; dumps 0x4018e9..0x401fff (1815 bytes)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd: list[str]) -> tuple[int, bytes, bytes]:
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=120)
        return completed.returncode, completed.stdout, completed.stderr
    except FileNotFoundError:
        return 127, b"", f"command not found: {cmd[0]}".encode()
    except subprocess.TimeoutExpired:
        return 124, b"", b"timeout"


def elf_file_offset(buf: bytes, vaddr: int) -> int | None:
    import struct

    e_phoff, = struct.unpack_from("<Q", buf, 0x20)
    e_phentsize, e_phnum = struct.unpack_from("<HH", buf, 0x36)
    for index in range(e_phnum):
        p_type, _flags, offset, va, _pa, filesz, _memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", buf, e_phoff + index * e_phentsize
        )
        if p_type == 1 and va <= vaddr < va + filesz:
            return offset + (vaddr - va)
    return None


def main() -> int:
    if not hasattr(os, "geteuid"):
        print("NOT VERIFIED: non-POSIX host; this probe is guest-only")
        return 2
    if len(sys.argv) != 3:
        print("usage: m4_gate5_probe.py <repo-root> <work-dir>")
        return 2
    root = Path(sys.argv[1]).resolve()
    work = Path(sys.argv[2]).resolve()
    candidate = root / "routea/out/cave-probe.elf"
    original = root / "fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf"
    tests = root / "fixtures/public/CWE121_src_char_declare_cpy_51b/tests"

    checks: list[dict[str, str]] = []

    def check(check_id: str, status: str, detail: str) -> None:
        assert status in {"PASS", "FAIL", "NOT VERIFIED"}
        checks.append({"id": check_id, "status": status, "detail": detail})
        print(f"CHECK {status} {check_id} {detail}")

    if os.geteuid() != 1003:
        print(f"NOT RUN: euid={os.geteuid()} (expected 1003 from jobwrap); no evidence produced")
        return 2
    if not candidate.is_file() or not original.is_file():
        print("NOT VERIFIED: candidate or original missing in the checkout")
        return 2
    work.mkdir(parents=True, exist_ok=True)
    # Run our own copy: the repo checkout may not carry the executable bit, and the
    # original repo bytes stay untouched.
    source_candidate = candidate
    candidate = work / "candidate.elf"
    shutil.copyfile(source_candidate, candidate)
    os.chmod(candidate, 0o755)

    # --- identity and environment -----------------------------------------
    candidate_hash = sha256_file(candidate)
    original_hash = sha256_file(original)
    check("R1-candidate-identity", "PASS" if candidate_hash == EXPECT_CAND else "FAIL", candidate_hash)
    check("R1-original-identity", "PASS" if original_hash == EXPECT_ORIG else "FAIL", original_hash)

    status_text = Path("/proc/self/status").read_text(errors="replace") if Path("/proc/self/status").is_file() else ""
    env = [
        f"date_utc={datetime.now(timezone.utc).isoformat()}",
        f"uname={platform.platform()}",
        f"python={platform.python_version()}",
        f"script_sha256={sha256_file(Path(__file__).resolve())}",
        f"source_candidate={source_candidate}",
        *[line for line in status_text.splitlines() if line.startswith(("Uid:", "Gid:", "CapEff:", "CapBnd:", "NoNewPrivs:", "Seccomp:"))],
    ]
    ip_rc, ip_out, _ = run(["ip", "route", "show", "default"])
    env.append(f"default_route_rc={ip_rc} default_route={ip_out.decode(errors='replace').strip()!r}")
    try:
        env.append(f"netns={os.readlink('/proc/self/ns/net')}")
    except OSError:
        env.append("netns=unreadable")
    gdb = shutil.which("gdb")
    env.append(f"gdb={gdb or 'missing'}")
    (work / "environment.txt").write_text("\n".join(env) + "\n", encoding="utf-8")

    caps = ""
    nnp = ""
    for line in status_text.splitlines():
        if line.startswith("CapEff:"):
            caps = line.split(":", 1)[1].strip()
        if line.startswith("NoNewPrivs:"):
            nnp = line.split(":", 1)[1].strip()
    check("R6-isolation-uid", "PASS" if os.geteuid() == 1003 else "FAIL", f"uid={os.geteuid()}")
    check("R6-isolation-caps", "PASS" if caps and set(caps) == {"0"} else "FAIL", f"CapEff={caps or 'missing'}")
    check("R6-isolation-nnp", "PASS" if nnp == "1" else "FAIL", f"NoNewPrivs={nnp or 'missing'}")

    # --- runtime mapping / memory equals file / breakpoints ----------------
    memory_dump = work / "cave-memory.bin"
    if gdb is None:
        check("R2-runtime-mapping", "NOT VERIFIED", "gdb missing in the guest")
        check("R3-memory-equals-file", "NOT VERIFIED", "no runtime memory dump")
        check("R4-breakpoints", "NOT VERIFIED", "no gdb probe")
    else:
        gdb_script = work / "probe.gdb"
        gdb_script.write_text(
            "\n".join(
                [
                    "set pagination off",
                    "set confirm off",
                    "set breakpoint pending on",
                    "break *0x401304",
                    "commands",
                    " silent",
                    ' printf "SITE pc=%p rsp=%p rdi=%p rsi=%p\\n", $pc,$rsp,$rdi,$rsi',
                    " continue",
                    "end",
                    "break *0x4018e9",
                    "commands",
                    " silent",
                    ' printf "CAVE pc=%p rsp=%p\\n", $pc,$rsp',
                    " x/12i $pc",
                    " info proc mappings",
                    f" dump binary memory {memory_dump} 0x{CAVE_START:x} 0x{CAVE_END:x}",
                    " continue",
                    "end",
                    "break *0x401309",
                    "commands",
                    " silent",
                    ' printf "AFTER pc=%p rsp=%p rax=%p\\n", $pc,$rsp,$rax',
                    " continue",
                    "end",
                    "break *0x401317",
                    "commands",
                    " silent",
                    ' printf "RET pc=%p rsp=%p\\n", $pc,$rsp',
                    " continue",
                    "end",
                    "run",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        gdb_rc, gdb_out, gdb_err = run(
            [gdb, "-q", "-batch", "-x", str(gdb_script), "--args", str(candidate), "A" * 10]
        )
        (work / "gdb-probe.log").write_bytes(gdb_out + b"\n--- stderr ---\n" + gdb_err)
        gdb_text = (gdb_out + gdb_err).decode(errors="replace")
        (work / "gdb-probe.rc").write_text(f"rc={gdb_rc}\n", encoding="utf-8")
        mapping_line = ""
        for line in gdb_text.splitlines():
            low = line.lower()
            if "401000" in low and "402000" in low and ("r-x" in low or "r-xp" in low):
                mapping_line = line.strip()
                break
        check("R2-runtime-mapping", "PASS" if mapping_line else "NOT VERIFIED", mapping_line or "no r-xp mapping line covering 0x401000-0x402000")
        for marker, check_id in (("SITE", "R4-site"), ("CAVE", "R4-cave"), ("AFTER", "R4-after"), ("RET", "R4-ret")):
            check(check_id, "PASS" if marker in gdb_text else "FAIL", f"marker {marker} in gdb output")
        # cave file bytes from the candidate ELF (address -> file offset via phdr)
        cave_file = work / "cave-file.bin"
        buf = candidate.read_bytes()
        file_offset = elf_file_offset(buf, CAVE_START)
        if file_offset is None:
            check("R3-memory-equals-file", "NOT VERIFIED", "cave start is not file-backed in the candidate")
        else:
            cave_bytes = buf[file_offset : file_offset + (CAVE_END - CAVE_START)]
            cave_file.write_bytes(cave_bytes)
            (work / "cave-file.sha256").write_text(sha256_file(cave_file) + "\n", encoding="utf-8")
            if memory_dump.is_file():
                memory_bytes = memory_dump.read_bytes()
                (work / "cave-memory.sha256").write_text(sha256_file(memory_dump) + "\n", encoding="utf-8")
                same = memory_bytes == cave_bytes
                check(
                    "R3-memory-equals-file",
                    "PASS" if same else "FAIL",
                    f"file={sha256_file(cave_file)} memory={sha256_file(memory_dump)} bytes={len(cave_bytes)}/{len(memory_bytes)}",
                )
            else:
                check("R3-memory-equals-file", "NOT VERIFIED", "gdb produced no memory dump")

    # --- six frozen cases on the candidate --------------------------------
    case_rows = []
    for name in CASE_FILES:
        data = json.loads((tests / name).read_text(encoding="utf-8"))
        for case in data["tests"]:
            case_id = case["id"]
            argument = case["argv"][1]
            expected = case["expected"]
            rc, out, err = run([str(candidate), argument])
            rc_file = work / f"case-{case_id}.rc"
            out_file = work / f"case-{case_id}.stdout.bin"
            err_file = work / f"case-{case_id}.stderr.bin"
            rc_file.write_text(f"{rc}\n", encoding="utf-8")
            out_file.write_bytes(out)
            err_file.write_bytes(err)
            ok = (
                rc == expected["return_code"]
                and out == expected["stdout"].encode("utf-8")
                and err == expected["stderr"].encode("utf-8")
            )
            row = {
                "id": case_id,
                "kind": case["kind"],
                "input_len": len(argument.encode()),
                "rc": rc,
                "stdout_sha256": sha256_file(out_file),
                "stderr_sha256": sha256_file(err_file),
                "expected_rc": expected["return_code"],
                "ok": ok,
            }
            case_rows.append(row)
            check(f"R5-case-{case_id}", "PASS" if ok else "FAIL", f"rc={rc} expected={expected['return_code']}")

    (work / "six-cases.json").write_text(json.dumps(case_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    check("R5-six-cases", "PASS" if len(case_rows) == 6 and all(r["ok"] for r in case_rows) else "FAIL", f"{sum(1 for r in case_rows if r['ok'])}/6")

    # --- stable candidate bytes and evidence manifest ---------------------
    candidate_after = sha256_file(candidate)
    check("R7-candidate-stable", "PASS" if candidate_after == candidate_hash else "FAIL", candidate_after)

    (work / "probe-summary.json").write_text(json.dumps(checks, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    names = sorted(
        str(path.relative_to(work)).replace("\\", "/")
        for path in work.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [f"{sha256_file(work / name)}  {name}" for name in names]
    (work / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"CHECK PASS R8-evidence-manifest rows={len(lines)} sha256sums_sha256={sha256_file(work / 'SHA256SUMS')}")

    failed = [c for c in checks if c["status"] == "FAIL"]
    unverified = [c for c in checks if c["status"] == "NOT VERIFIED"]
    print(f"SUMMARY pass={sum(1 for c in checks if c['status'] == 'PASS')} fail={len(failed)} not_verified={len(unverified)}")
    print(f"WORKDIR={work}")
    if failed:
        return 1
    if unverified:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
