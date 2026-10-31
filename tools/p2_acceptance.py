#!/usr/bin/env python3
"""P2 acceptance check for a completed controller job.

This does not execute anything. It waits for a real job to reach ``finished`` and
asserts that the controller snapshot exposes two route verdicts and their
obligations. Exit 0 only when the snapshot is complete.

Usage:
  python tools/p2_acceptance.py --job-id <id> [--base-url http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VALID_OUTCOMES = frozenset({
    "success_within_scope",
    "unsupported",
    "rejected",
    "inconclusive",
    "error",
    "cancelled",
})
OBLIGATION_KINDS = frozenset({
    "identity", "structure", "interface", "behaviour", "symbolic", "reproducibility",
})


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _snapshot_from_data(data_dir: Path, job_id: str) -> dict | None:
    os.environ["BINARYREPAIR_DATA_DIR"] = str(data_dir)
    from app.controller_api import _read_meta, _snapshot
    from app.services import jobs

    record = jobs.get_job(job_id, data_dir=data_dir)
    if record is None:
        return None
    return _snapshot(record, _read_meta(job_id))


def validate_snapshot(snapshot: dict) -> list[str]:
    """Return acceptance errors. This is deliberately stricter than schema-only."""
    errors: list[str] = []
    if snapshot.get("demo") is not False:
        errors.append("snapshot demo is not false")
    verdict_raw = snapshot.get("verdict")
    verdict = None
    if not verdict_raw:
        errors.append("snapshot verdict missing")
    elif isinstance(verdict_raw, dict):
        verdict = verdict_raw.get("outcome")
        if verdict not in VALID_OUTCOMES:
            errors.append(f"snapshot verdict is not a valid outcome: {verdict!r}")
    elif verdict_raw not in VALID_OUTCOMES:
        errors.append(f"snapshot verdict is not a valid outcome: {verdict_raw!r}")
    else:
        verdict = verdict_raw

    all_not_run_routes: list[str] = []
    for key in ("A", "B"):
        route = (snapshot.get("routes") or {}).get(key) or {}
        outcome = route.get("outcome")
        if outcome is None:
            errors.append(f"route {key} has no outcome")
        elif outcome not in VALID_OUTCOMES:
            errors.append(f"route {key} outcome is not a valid outcome: {outcome!r}")
        if not route.get("verdict"):
            errors.append(f"route {key} verdict missing")
        obligations = route.get("obligations") or []
        kinds = {item.get("kind") for item in obligations if isinstance(item, dict)}
        if len(obligations) != 6 or kinds != OBLIGATION_KINDS:
            errors.append(f"route {key} obligations incomplete: {sorted(kinds)}")
        results = [item.get("result") for item in obligations if isinstance(item, dict)]
        all_not_run = bool(obligations) and all(result == "not_run" for result in results)
        if all_not_run:
            all_not_run_routes.append(key)
        if outcome == "success_within_scope" and all_not_run:
            errors.append(
                f"route {key} is success_within_scope with all obligations not_run"
            )
    if verdict == "success_within_scope" and all_not_run_routes:
        errors.append(
            "snapshot is success_within_scope but route(s) all obligations not_run: "
            + ",".join(all_not_run_routes)
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--poll", type=float, default=2.0)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="offline synthetic mode: read the controller store directly")
    parser.add_argument("--synthetic", action="store_true",
                        help="label the result as synthetic (never a real gate pass)")
    args = parser.parse_args(argv)

    if args.data_dir is not None and not args.synthetic:
        print("p2_acceptance: BLOCKED — --data-dir is synthetic and requires --synthetic",
              file=sys.stderr)
        return 2
    if args.synthetic and args.data_dir is None:
        print("p2_acceptance: BLOCKED — --synthetic requires --data-dir",
              file=sys.stderr)
        return 2

    if args.data_dir is not None:
        snapshot = _snapshot_from_data(args.data_dir, args.job_id)
        if snapshot is None:
            print(f"p2_acceptance: no job {args.job_id}", file=sys.stderr)
            return 2
        if snapshot.get("phase") != "finished":
            print(f"p2_acceptance: job {args.job_id} not finished: {snapshot.get('phase')}",
                  file=sys.stderr)
            return 2
    else:
        deadline = time.monotonic() + args.timeout
        while True:
            try:
                snapshot = _get(args.base_url, f"/api/jobs/{args.job_id}")
            except (urllib.error.URLError, TimeoutError) as exc:
                print(f"p2_acceptance: BLOCKED — controller unreachable: {exc}", file=sys.stderr)
                return 2
            if snapshot.get("phase") == "finished":
                break
            if time.monotonic() >= deadline:
                print(f"p2_acceptance: job {args.job_id} did not finish", file=sys.stderr)
                print(json.dumps({"phase": snapshot.get("phase"), "routes": snapshot.get("routes")}, indent=2))
                return 1
            time.sleep(args.poll)

    errors = validate_snapshot(snapshot)

    summary = {
        "job_id": args.job_id,
        "synthetic": bool(args.synthetic),
        "phase": snapshot.get("phase"),
        "verdict": snapshot.get("verdict"),
        "routes": {
            key: {
                "outcome": (snapshot.get("routes", {}).get(key) or {}).get("outcome"),
                "obligations": len((snapshot.get("routes", {}).get(key) or {}).get("obligations") or []),
            }
            for key in ("A", "B")
        },
    }
    print(json.dumps(summary, indent=2))
    if errors:
        for error in errors:
            print(f"p2_acceptance: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
