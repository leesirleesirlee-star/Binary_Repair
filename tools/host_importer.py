#!/usr/bin/env python3
"""Atomically import one executor result bundle into the controller store.

The broker (``tools/host_broker.py``) uses :func:`import_bundle`; the CLI is
useful for a single manual import.

A bundle is written to::

    <data>/jobs/<job_id>/runs/<run_id>/attempts/<attempt_id>.json

The write is atomic (temp file + os.replace), idempotent for identical bytes,
and refuses to overwrite a different existing bundle. After a successful import
the worker is triggered in-process unless ``--no-trigger`` is set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import connect
from app.services import worker

ROUTES = {"route_a", "route_b"}
STATUSES = {"passed", "failed", "not_run", "unknown", "timed_out"}
SHA256 = re.compile(r"[0-9a-f]{64}")


def _validate_bundle(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a JSON object")
    route = bundle.get("route")
    if route is not None and route not in ROUTES:
        raise ValueError("bundle route must be route_a or route_b")
    candidate = bundle.get("candidate_sha256")
    if not isinstance(candidate, str) or SHA256.fullmatch(candidate) is None:
        raise ValueError("bundle candidate_sha256 must be 64 lowercase hex characters")
    results = bundle.get("results")
    if not isinstance(results, dict):
        raise ValueError("bundle results must be an object")
    for gate, status in results.items():
        if gate not in {f"V{i}" for i in range(6)}:
            raise ValueError(f"unknown result gate: {gate}")
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError(f"unknown result status for {gate}: {status!r}")
    evidence = bundle.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise ValueError("bundle evidence must be an object")
    return bundle


def _target_path(data_dir: Path, job_id: str, run_id: str, attempt_id: str) -> Path:
    return data_dir / "jobs" / job_id / "runs" / run_id / "attempts" / f"{attempt_id}.json"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@dataclass
class ResultReceipt:
    connection: sqlite3.Connection
    data_dir: Path
    binding: dict[str, Any]
    created: list[Path] = field(default_factory=list)

    def validate_binding(self, payload: dict[str, Any]) -> None:
        for key in (
            "job_id", "run_id", "attempt_id", "route", "original_sha256",
            "manifest_sha256", "reviewed_scope_sha256",
        ):
            if key in payload and payload[key] != self.binding[key]:
                raise ValueError(f"bundle {key} does not match the persisted attempt binding")

    def store(self, bundle: dict[str, Any]) -> dict[str, Any]:
        payload = _validate_bundle(bundle)
        self.validate_binding(payload)
        self._verify_evidence(payload)
        identity = {key: self.binding[key] for key in ("job_id", "run_id", "attempt_id", "route")}
        target = _target_path(self.data_dir, identity["job_id"], identity["run_id"], identity["attempt_id"])
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError("a different bundle already exists for this attempt")
            changed = False
        else:
            if self.binding["attempt_phase"] in {"finished", "cancelled"}:
                raise ValueError("attempt no longer accepts results")
            _atomic_write(target, raw)
            self.created.append(target)
            changed = True
        return {**identity, "path": str(target), "changed": changed}

    def _verify_evidence(self, payload: dict[str, Any]) -> None:
        evidence = payload.get("evidence") or {}
        for gate, status in payload["results"].items():
            if status == "passed" and gate not in evidence:
                raise ValueError(f"gate {gate} passed without registered attempt evidence")
        root = self.data_dir.resolve()
        for gate, artifact in evidence.items():
            if gate not in payload["results"] or not isinstance(artifact, str):
                raise ValueError("bundle evidence must map declared gates to registered artifact IDs")
            row = self.connection.execute(
                "SELECT relative_path, sha256 FROM artifacts "
                "WHERE id = ? AND job_id = ? AND attempt_id = ?",
                (artifact, self.binding["job_id"], self.binding["attempt_id"]),
            ).fetchone()
            if row is None:
                raise ValueError(f"gate {gate} evidence does not belong to the attempt")
            expected = row["sha256"]
            if gate == "V0" and payload["results"][gate] == "passed" and expected != payload["candidate_sha256"]:
                raise ValueError("gate V0 candidate hash does not match registered attempt evidence")
            path = (root / row["relative_path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"gate {gate} registered evidence is unavailable")
            with path.open("rb") as handle:
                observed = hashlib.file_digest(handle, "sha256").hexdigest()
            if observed != expected:
                raise ValueError(f"gate {gate} evidence bytes do not match the registered hash")


@contextmanager
def receive_result(*, data_dir: Path, job_id: str, run_id: str, attempt_id: str, route: str | None):
    """Hold the run-state write lock through evidence publication and rollback."""
    with closing(connect(data_dir)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT a.id AS attempt_id, a.route, a.phase AS attempt_phase,
                   r.id AS run_id, r.job_id, r.phase, r.manifest_sha256, r.reviewed_scope_sha256,
                   j.original_sha256, j.cancel_requested
            FROM attempts AS a JOIN runs AS r ON r.id = a.run_id
            JOIN jobs AS j ON j.id = r.job_id WHERE a.id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown attempt id")
        if row["run_id"] != run_id:
            raise ValueError("attempt does not belong to the supplied run")
        if row["job_id"] != job_id:
            raise ValueError("run does not belong to the supplied job")
        if route is not None and row["route"] != route:
            raise ValueError("route does not match the persisted attempt")
        if row["cancel_requested"] or row["phase"] in {"cancelling", "cancelled", "finished"}:
            raise ValueError("run no longer accepts results")
        receipt = ResultReceipt(connection, data_dir, dict(row))
        try:
            yield receipt
            connection.commit()
        except BaseException:
            for path in reversed(receipt.created):
                path.unlink(missing_ok=True)
            raise


def import_bundle(
    *,
    data_dir: str | Path,
    job_id: str,
    run_id: str,
    attempt_id: str,
    bundle: Any,
    route: str | None = None,
    trigger: bool = True,
) -> dict[str, Any]:
    """Import one bundle and optionally trigger the worker.

    Raises ValueError without writing anything when the attempt/run/job binding
    or the bundle itself is invalid.
    """

    data_dir = Path(data_dir)
    with receive_result(
        data_dir=data_dir, job_id=job_id, run_id=run_id, attempt_id=attempt_id, route=route,
    ) as receipt:
        result = receipt.store(bundle)
    result["worker_processed"] = worker.run_once(data_dir=data_dir) if trigger and result["changed"] else 0
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools/host_importer.py")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--route", choices=sorted(ROUTES))
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--no-trigger", action="store_true")
    args = parser.parse_args(argv)

    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        result = import_bundle(
            data_dir=args.data_dir,
            job_id=args.job_id,
            run_id=args.run_id,
            attempt_id=args.attempt_id,
            bundle=bundle,
            route=args.route,
            trigger=not args.no_trigger,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"host_importer: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
