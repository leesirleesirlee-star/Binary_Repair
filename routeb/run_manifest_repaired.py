#!/usr/bin/env python3
"""Run the frozen manifest against a repaired candidate and check expectations."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(elf: Path, arg: str) -> dict:
    try:
        p = subprocess.run([str(elf), arg], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=15)
        return {"returncode": p.returncode, "stdout_hex": p.stdout.hex(),
                "stderr_hex": p.stderr.hex()}
    except subprocess.TimeoutExpired as exc:
        return {"timeout": True, "returncode": None,
                "stdout_hex": (exc.stdout or b"").hex(),
                "stderr_hex": (exc.stderr or b"").hex()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--tests", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    rows = []
    for name in ("valid.json", "invalid.json"):
        data = json.loads((args.tests / name).read_text(encoding="utf-8"))
        for case in data["tests"]:
            arg = case["argv"][1]
            got = run(args.binary, arg)
            exp = case["expected"]
            ok = (
                got.get("returncode") == exp["return_code"]
                and got.get("stdout_hex") == exp["stdout"].encode().hex()
                and got.get("stderr_hex") == exp["stderr"].encode().hex()
            )
            rows.append({"manifest": name, "id": case["id"], "kind": case["kind"],
                         "input_len": len(arg.encode()), "expected": exp,
                         "got": got, "ok": ok})
    passed = sum(1 for row in rows if row["ok"])
    result = {"binary": str(args.binary), "binary_sha256": sha256(args.binary),
              "cases": rows, "passed": passed, "total": len(rows),
              "repaired_pass": passed == len(rows)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, "total": len(rows),
                      "repaired_pass": result["repaired_pass"],
                      "binary_sha256": result["binary_sha256"]}, indent=2))
    return 0 if result["repaired_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
