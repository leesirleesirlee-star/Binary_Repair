#!/usr/bin/env python3
"""Synthetic controller -> worker -> p2 pipeline (never a real gate result).

Creates a disposable job with both routes' V0-V5 marked passed and valid
artifact ids, runs the worker, then runs p2_acceptance in offline mode with
`synthetic: true`. This proves the pipe; it does not prove a repair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import connect
from app.services import jobs, worker
from tools import p2_acceptance


def _artifacts(data_dir: Path, job_id: str, attempt_id: str, route: str) -> dict[str, str]:
    evidence: dict[str, str] = {}
    directory = data_dir / "jobs" / job_id / "synthetic" / attempt_id
    directory.mkdir(parents=True, exist_ok=True)
    connection = connect(data_dir)
    try:
        with connection:
            for index in range(6):
                artifact_id = f"synth-{attempt_id}-V{index}"
                name = f"V{index}.json"
                path = directory / name
                path.write_text(json.dumps({"gate": f"V{index}", "synthetic": True}),
                                encoding="utf-8")
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                connection.execute(
                    "INSERT OR REPLACE INTO artifacts "
                    "(id, job_id, attempt_id, relative_path, sha256, size_bytes, media_type, role, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'application/json', 'evidence', ?)",
                    (artifact_id, job_id, attempt_id,
                     f"jobs/{job_id}/synthetic/{attempt_id}/{name}",
                     digest, path.stat().st_size,
                     datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
                evidence[f"V{index}"] = artifact_id
    finally:
        connection.close()
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthetic_pipeline.py")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="binaryrepair-synthetic-"))
    data_dir.mkdir(parents=True, exist_ok=True)

    record = jobs.create_job(
        original_bytes=b"\x7fELF synthetic pipeline",
        filename="synthetic.elf",
        defect_description="synthetic pipeline only",
        data_dir=data_dir,
    )
    meta = data_dir / "jobs" / record.id / "meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"synthetic": True}), encoding="utf-8")
    approval = jobs.approve_contract(
        record.id, contract_revision=1, reviewer_label="synthetic",
        data_dir=data_dir, input_sha256=record.original_sha256,
        manifest_sha256="1" * 64,
    )
    run = jobs.start_run(record.id, approval=approval,
                         idempotency_key="synthetic-pipeline", data_dir=data_dir)
    for attempt in jobs.list_attempts(run.id, data_dir=data_dir):
        evidence = _artifacts(data_dir, record.id, attempt.id, attempt.route)
        payload = {
            "route": attempt.route,
            "candidate_sha256": record.original_sha256,
            "results": {f"V{i}": "passed" for i in range(6)},
            "evidence": evidence,
            "details": {f"V{i}": "synthetic only" for i in range(6)},
        }
        path = (data_dir / "jobs" / record.id / "runs" / run.id / "attempts" /
                f"{attempt.id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    processed = worker.run_once(data_dir=data_dir)
    rc = p2_acceptance.main([
        "--job-id", record.id, "--data-dir", str(data_dir), "--synthetic",
    ])
    print(json.dumps({
        "synthetic": True,
        "data_dir": str(data_dir),
        "job_id": record.id,
        "run_id": run.id,
        "worker_processed": processed,
        "p2_rc": rc,
    }, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
