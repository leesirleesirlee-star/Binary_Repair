"""Integration tests for the queued-run worker."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.controller_api import app
from app.db.connection import connect
from app.services import jobs, worker


def _started(tmp_path: Path):
    data_dir = tmp_path / "var"
    record = jobs.create_job(
        original_bytes=b"\x7fELF worker bytes",
        filename="sample.elf",
        defect_description="stack buffer overflow",
        data_dir=data_dir,
    )
    approval = jobs.approve_contract(
        record.id,
        contract_revision=1,
        reviewer_label="test",
        data_dir=data_dir,
        input_sha256=record.original_sha256,
        manifest_sha256="1" * 64,
    )
    run = jobs.start_run(
        record.id,
        approval=approval,
        idempotency_key="worker-1",
        data_dir=data_dir,
    )
    return data_dir, record, run


def _bundle_path(data_dir: Path, job_id: str, run_id: str, attempt_id: str) -> Path:
    return data_dir / "jobs" / job_id / "runs" / run_id / "attempts" / f"{attempt_id}.json"


def _add_evidence(data_dir: Path, job_id: str, attempt_id: str, count: int = 5) -> dict[str, str]:
    evidence: dict[str, str] = {}
    connection = connect(data_dir)
    try:
        with connection:
            for index in range(1, count + 1):
                artifact_id = f"art-{attempt_id}-{index}"
                connection.execute(
                    "INSERT INTO artifacts "
                    "(id, job_id, attempt_id, relative_path, sha256, size_bytes, media_type, role, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'application/octet-stream', 'evidence', ?)",
                    (
                        artifact_id,
                        job_id,
                        attempt_id,
                        f"evidence/{attempt_id}/{index}.bin",
                        str(index) * 64,
                        index,
                        "2026-10-22T00:00:00+00:00",
                    ),
                )
                evidence[f"V{index}"] = artifact_id
    finally:
        connection.close()
    return evidence


def _write_bundle(
    data_dir: Path,
    job_id: str,
    run_id: str,
    attempt,
    record,
) -> None:
    evidence = _add_evidence(data_dir, job_id, attempt.id)
    payload = {
        "route": attempt.route,
        "candidate_sha256": record.original_sha256,
        "results": {f"V{i}": "passed" for i in range(6)},
        "evidence": evidence,
        "details": {f"V{i}": f"checked V{i}" for i in range(6)},
    }
    path = _bundle_path(data_dir, job_id, run_id, attempt.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_worker_finishes_both_routes_and_persists_verdict(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    attempts = jobs.list_attempts(run.id, data_dir=data_dir)
    for attempt in attempts:
        _write_bundle(data_dir, record.id, run.id, attempt, record)

    processed = worker.run_once(data_dir=data_dir)
    assert processed == 2

    finished = jobs.list_attempts(run.id, data_dir=data_dir)
    assert {item.phase for item in finished} == {"finished"}
    assert {item.outcome for item in finished} == {"success_within_scope"}
    run_after = jobs.get_run(run.id, data_dir=data_dir)
    assert run_after is not None
    assert run_after.phase == "finished"
    assert jobs.get_job(record.id, data_dir=data_dir).phase == "finished"

    connection = connect(data_dir)
    try:
        counts = connection.execute(
            "SELECT attempt_id, COUNT(*) AS n FROM obligations GROUP BY attempt_id"
        ).fetchall()
        run_row = connection.execute(
            "SELECT outcome, reason, failing_obligation, detail FROM runs WHERE id = ?",
            (run.id,),
        ).fetchone()
    finally:
        connection.close()
    assert {row["n"] for row in counts} == {6}
    assert run_row["outcome"] == "success_within_scope"
    assert (data_dir / "jobs" / record.id / "run-verdict.json").exists()
    for attempt in finished:
        assert (
            data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / attempt.id / "verdict.json"
        ).exists()


def test_snapshot_exposes_attempts_obligations_and_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    data_dir, record, run = _started(tmp_path)
    monkeypatch.setenv("BINARYREPAIR_DATA_DIR", str(data_dir))
    attempts = jobs.list_attempts(run.id, data_dir=data_dir)
    for attempt in attempts:
        _write_bundle(data_dir, record.id, run.id, attempt, record)
    worker.run_once(data_dir=data_dir)

    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get(f"/api/jobs/{record.id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["verdict"]["outcome"] == "success_within_scope"
    assert payload["run"]["id"] == run.id
    assert payload["routes"]["A"]["outcome"] == "success_within_scope"
    assert payload["routes"]["B"]["outcome"] == "success_within_scope"
    assert payload["routes"]["A"]["verdict"]["outcome"] == "success_within_scope"
    assert len(payload["routes"]["A"]["obligations"]) == 6
    assert all(stage["state"] == "done" for stage in payload["routes"]["A"]["stages"])


def test_worker_leaves_queued_when_no_bundle(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    processed = worker.run_once(data_dir=data_dir)
    assert processed == 0
    run_after = jobs.get_run(run.id, data_dir=data_dir)
    assert run_after is not None
    assert run_after.phase != "finished"
    assert {item.outcome for item in jobs.list_attempts(run.id, data_dir=data_dir)} == {None}
