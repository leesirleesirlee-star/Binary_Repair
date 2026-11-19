"""Host importer/broker tests: atomic import + worker trigger, fail closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import jobs
from tests.support_typed_contract import sample_typed_contract
from tools.host_broker import process_once
from tools.host_importer import import_bundle


def _started(tmp_path: Path):
    data_dir = tmp_path / "var"
    record = jobs.create_job(
        original_bytes=b"\x7fELF broker bytes",
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
        contract_body=sample_typed_contract(),
    )
    run = jobs.start_run(
        record.id,
        approval=approval,
        idempotency_key="broker-1",
        data_dir=data_dir,
    )
    return data_dir, record, run


def _bundle(record, attempt) -> dict:
    return {
        "route": attempt.route,
        "candidate_sha256": record.original_sha256,
        "results": {f"V{i}": "not_run" for i in range(6)},
    }


def test_broker_imports_atomically_and_triggers_worker(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    attempt = next(a for a in jobs.list_attempts(run.id, data_dir=data_dir) if a.route == "route_a")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    envelope = {
        "job_id": record.id,
        "run_id": run.id,
        "attempt_id": attempt.id,
        "route": attempt.route,
        "bundle": _bundle(record, attempt),
    }
    (inbox / "one.json").write_text(json.dumps(envelope), encoding="utf-8")

    summary = process_once(data_dir=data_dir, inbox=inbox, trigger=True)
    assert summary == {"imported": 1, "failed": 0, "worker_processed": 1}

    target = data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / f"{attempt.id}.json"
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8"))["results"]["V0"] == "not_run"
    assert (inbox / "processed" / "one.json").is_file()
    assert not list(inbox.glob("*.tmp"))

    # Idempotent import: same bytes do not rewrite, worker is not triggered.
    duplicate = import_bundle(
        data_dir=data_dir,
        job_id=record.id,
        run_id=run.id,
        attempt_id=attempt.id,
        bundle=_bundle(record, attempt),
        trigger=False,
    )
    assert duplicate["changed"] is False
    finished = jobs.get_attempt(attempt.id, data_dir=data_dir)
    assert finished is not None
    assert finished.outcome == "inconclusive"


def test_broker_rejects_wrong_route_without_writing(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    attempt = next(a for a in jobs.list_attempts(run.id, data_dir=data_dir) if a.route == "route_b")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    envelope = {
        "job_id": record.id,
        "run_id": run.id,
        "attempt_id": attempt.id,
        "route": "route_a",  # wrong on purpose
        "bundle": {
            "route": "route_a",
            "candidate_sha256": record.original_sha256,
            "results": {f"V{i}": "not_run" for i in range(6)},
        },
    }
    (inbox / "bad.json").write_text(json.dumps(envelope), encoding="utf-8")

    summary = process_once(data_dir=data_dir, inbox=inbox, trigger=True)
    assert summary["imported"] == 0
    assert summary["failed"] == 1
    assert summary["worker_processed"] == 0
    assert (inbox / "failed" / "bad.json").is_file()
    target = data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / f"{attempt.id}.json"
    assert not target.exists()
    assert jobs.get_attempt(attempt.id, data_dir=data_dir).outcome is None


def test_broker_rejects_bundle_path_escape(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    attempt = next(a for a in jobs.list_attempts(run.id, data_dir=data_dir) if a.route == "route_a")
    inbox = tmp_path / "inbox"; inbox.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_bundle(record, attempt)))
    envelope = {
        "job_id": record.id, "run_id": run.id, "attempt_id": attempt.id,
        "route": attempt.route, "bundle_path": "../outside.json",
    }
    (inbox / "escape.json").write_text(json.dumps(envelope), encoding="utf-8")
    summary = process_once(data_dir=data_dir, inbox=inbox, trigger=False)
    assert summary["imported"] == 0
    assert summary["failed"] == 1


@pytest.mark.parametrize("mismatch", ["job_id", "run_id", "original_sha256", "candidate_sha256", "registered_bytes"])
def test_broker_rejects_wrong_binding_or_digest_without_publishing(tmp_path: Path, mismatch: str) -> None:
    data_dir, record, run = _started(tmp_path)
    attempt = jobs.list_attempts(run.id, data_dir=data_dir)[0]
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    bundle = _bundle(record, attempt)
    envelope = {
        "job_id": record.id, "run_id": run.id, "attempt_id": attempt.id,
        "route": attempt.route, "bundle": bundle,
    }
    if mismatch in {"job_id", "run_id"}:
        other = jobs.create_job(
            original_bytes=b"another inert fixture", filename="other.bin",
            defect_description="Another synthetic task.", data_dir=data_dir,
        )
        approval = jobs.approve_contract(
            other.id, contract_revision=1, reviewer_label="synthetic", data_dir=data_dir,
            input_sha256=other.original_sha256, manifest_sha256="1" * 64,
        )
        other_run = jobs.start_run(other.id, approval=approval, idempotency_key="other", data_dir=data_dir)
        envelope[mismatch] = other.id if mismatch == "job_id" else other_run.id
    elif mismatch == "original_sha256":
        bundle[mismatch] = "0" * 64
    else:
        relative = f"jobs/{record.id}/synthetic-candidate"
        candidate = data_dir / relative
        candidate.write_bytes(b"inert evidence bytes")
        artifact = jobs.add_artifact(
            record.id, relative_path=relative, role="v0_evidence",
            attempt_id=attempt.id, data_dir=data_dir,
        )
        bundle["results"]["V0"] = "passed"
        bundle["evidence"] = {"V0": artifact["id"]}
        bundle["candidate_sha256"] = "f" * 64 if mismatch == "candidate_sha256" else artifact["sha256"]
        if mismatch == "registered_bytes":
            candidate.write_bytes(b"changed inert evidence bytes")

    before = jobs.list_artifacts(record.id, data_dir=data_dir)
    (inbox / "mismatch.json").write_text(json.dumps(envelope), encoding="utf-8")
    assert process_once(data_dir=data_dir, inbox=inbox, trigger=False) == {
        "imported": 0, "failed": 1, "worker_processed": 0,
    }
    assert (inbox / "failed" / "mismatch.json").is_file()
    assert not (data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / f"{attempt.id}.json").exists()
    assert jobs.list_artifacts(record.id, data_dir=data_dir) == before
    assert jobs.get_attempt(attempt.id, data_dir=data_dir).outcome is None
