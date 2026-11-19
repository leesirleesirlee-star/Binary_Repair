from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import jobs
from tools.route_bundle_ingest import IngestError, ingest


def _job(tmp_path: Path):
    data = tmp_path / "var"
    job = jobs.create_job(original_bytes=b"\x7fELF ingest", filename="sample.elf",
                          defect_description="overflow", data_dir=data)
    approval = jobs.approve_contract(job.id, contract_revision=1, reviewer_label="test",
                                     data_dir=data, input_sha256=job.original_sha256,
                                     manifest_sha256="1" * 64)
    run = jobs.start_run(job.id, approval=approval, idempotency_key="ingest", data_dir=data)
    attempt = next(a for a in jobs.list_attempts(run.id, data_dir=data) if a.route == "route_a")
    return data, job, run, attempt


def _bundle(tmp_path: Path, candidate: str | None = None) -> Path:
    import hashlib
    bundle = tmp_path / "bundle"; bundle.mkdir()
    payload = b"candidate bytes"
    (bundle / "candidate.elf").write_bytes(payload)
    if candidate is None:
        # A placeholder hash would let V0 pass without carrying the candidate.
        candidate = hashlib.sha256(payload).hexdigest()
    (bundle / "audit.json").write_text('{"pass": true}')
    (bundle / "bundle.json").write_text(json.dumps({
        "schema_version": 1, "route": "route_a", "candidate_sha256": candidate,
        "gates": {"V0": {"status": "passed", "artifacts": ["candidate.elf"]},
                  "V1": {"status": "passed", "artifacts": ["audit.json"]}},
    }))
    return bundle


def test_ingest_registers_artifacts_and_bundle(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    result = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                    route="route_a", bundle=bundle, trigger=False)
    assert result["evidence"]["V0"]
    payload = json.loads((data / "jobs" / job.id / "runs" / run.id / "attempts" / f"{attempt.id}.json").read_text())
    assert payload["results"]["V0"] == "passed"
    assert payload["evidence"]["V0"] == result["evidence"]["V0"]


def test_ingest_rejects_traversal_and_missing_passed_evidence(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    before = jobs.list_artifacts(job.id, data_dir=data)
    bundle = _bundle(tmp_path)
    manifest = json.loads((bundle / "bundle.json").read_text())
    manifest["gates"]["V1"] = {"status": "passed", "artifacts": ["../outside"]}
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(IngestError):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)
    assert jobs.list_artifacts(job.id, data_dir=data) == before
    assert not list((data / "jobs" / job.id / "evidence" / attempt.id).glob("*"))


def test_ingest_is_idempotent_for_same_bytes(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    first = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                   route="route_a", bundle=bundle, trigger=False)
    second = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                    route="route_a", bundle=bundle, trigger=False)
    assert first["evidence"] == second["evidence"]


def test_ingest_registers_analysis_and_comparison(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    (bundle / "analysis").mkdir()
    (bundle / "analysis" / "v3.json").write_text('{"cases": 6}')
    (bundle / "behavior-comparison.json").write_text('{"same": 6}')
    manifest = json.loads((bundle / "bundle.json").read_text())
    manifest["analysis"] = [{"role": "analysis_v3", "path": "analysis/v3.json"}]
    manifest["comparison"] = "behavior-comparison.json"
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    result = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                    route="route_a", bundle=bundle, trigger=False)
    assert result["analysis_artifact_ids"]


def test_ingest_rejects_symlink(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    try:
        (bundle / "link.json").symlink_to(bundle / "audit.json")
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    manifest = json.loads((bundle / "bundle.json").read_text())
    manifest["gates"]["V1"] = {"status": "passed", "artifacts": ["link.json"]}
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(IngestError):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)


def test_ingest_rejects_unknown_gate_and_status(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    manifest = json.loads((bundle / "bundle.json").read_text())
    manifest["gates"]["V9"] = {"status": "passed", "artifacts": []}
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(IngestError, match="unknown gate"):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)
    manifest["gates"].pop("V9")
    manifest["gates"]["V1"]["status"] = "maybe"
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(IngestError, match="unknown status"):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)


def test_ingest_validates_expected_effects_analysis_schema(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    analysis = bundle / "analysis"; analysis.mkdir()
    (analysis / "effects.json").write_text(json.dumps({
        "schema_version": 1, "candidate_sha256": "a" * 64,
        "expected_effects": ["guard added"], "assumptions": [], "uncertainties": [],
    }))
    manifest = json.loads((bundle / "bundle.json").read_text())
    manifest["analysis"] = [{"role": "analysis_expected_effects", "path": "analysis/effects.json"}]
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    result = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                    route="route_a", bundle=bundle, trigger=False)
    assert result["analysis_artifact_ids"]


def test_ingest_accepts_legacy_interface_v1(tmp_path: Path) -> None:
    import hashlib
    data, job, run, attempt = _job(tmp_path)
    bundle = tmp_path / "legacy"; bundle.mkdir()
    candidate = bundle / "candidate.elf"; candidate.write_bytes(b"\x7fELF legacy")
    (bundle / "result-bundle.json").write_text(json.dumps({
        "route": "route_a",
        "identity": {"candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()},
        "gates": {"V0": {"status": "pass", "evidence": [{"relative_path": "candidate.elf"}]}},
    }))
    files = ["result-bundle.json", "candidate.elf"]
    (bundle / "handoff.json").write_text(json.dumps({
        "id": "LEGACY", "from": "M3", "to": "M5", "route": "route_a",
        "created_at": "2026-11-05T00:00:00+08:00", "files": files,
    }))
    lines = []
    for name in sorted(files):
        lines.append(f"{hashlib.sha256((bundle/name).read_bytes()).hexdigest()}  {name}")
    (bundle / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    result = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                    route="route_a", bundle=bundle, trigger=False)
    assert result["evidence"]["V0"]
    from app.db.connection import connect
    connection = connect(data)
    try:
        role = connection.execute(
            "SELECT role FROM artifacts WHERE job_id = ? AND relative_path LIKE ?",
            (job.id, "%result-bundle.json"),
        ).fetchone()["role"]
    finally:
        connection.close()
    assert role == "legacy_result_bundle"


def test_legacy_result_bundle_missing_from_handoff_is_rejected(tmp_path: Path) -> None:
    import hashlib
    data, job, run, attempt = _job(tmp_path)
    bundle = tmp_path / "legacy-unlisted"; bundle.mkdir()
    candidate = bundle / "candidate.elf"; candidate.write_bytes(b"\x7fELF")
    (bundle / "result-bundle.json").write_text(json.dumps({"identity": {"candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}}))
    # handoff.json omits result-bundle.json while SHA256SUMS lists it, so the
    # rejection below can only come from the handoff check. The companion test
    # covers the opposite: listed in handoff, absent from SHA256SUMS.
    files = ["candidate.elf"]
    (bundle / "handoff.json").write_text(json.dumps({
        "id": "LEGACY", "from": "M3", "to": "M5", "route": "route_a",
        "created_at": "2026-11-05T00:00:00+08:00", "files": files,
    }))
    rb = bundle / "result-bundle.json"
    (bundle / "SHA256SUMS").write_text(
        f"{hashlib.sha256(candidate.read_bytes()).hexdigest()}  candidate.elf\n"
        f"{hashlib.sha256(rb.read_bytes()).hexdigest()}  result-bundle.json\n"
    )
    with pytest.raises(IngestError, match="result-bundle.json"):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)


def test_legacy_result_bundle_in_handoff_but_not_sums_is_rejected(tmp_path: Path) -> None:
    import hashlib
    data, job, run, attempt = _job(tmp_path)
    bundle = tmp_path / "legacy-unhashed"; bundle.mkdir()
    candidate = bundle / "candidate.elf"; candidate.write_bytes(b"\x7fELF legacy")
    (bundle / "result-bundle.json").write_text(json.dumps({
        "identity": {"candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()},
        "gates": {"V0": {"status": "passed",
                         "evidence": [{"relative_path": "candidate.elf"}]}},
    }))
    (bundle / "handoff.json").write_text(json.dumps({
        "id": "LEGACY", "from": "M3", "to": "M5", "route": "route_a",
        "created_at": "2026-11-05T00:00:00+08:00",
        "files": ["candidate.elf", "result-bundle.json"],
    }))
    # SHA256SUMS lists the payload but omits the gate-carrying file.
    (bundle / "SHA256SUMS").write_text(
        f"{hashlib.sha256(candidate.read_bytes()).hexdigest()}  candidate.elf\n"
    )
    with pytest.raises(IngestError, match="result-bundle.json"):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)


def test_v0_picks_candidate_not_first_artifact(tmp_path: Path) -> None:
    from app.db.connection import connect
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    (bundle / "decoy.json").write_text('{"not": "candidate"}')
    manifest = json.loads((bundle / "bundle.json").read_text())
    manifest["candidate_sha256"] = __import__("hashlib").sha256((bundle / "candidate.elf").read_bytes()).hexdigest()
    manifest["gates"]["V0"]["artifacts"] = ["decoy.json", "candidate.elf"]
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    result = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                    route="route_a", bundle=bundle, trigger=False)
    connection = connect(data)
    try:
        row = connection.execute(
            "SELECT sha256 FROM artifacts WHERE id = ?", (result["evidence"]["V0"],)
        ).fetchone()
    finally:
        connection.close()
    assert row["sha256"] == manifest["candidate_sha256"]


def test_v0_without_the_candidate_bytes_is_rejected(tmp_path: Path) -> None:
    """No silent ids[0] fallback: if V0 does not carry the candidate, refuse."""
    import hashlib
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    (bundle / "decoy.json").write_text('{"not": "candidate"}')
    manifest = json.loads((bundle / "bundle.json").read_text())
    # Claim a candidate that is not present in the V0 artifact list.
    manifest["candidate_sha256"] = hashlib.sha256(b"absent candidate").hexdigest()
    manifest["gates"]["V0"]["artifacts"] = ["decoy.json"]
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(IngestError, match="declared candidate"):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)


def test_legacy_without_result_bundle_is_all_not_run(tmp_path: Path) -> None:
    import hashlib
    data, job, run, attempt = _job(tmp_path)
    bundle = tmp_path / "legacy-no-result"; bundle.mkdir()
    candidate = bundle / "candidate.elf"; candidate.write_bytes(b"\x7fELF legacy")
    files = ["candidate.elf"]
    (bundle / "handoff.json").write_text(json.dumps({
        "id": "LEGACY", "from": "M3", "to": "M5", "route": "route_a",
        "created_at": "2026-11-05T00:00:00+08:00", "files": files,
        # Prose claims must never become gate evidence.
        "notes": "V2 16/16 passed; V3 6/6 repaired_behaviour; all gates green",
    }))
    (bundle / "SHA256SUMS").write_text(
        f"{hashlib.sha256(candidate.read_bytes()).hexdigest()}  candidate.elf\n"
    )
    result = ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
                    route="route_a", bundle=bundle, trigger=False)
    payload = json.loads((data / "jobs" / job.id / "runs" / run.id / "attempts" / f"{attempt.id}.json").read_text())
    assert payload["results"] == {f"V{i}": "not_run" for i in range(6)}
    assert result["evidence"] == {}


def test_v0_candidate_must_exist(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    before = jobs.list_artifacts(job.id, data_dir=data)
    bundle = _bundle(tmp_path)
    manifest = json.loads((bundle / "bundle.json").read_text())
    manifest["candidate_sha256"] = "a" * 64
    (bundle / "bundle.json").write_text(json.dumps(manifest))
    # M5 017ca87 asserted its own wording; the merge kept M1's message, so this
    # pins the behaviour (rejection) rather than the sentence.
    with pytest.raises(IngestError, match="does not contain the declared candidate"):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)
    assert jobs.list_artifacts(job.id, data_dir=data) == before
    assert not (data / "jobs" / job.id / "evidence" / attempt.id).exists()


def test_conflicting_bundle_preserves_registered_artifact_bytes(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    options = dict(
        data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
        route="route_a", bundle=bundle, trigger=False,
    )
    first = ingest(**options)
    artifact = jobs.get_artifact(first["evidence"]["V1"], data_dir=data)
    path = data / artifact["relative_path"]
    previous = path.read_bytes()
    originals = jobs.list_artifacts(job.id, data_dir=data)
    (bundle / "audit.json").write_text('{"pass": false}', encoding="utf-8")
    with pytest.raises(IngestError, match="conflicting existing artifact"):
        ingest(**options)
    assert path.read_bytes() == previous
    assert jobs.list_artifacts(job.id, data_dir=data) == originals


def test_directory_ingest_refuses_late_evidence_before_registering(tmp_path: Path) -> None:
    data, job, run, attempt = _job(tmp_path)
    bundle = _bundle(tmp_path)
    assert jobs.claim_run(run, data_dir=data)
    assert jobs.cancel_run(job.id, run.id, data_dir=data)["phase"] == "cancelling"
    before = jobs.list_artifacts(job.id, data_dir=data)
    with pytest.raises(IngestError, match="run no longer accepts results"):
        ingest(data_dir=data, job_id=job.id, run_id=run.id, attempt_id=attempt.id,
               route="route_a", bundle=bundle, trigger=False)
    assert jobs.list_artifacts(job.id, data_dir=data) == before
    assert jobs.get_run(run.id, data_dir=data).phase == "cancelling"
