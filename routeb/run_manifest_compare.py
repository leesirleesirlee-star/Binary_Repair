#!/usr/bin/env python3
"""Run the frozen manifest against two ELFs and compare behaviour byte-for-byte.

Gate 4 (unchanged Route B round trip) passes only when all six cases produce the
same exit code, stdout and stderr on the original and the patched binary. The
original is expected to be 4/6 (the two invalid cases fail); the patched
unchanged binary must reproduce exactly that 4/6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def run(elf: Path, arg: str) -> dict:
    try:
        p = subprocess.run(
            [str(elf), arg], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
        )
        return {
            "returncode": p.returncode,
            "stdout_hex": p.stdout.hex(),
            "stderr_hex": p.stderr.hex(),
        }
    except subprocess.TimeoutExpired as exc:
        return {"timeout": True, "stdout_hex": (exc.stdout or b"").hex(),
                "stderr_hex": (exc.stderr or b"").hex(), "returncode": None}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", type=Path, required=True)
    ap.add_argument("--patched", type=Path, required=True)
    ap.add_argument("--tests", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    cases = []
    for name in ("valid.json", "invalid.json"):
        data = json.loads((args.tests / name).read_text(encoding="utf-8"))
        for case in data["tests"]:
            cases.append((name, case))

    rows = []
    for name, case in cases:
        arg = case["argv"][1]
        before = run(args.original, arg)
        after = run(args.patched, arg)
        same = before == after
        rows.append({
            "manifest": name,
            "id": case["id"],
            "kind": case["kind"],
            "input_len": len(arg.encode()),
            "original": before,
            "patched": after,
            "same": same,
        })

    passed = sum(1 for row in rows if row["same"])
    result = {
        "original_sha256": sha256(args.original),
        "patched_sha256": sha256(args.patched),
        "original_size": args.original.stat().st_size,
        "patched_size": args.patched.stat().st_size,
        "cases": rows,
        "same_count": passed,
        "total": len(rows),
        "gate4_pass": passed == len(rows),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in
                      ("same_count", "total", "gate4_pass",
                       "original_sha256", "patched_sha256")}, indent=2))
    return 0 if result["gate4_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
