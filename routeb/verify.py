#!/usr/bin/env python3
"""Behavioural oracle for the Route B fixture.

Modes
  vulnerable : original/unrepaired behaviour — valid inputs pass, >=16 corrupts the guard
  fixed      : repaired behaviour — valid inputs pass, >=16 returns -1 with guard intact
  roundtrip  : compare a candidate against a reference binary on every case; the
               recompiled-but-unchanged function must behave exactly like the original

Examples
  python3 verify.py --binary ./vuln --mode vulnerable
  python3 verify.py --binary ./vuln_patched --mode fixed
  python3 verify.py --binary ./vuln_roundtrip --mode roundtrip --reference ./vuln

Exit code 0 means every expectation held; 1 means at least one did not.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict

VALID = ["", "a", "hello", "0123456789abcde"]          # lengths 0,1,5,15
INVALID = ["0123456789abcdef", "0123456789abcdefg"]     # lengths 16,17

LINE = re.compile(r"^in_len=(\d+) ret=(-?\d+) name=(.*) guard=(ok|CORRUPT)$")


@dataclass
class Observation:
    case: str
    kind: str
    returncode: int
    stdout: str
    stderr: str
    parsed: bool
    in_len: int | None
    ret: int | None
    name: str | None
    guard: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def run(binary: str, arg: str) -> Observation:
    proc = subprocess.run(
        [binary, arg], capture_output=True, text=True, timeout=5, check=False
    )
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    m = LINE.match(line)
    return Observation(
        case=arg,
        kind="valid" if len(arg) <= 15 else "invalid",
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr[-400:],
        parsed=bool(m),
        in_len=int(m.group(1)) if m else None,
        ret=int(m.group(2)) if m else None,
        name=m.group(3) if m else None,
        guard=m.group(4) if m else None,
    )


def check_vulnerable(o: Observation) -> str | None:
    if not o.parsed:
        return "output does not match the expected line format"
    if o.kind == "valid":
        if o.guard != "ok":
            return "valid input corrupted the guard"
        if o.name != o.case:
            return f"valid input not copied: name={o.name!r}"
        if o.returncode != 0:
            return f"valid input exited {o.returncode}"
    else:
        if o.guard != "CORRUPT":
            return "overlong input did not corrupt the guard (defect not reproduced)"
    return None


def check_fixed(o: Observation) -> str | None:
    if not o.parsed:
        return "output does not match the expected line format"
    if o.guard != "ok":
        return "guard corrupted"
    if o.kind == "valid":
        if o.name != o.case:
            return f"valid input not copied: name={o.name!r}"
        if o.returncode != 0:
            return f"valid input exited {o.returncode}"
    else:
        if o.ret != -1:
            return f"overlong input returned {o.ret}, contract requires -1"
        if o.returncode != 0:
            return f"overlong input exited {o.returncode}, contract requires 0"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--mode", choices=["vulnerable", "fixed", "roundtrip"], required=True)
    ap.add_argument("--reference", help="reference binary for roundtrip mode")
    ap.add_argument("--json-out")
    args = ap.parse_args()

    if args.mode == "roundtrip" and not args.reference:
        ap.error("--reference is required for roundtrip mode")

    rows = []
    failures = 0
    for arg in VALID + INVALID:
        obs = run(args.binary, arg)
        problems = []

        if args.mode == "vulnerable":
            err = check_vulnerable(obs)
            if err:
                problems.append(err)
        elif args.mode == "fixed":
            err = check_fixed(obs)
            if err:
                problems.append(err)
        else:
            ref = run(args.reference, arg)
            base_err = check_vulnerable(ref)
            if base_err:
                problems.append(f"reference is not behaving as the frozen original: {base_err}")
            if (obs.stdout, obs.returncode) != (ref.stdout, ref.returncode):
                problems.append("candidate output differs from the reference")
            err = check_vulnerable(obs)
            if err:
                problems.append(f"unchanged round trip changed behaviour: {err}")

        failures += bool(problems)
        rows.append({**obs.as_dict(), "problems": problems})

    summary = {
        "binary": args.binary,
        "mode": args.mode,
        "cases": len(rows),
        "failed": failures,
        "passed": len(rows) - failures,
        "ok": failures == 0,
        "rows": rows,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
