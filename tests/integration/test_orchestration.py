"""Contract tests for the V0-V5 -> Verdict orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.connection import connect
from app.services import jobs, orchestration


def _started(tmp_path: Path):
    data_dir = tmp_path / "var"
    record = jobs.create_job(
        original_bytes=b"\x7fELF orchestration bytes",
        filename="sample.elf",
        defect_description="stack buffer overflow in copy_input",
        data_dir=data_dir,
    )
    approval = jobs.approve_contract(
        record.id,
        input_sha256=record.original_sha256,
        contract_revision=1,
        manifest_sha256="1" * 64,
        scope_sha256="2" * 64,
        reviewer_label="test",
        data_dir=data_dir,
    )
    run = jobs.start_run(
        record.id,
        approval=approval,
        idempotency_key="run-1",
        data_dir=data_dir,
    )
    return data_dir, record, run


def test_missing_gates_become_inconclusive_not_success(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    verdict = orchestration.evaluate_attempt(
        record.id,
        run.id,
        results={"V0": "passed"},
        data_dir=data_dir,
        candidate_sha256=record.original_sha256,
    )
    assert verdict.outcome.value == "inconclusive"
    assert verdict.reason.value == "required_check_not_run"

    path = data_dir / "jobs" / record.id / "runs" / run.id / "verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["obligations"]) == 6
    assert payload["verdict"]["outcome"] == "inconclusive"
    assert payload["verdict"]["failing_obligation"] == "structure"


def test_all_v0_v5_passed_is_success(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    results = {gate: "passed" for gate, _, _ in orchestration.GATES}
    verdict = orchestration.evaluate_attempt(
        record.id,
        run.id,
        results=results,
        data_dir=data_dir,
        candidate_sha256=record.original_sha256,
    )
    assert verdict.outcome.value == "success_within_scope"


def test_candidate_differing_from_original_can_succeed(tmp_path: Path) -> None:
    """Approval does not bind the original input hash.

    A real repair candidate normally differs from the original binary. With
    the approval currently binding only the reviewed scope, a candidate that
    differs from ``job.original_sha256`` and passes V0-V5 is a success.
    """
    data_dir, record, run = _started(tmp_path)
    candidate = "9" * 64
    assert candidate != record.original_sha256
    results = {gate: "passed" for gate, _, _ in orchestration.GATES}
    verdict = orchestration.evaluate_attempt(
        record.id,
        run.id,
        results=results,
        data_dir=data_dir,
        candidate_sha256=candidate,
    )
    assert verdict.outcome.value == "success_within_scope"
    assert verdict.reason is None

    path = data_dir / "jobs" / record.id / "runs" / run.id / "verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["expected_sha256"] is None
    assert payload["original_sha256"] == record.original_sha256


def test_run_requires_both_routes(tmp_path: Path) -> None:
    data_dir, record, run_a = _started(tmp_path)
    results = {gate: "passed" for gate, _, _ in orchestration.GATES}
    route_a = orchestration.evaluate_attempt(
        record.id,
        run_a.id,
        results=results,
        data_dir=data_dir,
        candidate_sha256=record.original_sha256,
    )

    approval = jobs.approve_contract(
        record.id,
        input_sha256=record.original_sha256,
        contract_revision=1,
        manifest_sha256="1" * 64,
        scope_sha256="2" * 64,
        reviewer_label="test",
        data_dir=data_dir,
    )
    run_b = jobs.start_run(
        record.id,
        approval=approval,
        idempotency_key="run-2",
        data_dir=data_dir,
    )
    route_b = orchestration.evaluate_attempt(
        record.id,
        run_b.id,
        results={**results, "V3": "failed"},
        data_dir=data_dir,
        candidate_sha256=record.original_sha256,
    )
    assert route_b.outcome.value == "rejected"

    run_verdict = orchestration.evaluate_run(
        record.id,
        route_a=route_a,
        route_b=route_b,
        data_dir=data_dir,
    )
    assert run_verdict.outcome.value == "rejected"
    assert jobs.get_job(record.id, data_dir=data_dir).phase == "finished"

    path = data_dir / "jobs" / record.id / "run-verdict.json"
    assert path.exists()


def test_verdict_is_persisted_field_by_field(tmp_path: Path) -> None:
    data_dir, record, run = _started(tmp_path)
    attempts = jobs.list_attempts(run.id, data_dir=data_dir)
    assert {attempt.route for attempt in attempts} == {"route_a", "route_b"}

    results = {gate: "passed" for gate, _, _ in orchestration.GATES}
    route_a = orchestration.evaluate_attempt(
        record.id,
        run.id,
        attempt_id=next(a.id for a in attempts if a.route == "route_a"),
        results=results,
        data_dir=data_dir,
        candidate_sha256=record.original_sha256,
    )
    route_b = orchestration.evaluate_attempt(
        record.id,
        run.id,
        attempt_id=next(a.id for a in attempts if a.route == "route_b"),
        results=results,
        data_dir=data_dir,
        candidate_sha256=record.original_sha256,
    )
    assert route_a.outcome.value == route_b.outcome.value == "success_within_scope"

    for attempt in jobs.list_attempts(run.id, data_dir=data_dir):
        assert attempt.phase == "finished"
        assert attempt.outcome == "success_within_scope"
        assert attempt.reason is None
        assert attempt.failing_obligation is None

    run_verdict = orchestration.evaluate_run(
        record.id,
        run_id=run.id,
        route_a=route_a,
        route_b=route_b,
        data_dir=data_dir,
    )
    assert run_verdict.outcome.value == "success_within_scope"

    connection = connect(data_dir)
    try:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run.id,)).fetchone()
    finally:
        connection.close()
    assert row["phase"] == "finished"
    assert row["outcome"] == "success_within_scope"
    assert row["reason"] is None
    assert row["failing_obligation"] is None
    assert row["detail"] == run_verdict.detail
    assert row["finished_at"] is not None


def test_unknown_run_is_rejected(tmp_path: Path) -> None:
    data_dir, record, _ = _started(tmp_path)
    with pytest.raises(ValueError):
        orchestration.evaluate_attempt(
            record.id,
            "missing-run",
            results={},
            data_dir=data_dir,
            candidate_sha256=record.original_sha256,
        )
