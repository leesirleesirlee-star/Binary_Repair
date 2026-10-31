"""Tests for the executor -> worker attempt bundle writer."""

from __future__ import annotations

import json
from pathlib import Path

from app.db.connection import connect
from app.services import attempt_writer, jobs


def _job(tmp_path: Path, route: str = "route_a"):
    data_dir = tmp_path / "var"
    record = jobs.create_job(
        original_bytes=b"\x7fELF attempt writer",
        filename="sample.elf",
        defect_description="test",
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
        idempotency_key="writer-1",
        data_dir=data_dir,
    )
    attempt = next(a for a in jobs.list_attempts(run.id, data_dir=data_dir) if a.route == route)
    return data_dir, record, run, attempt


def test_generic_writer_writes_worker_bundle(tmp_path: Path) -> None:
    data_dir, record, run, attempt = _job(tmp_path)
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            {
                "results": {f"V{i}": "not_run" for i in range(6)},
                "details": {"V0": "identity pending"},
            }
        ),
        encoding="utf-8",
    )
    out = data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / f"{attempt.id}.json"
    code = attempt_writer.main(
        [
            "--input",
            str(source),
            "--out",
            str(out),
            "--route",
            "route_a",
            "--candidate-sha256",
            record.original_sha256,
        ]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["route"] == "route_a"
    assert payload["candidate_sha256"] == record.original_sha256
    assert payload["results"]["V0"] == "not_run"


def test_routeb_bundle_maps_static_audit_without_runtime_pass(tmp_path: Path) -> None:
    data_dir, record, run, attempt = _job(tmp_path)
    bundle = tmp_path / "routeb"
    bundle.mkdir()
    (bundle / "candidate.elf").write_bytes(b"\x7fELF candidate")
    (bundle / "audit.json").write_text('{"pass": true}', encoding="utf-8")
    out = data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / f"{attempt.id}.json"

    code = attempt_writer.main(
        [
            "--routeb-bundle",
            str(bundle),
            "--data-dir",
            str(data_dir),
            "--job-id",
            record.id,
            "--attempt-id",
            attempt.id,
            "--out",
            str(out),
        ]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["route"] == "route_b"
    assert payload["results"]["V0"] == "not_run"
    assert payload["results"]["V1"] == "passed"
    assert payload["results"]["V3"] == "not_run"
    assert "V1" in payload["evidence"]


def test_routea_bundle_maps_gates_and_registers_evidence(tmp_path: Path) -> None:
    from tools.routea_bundle_to_attempt import convert

    data_dir, record, run, attempt = _job(tmp_path)
    evidence = tmp_path / "routea" / "evidence.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("route-a evidence\n", encoding="utf-8")
    bundle = {
        "route": "route_a",
        "identity": {"candidate_sha256": record.original_sha256},
        "gates": {
            "V0": {"status": "pass", "evidence": [
                {"relative_path": "routea/evidence.txt"}]},
            "V1": {"status": "inconclusive", "evidence": []},
            "V2": {"status": "not_run", "evidence": []},
            "V3": {"status": "not_run", "evidence": []},
            "V4": {"status": "not_run", "evidence": []},
            "V5": {"status": "not_run", "evidence": []},
        },
    }
    payload = convert(
        bundle, evidence_root=tmp_path, data_dir=data_dir,
        job_id=record.id, attempt_id=attempt.id,
    )
    assert payload["results"] == {
        "V0": "passed", "V1": "unknown", "V2": "not_run",
        "V3": "not_run", "V4": "not_run", "V5": "not_run",
    }
    artifact_id = payload["evidence"]["V0"]
    connection = connect(data_dir)
    try:
        row = connection.execute(
            "SELECT sha256, relative_path FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row["sha256"] == __import__("hashlib").sha256(evidence.read_bytes()).hexdigest()
    assert row["relative_path"].startswith(f"attempts/{attempt.id}/")


def test_routea_bundle_rejects_passed_without_evidence(tmp_path: Path) -> None:
    import pytest
    from tools.routea_bundle_to_attempt import convert

    data_dir, record, run, attempt = _job(tmp_path)
    bundle = {
        "route": "route_a",
        "identity": {"candidate_sha256": record.original_sha256},
        "gates": {"V0": {"status": "pass", "evidence": []}},
    }
    with pytest.raises(ValueError, match="V0 passed without evidence"):
        convert(bundle, evidence_root=tmp_path, data_dir=data_dir,
                job_id=record.id, attempt_id=attempt.id)


def _routeb_identity_bundle(tmp_path: Path, *, original_sha256: str) -> tuple[Path, Path]:
    bundle = tmp_path / "routeb-identity"
    bundle.mkdir()
    candidate = bundle / "candidate.elf"
    candidate.write_bytes(b"\x7fELF identity candidate")
    (bundle / "audit.json").write_text('{"pass": true}', encoding="utf-8")
    (bundle / "edit-manifest.json").write_text(
        json.dumps({
            "original_sha256": original_sha256,
            "candidate_sha256": __import__("hashlib").sha256(candidate.read_bytes()).hexdigest(),
        }),
        encoding="utf-8",
    )
    return bundle, candidate


def test_routeb_identity_passes_only_with_matching_original(tmp_path: Path) -> None:
    data_dir, record, run, attempt = _job(tmp_path, route="route_b")
    bundle, _ = _routeb_identity_bundle(tmp_path, original_sha256=record.original_sha256)
    out = data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / f"{attempt.id}.json"
    code = attempt_writer.main([
        "--routeb-bundle", str(bundle),
        "--data-dir", str(data_dir),
        "--job-id", record.id,
        "--attempt-id", attempt.id,
        "--out", str(out),
    ])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["results"]["V0"] == "passed"


def test_routeb_identity_fails_on_original_mismatch(tmp_path: Path) -> None:
    data_dir, record, run, attempt = _job(tmp_path, route="route_b")
    bundle, _ = _routeb_identity_bundle(tmp_path, original_sha256="f" * 64)
    out = data_dir / "jobs" / record.id / "runs" / run.id / "attempts" / f"{attempt.id}.json"
    code = attempt_writer.main([
        "--routeb-bundle", str(bundle),
        "--data-dir", str(data_dir),
        "--job-id", record.id,
        "--attempt-id", attempt.id,
        "--out", str(out),
    ])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["results"]["V0"] == "failed"
