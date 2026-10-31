#!/usr/bin/env python3
"""Convert a Route A result-bundle.json to M1's attempt_writer input.

M1 schema (controller-worker-executor-2026-10-22-v2):
  results: {V0..V5: passed|failed|not_run|unknown|timed_out}
  candidate_sha256, optional route/evidence/evidence_files/details.
  No outcome/reason/decision.

This is a thin, fail-closed mapping. It does not run anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

STATUS_MAP = {
    "pass": "passed",
    "fail": "failed",
    "not_run": "not_run",
    "inconclusive": "unknown",
    "unsupported": "unknown",
}
GATES = ("V0", "V1", "V2", "V3", "V4", "V5")


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve(rel, job_dir, work_dir):
    """Resolve a bundle relative_path against the job/work roots."""
    if os.path.isabs(rel):
        return rel
    for root in (work_dir, job_dir):
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return os.path.join(work_dir, rel)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--route", default="route_a", choices=["route_a"])
    ap.add_argument("--candidate-sha256")
    args = ap.parse_args(argv)

    bundle = load(args.bundle)
    identity = bundle.get("identity", {})
    candidate = args.candidate_sha256 or identity.get("candidate_sha256")
    if not candidate or len(candidate) != 64:
        raise SystemExit("candidate_sha256 missing or not 64 hex")
    try:
        int(candidate, 16)
    except ValueError as exc:
        raise SystemExit("candidate_sha256 is not hex") from exc
    route = bundle.get("route", "A")
    if route not in ("A", "route_a"):
        raise SystemExit(f"bundle route is {route}, not route_a")

    results, evidence_files, details = {}, {}, {}
    for gate in GATES:
        src = bundle.get("gates", {}).get(gate)
        if not src:
            raise SystemExit(f"bundle has no gate {gate}")
        raw = src.get("status")
        if raw not in STATUS_MAP:
            raise SystemExit(f"gate {gate} has invalid status {raw!r}")
        mapped = STATUS_MAP[raw]
        results[gate] = mapped

        extra = []
        for ev in src.get("evidence") or []:
            rel = ev.get("relative_path")
            if rel:
                resolved = resolve(rel, args.job_dir, args.work_dir)
                if os.path.isfile(resolved):
                    extra.append(resolved)
        if mapped == "passed" and gate != "V0" and not extra:
            raise SystemExit(f"gate {gate} is passed but has no evidence file")
        if extra and mapped == "passed" and gate != "V0":
            evidence_files[gate] = extra[0]
        if gate == "V0":
            continue
        note = src.get("details") or {}
        missing = src.get("missing") or []
        if missing:
            details[gate] = "; ".join(str(x) for x in missing)
        elif note:
            details[gate] = json.dumps(note, ensure_ascii=False, sort_keys=True)

    payload = {
        "route": args.route,
        "candidate_sha256": candidate,
        "results": results,
        "evidence_files": evidence_files,
        "details": details,
    }
    # M1 forbids these fields in the input.
    for forbidden in ("outcome", "reason", "decision"):
        payload.pop(forbidden, None)

    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps({"out": args.out, "results": results, "evidence_files": evidence_files}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
