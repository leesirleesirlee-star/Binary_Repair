#!/usr/bin/env python3
"""Host-side inbox broker for executor result bundles.

Drop one envelope per attempt into ``--inbox``::

    {"job_id": "...", "run_id": "...", "attempt_id": "...",
     "route": "route_a" | "route_b", "bundle": {...}}

or ``"bundle_path": "relative/result.json"`` instead of ``bundle``.

The broker imports each envelope atomically into the controller store, moves the
envelope to ``processed/`` on success or ``failed/`` on error, and triggers the
worker once per batch. It never invents a verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import worker
from tools.host_importer import import_bundle

ROUTES = {"route_a", "route_b"}


def _read_envelope(path: Path) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be a JSON object")
    for key in ("job_id", "run_id", "attempt_id"):
        if not isinstance(envelope.get(key), str) or not envelope[key]:
            raise ValueError(f"envelope.{key} must be a non-empty string")
    route = envelope.get("route")
    if route is not None and route not in ROUTES:
        raise ValueError("envelope.route must be route_a or route_b")
    if ("bundle" in envelope) == ("bundle_path" in envelope):
        raise ValueError("envelope needs exactly one of bundle or bundle_path")
    return envelope


def _bundle_from_envelope(envelope: dict[str, Any], path: Path) -> Any:
    if "bundle" in envelope:
        return envelope["bundle"]
    value = envelope["bundle_path"]
    if not isinstance(value, str) or not value:
        raise ValueError("bundle_path must be a non-empty relative path")
    raw_path = Path(value)
    if raw_path.anchor or ".." in raw_path.parts:
        raise ValueError("bundle_path must be relative and contain no parent component")
    base = path.parent.resolve()
    candidate = base
    for piece in raw_path.parts:
        candidate = candidate / piece
        if candidate.is_symlink():
            raise ValueError("bundle_path must not contain a symlink")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("bundle_path escapes the inbox directory") from exc
    return json.loads(resolved.read_text(encoding="utf-8"))


def _move(path: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / path.name
    if target.exists():
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        target = directory / f"{path.stem}.{stamp}{path.suffix}"
    os.replace(path, target)
    return target


def process_once(*, data_dir: Path, inbox: Path, trigger: bool = True) -> dict[str, int]:
    processed_dir = inbox / "processed"
    failed_dir = inbox / "failed"
    inbox.mkdir(parents=True, exist_ok=True)
    imported = 0
    failed = 0
    changed = False
    for path in sorted(inbox.glob("*.json")):
        try:
            envelope = _read_envelope(path)
            bundle = _bundle_from_envelope(envelope, path)
            result = import_bundle(
                data_dir=data_dir,
                job_id=envelope["job_id"],
                run_id=envelope["run_id"],
                attempt_id=envelope["attempt_id"],
                bundle=bundle,
                route=envelope.get("route"),
                trigger=False,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failed += 1
            target = _move(path, failed_dir)
            (target.parent / f"{target.name}.error.txt").write_text(str(exc), encoding="utf-8")
        else:
            imported += 1
            changed = changed or result["changed"]
            _move(path, processed_dir)
    worker_processed = worker.run_once(data_dir=data_dir) if trigger and changed else 0
    return {"imported": imported, "failed": failed, "worker_processed": worker_processed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools/host_broker.py")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--inbox", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=2.0)
    parser.add_argument("--no-trigger", action="store_true")
    args = parser.parse_args(argv)

    while True:
        summary = process_once(
            data_dir=args.data_dir,
            inbox=args.inbox,
            trigger=not args.no_trigger,
        )
        print(json.dumps(summary, sort_keys=True))
        if args.once:
            return 0 if summary["failed"] == 0 else 1
        time.sleep(max(0.2, args.poll))


if __name__ == "__main__":
    raise SystemExit(main())
