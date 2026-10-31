#!/usr/bin/env python3
"""Route A result-bundle (gates.V0..V5) -> worker attempt JSON.

Evidence files are copied into the job artifact store and replaced by artifact
ids. A `passed` gate without evidence is rejected rather than written as a
false pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from app.db.connection import connect

STATUS_MAP = {
    "pass": "passed",
    "passed": "passed",
    "fail": "failed",
    "failed": "failed",
    "not_run": "not_run",
    "inconclusive": "unknown",
    "unsupported": "not_run",
    "unknown": "unknown",
    "timed_out": "timed_out",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register(*, data_dir: Path, job_id: str, attempt_id: str, source: Path) -> str:
    if not source.is_file():
        raise ValueError(f"evidence file missing: {source}")
    artifact_id = uuid.uuid4().hex
    relative = f"attempts/{attempt_id}/{artifact_id}-{source.name}"
    target = data_dir / "jobs" / job_id / "artifacts" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute(
                "INSERT INTO artifacts "
                "(id, job_id, attempt_id, relative_path, sha256, size_bytes, media_type, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'application/octet-stream', 'route_a_evidence', ?)",
                (artifact_id, job_id, attempt_id, relative, _sha(target),
                 target.stat().st_size, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
    finally:
        connection.close()
    return artifact_id


def convert(
    bundle: dict[str, Any],
    *,
    evidence_root: Path | None = None,
    data_dir: Path | None = None,
    job_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be an object")
    if bundle.get("route") not in ("A", "a", "route_a"):
        raise ValueError("not a Route A bundle")
    identity = bundle.get("identity") or {}
    candidate = identity.get("candidate_sha256")
    if not isinstance(candidate, str) or len(candidate) != 64:
        raise ValueError("identity.candidate_sha256 must be 64 hex characters")
    int(candidate, 16)
    gates = bundle.get("gates")
    if not isinstance(gates, dict):
        raise ValueError("gates must be an object")

    results: dict[str, str] = {}
    evidence: dict[str, str] = {}
    details: dict[str, str] = {}
    for gate in ("V0", "V1", "V2", "V3", "V4", "V5"):
        entry = gates.get(gate) or {}
        raw = str(entry.get("status") or "not_run").lower()
        if raw not in STATUS_MAP:
            raise ValueError(f"unknown {gate} status {raw!r}")
        result = STATUS_MAP[raw]
        results[gate] = result
        paths = []
        for item in entry.get("evidence") or []:
            rel = item.get("relative_path") if isinstance(item, dict) else None
            if rel:
                paths.append(Path(rel))
        # Real worker conversion requires evidence for every passed gate
        # (including V0); preview mode may show a V0 result without artifacts.
        if result == "passed" and not paths and (gate != "V0" or data_dir is not None):
            raise ValueError(f"{gate} passed without evidence; refusing false pass")
        if paths and data_dir is None:
            # Preview mode: no DB registration was requested. Keep the relative
            # paths so the caller can inspect the mapping; worker writes still
            # require --data-dir/--job-id/--attempt-id to register artifacts.
            evidence[gate] = ", ".join(str(rel) for rel in paths)
            paths = []
        sources = []
        root = evidence_root or Path(".")
        for rel in paths:
            source = (root / rel).resolve()
            if not source.is_file():
                raise ValueError(f"{gate} evidence missing: {source}")
            sources.append(source)
        if sources:
            if job_id is None or attempt_id is None:
                raise ValueError("--job-id/--attempt-id required to register evidence")
            if len(sources) == 1:
                evidence[gate] = _register(data_dir=data_dir, job_id=job_id,
                                           attempt_id=attempt_id, source=sources[0])
            else:
                with tempfile.TemporaryDirectory(prefix=f"routea-{gate}-") as tmp:
                    archive = Path(tmp) / f"{gate}-evidence.zip"
                    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                        for source in sources:
                            zf.write(source, source.name)
                    evidence[gate] = _register(data_dir=data_dir, job_id=job_id,
                                               attempt_id=attempt_id, source=archive)
        missing = entry.get("missing") or []
        if missing:
            details[gate] = "; ".join(str(x) for x in missing)
        elif entry.get("details"):
            details[gate] = json.dumps(entry["details"], sort_keys=True)
    return {
        "route": "route_a",
        "candidate_sha256": candidate,
        "results": results,
        "evidence": evidence,
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools/routea_bundle_to_attempt.py")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--attempt-id")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.evidence_root or args.bundle.parent
    payload = convert(
        json.loads(args.bundle.read_text(encoding="utf-8")),
        evidence_root=root,
        data_dir=args.data_dir,
        job_id=args.job_id,
        attempt_id=args.attempt_id,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "results": payload["results"],
                      "evidence": sorted(payload["evidence"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
