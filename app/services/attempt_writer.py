"""Write one worker attempt bundle from an executor result.

Two modes:

* ``--input result.json``: a generic map with ``candidate_sha256``,
  ``results`` and optional ``evidence`` / ``details``. This is the adapter
  target for Route A executors that already produced their own bundle.
* ``--routeb-bundle <dir>``: map a `routeb/executor.py` static bundle to V0-V5
  without inventing a pass. Runtime checks without ``manifest-compare.json``
  stay ``not_run``.

The output path defaults to ``BINARYREPAIR_RESULT_PATH`` and identifiers default
to the environment the worker passes to ``BINARYREPAIR_ATTEMPT_CMD``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.db.connection import connect

ROUTES = ("route_a", "route_b")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _default(value: str | None, env: str) -> str:
    resolved = value or os.environ.get(env)
    if not resolved:
        raise SystemExit(f"missing --{env.lower().replace('binaryrepair_', '').replace('_', '-')} / {env}")
    return resolved


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    return path


def _register(
    *,
    data_dir: Path,
    job_id: str,
    attempt_id: str,
    source: Path,
    role: str,
) -> str:
    if not source.is_file():
        raise SystemExit(f"evidence file does not exist: {source}")
    artifact_id = uuid.uuid4().hex
    relative = f"attempts/{attempt_id}/{artifact_id}-{source.name}"
    target = data_dir / "jobs" / job_id / "artifacts" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    digest = _sha256(target)
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute(
                "INSERT INTO artifacts "
                "(id, job_id, attempt_id, relative_path, sha256, size_bytes, media_type, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'application/octet-stream', ?, ?)",
                (
                    artifact_id,
                    job_id,
                    attempt_id,
                    relative,
                    digest,
                    target.stat().st_size,
                    role,
                    "2026-10-22T00:00:00+00:00",
                ),
            )
    finally:
        connection.close()
    return artifact_id


def _generic(
    input_path: Path,
    *,
    route: str,
    candidate: str | None,
    data_dir: Path | None,
    job_id: str | None,
    attempt_id: str | None,
) -> dict[str, Any]:
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read input bundle {input_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("input bundle must be an object")
    body_route = payload.get("route")
    if body_route is not None and body_route != route:
        raise SystemExit(f"input route {body_route!r} does not match --route {route!r}")
    results = payload.get("results")
    if not isinstance(results, dict):
        raise SystemExit("input bundle results must be an object")
    candidate_sha = candidate or payload.get("candidate_sha256")
    if not isinstance(candidate_sha, str) or len(candidate_sha) != 64:
        raise SystemExit("candidate_sha256 must be a 64-character hex digest")

    evidence = dict(payload.get("evidence") or {})
    evidence_files = payload.get("evidence_files") or {}
    if not isinstance(evidence_files, dict):
        raise SystemExit("evidence_files must be an object")
    if evidence_files:
        if data_dir is None or job_id is None or attempt_id is None:
            raise SystemExit(
                "evidence_files requires --data-dir, --job-id and --attempt-id"
            )
        for gate, raw_path in evidence_files.items():
            if gate in evidence:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = input_path.parent / path
            evidence[gate] = _register(
                data_dir=data_dir,
                job_id=job_id,
                attempt_id=attempt_id,
                source=path,
                role=f"route_{route}_evidence",
            )

    return {
        "route": route,
        "candidate_sha256": candidate_sha,
        "results": results,
        "evidence": evidence,
        "details": payload.get("details") or {},
    }


def _job_original_sha256(data_dir: Path, job_id: str) -> str | None:
    """Return the job's recorded original hash, or None when the job is absent."""
    connection = connect(data_dir)
    try:
        row = connection.execute(
            "SELECT original_sha256 FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return row["original_sha256"]


def _routeb(
    bundle: Path,
    *,
    data_dir: Path,
    job_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    candidate = bundle / "candidate.elf"
    audit = bundle / "audit.json"
    compare = bundle / "manifest-compare.json"
    manifest_path = bundle / "edit-manifest.json"
    original = bundle / "original.elf"
    if not candidate.is_file():
        raise SystemExit(f"routeb bundle has no candidate.elf: {bundle}")

    results: dict[str, str] = {
        "V0": "not_run",
        "V1": "not_run",
        "V2": "not_run",
        "V3": "not_run",
        "V4": "not_run",
        "V5": "not_run",
    }
    details: dict[str, str] = {}
    evidence: dict[str, str] = {}
    candidate_sha = _sha256(candidate)
    job_original = _job_original_sha256(data_dir, job_id)

    # V0 identity: pass only when the bundle declares an original hash that
    # matches the job record and the candidate bytes match the declared candidate.
    # A bundle that does not carry that evidence stays `not_run`; existence of a
    # candidate.elf is not identity proof (missing is not passing).
    declared_original: str | None = None
    declared_candidate: str | None = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            manifest = None
        if isinstance(manifest, dict):
            value = manifest.get("original_sha256")
            if isinstance(value, str) and len(value) == 64:
                declared_original = value
            value = manifest.get("candidate_sha256")
            if isinstance(value, str) and len(value) == 64:
                declared_candidate = value
    elif original.is_file():
        declared_original = _sha256(original)

    if declared_original is None:
        details["V0"] = "identity not confirmed: bundle provides no original hash"
    elif declared_candidate is not None and declared_candidate != candidate_sha:
        results["V0"] = "failed"
        details["V0"] = "candidate.elf hash does not match edit-manifest.json"
    elif job_original is None:
        details["V0"] = "identity not confirmed: job record not found"
    elif declared_original != job_original:
        results["V0"] = "failed"
        details["V0"] = f"original {declared_original} does not match job {job_original}"
    else:
        results["V0"] = "passed"
        details["V0"] = f"original {declared_original} matches the job record"

    if audit.is_file():
        try:
            audit_data = json.loads(audit.read_text(encoding="utf-8"))
        except ValueError:
            audit_data = {}
        passed = bool(audit_data.get("pass")) if isinstance(audit_data, dict) else False
        results["V1"] = "passed" if passed else "failed"
        details["V1"] = "routeb static audit"
        if passed:
            evidence["V1"] = _register(
                data_dir=data_dir,
                job_id=job_id,
                attempt_id=attempt_id,
                source=audit,
                role="routeb_audit",
            )

    if compare.is_file():
        try:
            compare_data = json.loads(compare.read_text(encoding="utf-8"))
        except ValueError:
            compare_data = {}
        passed = bool(
            compare_data.get("gate4_pass")
            or compare_data.get("pass")
            or compare_data.get("passed")
        ) if isinstance(compare_data, dict) else False
        results["V3"] = "passed" if passed else "failed"
        details["V3"] = "routeb manifest-compare (runtime execution)"
        if passed:
            evidence["V3"] = _register(
                data_dir=data_dir,
                job_id=job_id,
                attempt_id=attempt_id,
                source=compare,
                role="routeb_manifest_compare",
            )
    else:
        details["V3"] = "static routeb bundle has no runtime manifest; Gate 4 not claimed"

    return {
        "route": "route_b",
        "candidate_sha256": _sha256(candidate),
        "results": results,
        "evidence": evidence,
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.attempt_writer")
    parser.add_argument("--input", type=Path, help="generic result JSON")
    parser.add_argument("--routeb-bundle", type=Path, help="routeb/executor.py bundle dir")
    parser.add_argument("--data-dir", default=os.environ.get("BINARYREPAIR_DATA_DIR"))
    parser.add_argument("--job-id", default=os.environ.get("BINARYREPAIR_JOB_ID"))
    parser.add_argument("--run-id", default=os.environ.get("BINARYREPAIR_RUN_ID"))
    parser.add_argument("--attempt-id", default=os.environ.get("BINARYREPAIR_ATTEMPT_ID"))
    parser.add_argument("--route", default=os.environ.get("BINARYREPAIR_ROUTE"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--candidate-sha256")
    args = parser.parse_args(argv)

    if args.out is None:
        out = os.environ.get("BINARYREPAIR_RESULT_PATH")
        if not out:
            raise SystemExit("missing --out / BINARYREPAIR_RESULT_PATH")
        args.out = Path(out)

    route = args.route or ("route_b" if args.routeb_bundle else None)
    if route not in ROUTES:
        raise SystemExit("route must be route_a or route_b")

    if args.routeb_bundle:
        data_dir = Path(_default(args.data_dir, "BINARYREPAIR_DATA_DIR"))
        job_id = _default(args.job_id, "BINARYREPAIR_JOB_ID")
        attempt_id = _default(args.attempt_id, "BINARYREPAIR_ATTEMPT_ID")
        payload = _routeb(
            args.routeb_bundle,
            data_dir=data_dir,
            job_id=job_id,
            attempt_id=attempt_id,
        )
    elif args.input:
        data_dir = Path(args.data_dir) if args.data_dir else None
        payload = _generic(
            args.input,
            route=route,
            candidate=args.candidate_sha256,
            data_dir=data_dir,
            job_id=args.job_id,
            attempt_id=args.attempt_id,
        )
    else:
        raise SystemExit("provide --input or --routeb-bundle")

    path = _write(args.out, payload)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
