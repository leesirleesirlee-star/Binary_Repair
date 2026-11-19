"""Contract regressions across the GUI and controller with synthetic data."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import api_v1, controller_api, main
from app.backend import BackendError, HttpBackend
from app.controller_api import app as controller_app
from app.db.connection import connect
from app.domain.outcomes import Outcome, ReasonCode
from app.domain.acceptance import Verdict
from app.services import jobs, orchestration, worker
from tests.support_typed_contract import sample_typed_contract, typed_contract_form
from tools.host_broker import process_once


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setenv("BINARYREPAIR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BINARYREPAIR_CONTROLLER_TOKEN", raising=False)
    monkeypatch.setattr(main, "SESSION_TOKEN", "synthetic-lifecycle-session")
    monkeypatch.setattr(main, "CSRF_TOKEN", "synthetic-lifecycle-csrf")
    with TestClient(controller_app, base_url="http://127.0.0.1:8000") as controller:
        def forward(request):
            response = controller.request(
                request.method, str(request.url), headers=request.headers, content=request.read(),
            )
            return httpx.Response(response.status_code, headers=response.headers, content=response.content)

        backend = HttpBackend(transport=httpx.MockTransport(forward))
        monkeypatch.setattr(main, "BACKEND", backend)
        try:
            with TestClient(main.app, base_url="http://127.0.0.1:8125") as client:
                client.cookies.set(main.SESSION_COOKIE, main.SESSION_TOKEN)
                client.headers.update({api_v1.VERSION_HEADER: "1", "x-csrf-token": main.CSRF_TOKEN})
                yield client
        finally:
            backend.close()


def draft(browser):
    response = browser.post(
        "/api/jobs",
        files={"binary": ("synthetic.elf", b"\x7fELFsynthetic")},
        data={
            "defect": "Synthetic task for API tests.",
            "valid_tests": "Keep valid inputs.",
            "contract": typed_contract_form(),
        },
    )
    assert response.status_code == 201, response.text
    return f"/api/jobs/{response.json()['id']}"


def approve(browser, path):
    digest = browser.get(path).json()["contract"]["review_binding_digest"]
    response = browser.post(
        path + "/approve", json={"expected_sha256": digest, "reviewer_label": "test"},
    )
    assert response.status_code == 200, response.text
    return digest


def start(browser, path, digest, key):
    return browser.post(
        path + "/start", json={"expected_sha256": digest},
        headers={"Idempotency-Key": key},
    )


@pytest.mark.parametrize("same_key", [True, False])
def test_competing_starts_and_replay_after_review_change(browser, same_key):
    path = draft(browser)
    digest = approve(browser, path)
    key = str(uuid4())
    barrier = Barrier(2)

    def submit(proposed_key):
        barrier.wait(timeout=5)
        return start(browser, path, digest, proposed_key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, key)
        second_key = key if same_key else str(uuid4())
        second = pool.submit(submit, second_key)
        replies = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(reply.status_code for reply in replies) == ([200, 200] if same_key else [200, 409])
    accepted = next(reply.json() for reply in replies if reply.status_code == 200)
    winning_key = key if replies[0].status_code == 200 else second_key
    if same_key:
        assert replies[0].json() == replies[1].json()
    else:
        failed = next(reply for reply in replies if reply.status_code == 409)
        assert failed.json()["code"] == "invalid_transition"
    assert start(browser, path, digest, winning_key).json() == accepted
    cancelled = browser.post(path + "/cancel", json={"run_id": accepted["run_id"]})
    assert cancelled.json()["execution"]["status"] == "cancelled"
    changed = browser.post(path + "/contract", json={"expected_revision": 1, "valid_domain": "Updated."})
    assert changed.status_code == 200, changed.text
    assert start(browser, path, digest, winning_key).json() == accepted
    conflict = start(browser, path, changed.json()["review_binding_digest"], winning_key)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"


def test_named_cancellation_and_completion_orderings(browser, tmp_path):
    path = draft(browser)
    digest = approve(browser, path)
    job_id = path.rsplit("/", 1)[1]
    verdict = Verdict(Outcome.INCONCLUSIVE, ReasonCode.REQUIRED_CHECK_NOT_RUN)
    first = start(browser, path, digest, str(uuid4())).json()["run_id"]
    orchestration.evaluate_run(job_id, run_id=first, route_a=verdict, route_b=verdict, data_dir=tmp_path)
    assert browser.post(path + "/cancel", json={"run_id": first}).status_code == 409

    second = start(browser, path, digest, str(uuid4())).json()["run_id"]
    assert browser.post(path + "/cancel", json={"run_id": ".."}).status_code == 404
    assert browser.post(path + "/cancel", json={"run_id": first}).status_code == 409
    run = jobs.get_run(second, data_dir=tmp_path)
    assert jobs.claim_run(run, data_dir=tmp_path)
    response = browser.post(path + "/cancel", json={"run_id": second})
    assert response.status_code == 200, response.text
    assert response.json()["execution"] == {
        "run_id": second, "status": "cancelling", "terminal": False, "reason_code": None,
    }
    orchestration.evaluate_run(job_id, run_id=second, route_a=verdict, route_b=verdict, data_dir=tmp_path)
    assert jobs.get_run(first, data_dir=tmp_path).phase == "finished"
    assert jobs.get_run(second, data_dir=tmp_path).phase == "cancelling"
    assert browser.post(path + "/cancel", json={"run_id": second}).json() == response.json()
    evaluation = browser.get(path + "/evaluation", params={"run_id": second}).json()
    assert evaluation["finalized"] is False
    assert evaluation["verdict"]["outcome"] is None


@pytest.mark.parametrize(("claimed", "expected_status"), [(True, "cancelling"), (False, "cancelled")])
def test_late_result_does_not_promote_versioned_status(browser, tmp_path, claimed, expected_status):
    path = draft(browser)
    job_id = path.rsplit("/", 1)[1]
    run_id = start(browser, path, approve(browser, path), str(uuid4())).json()["run_id"]
    run = jobs.get_run(run_id, data_dir=tmp_path)
    if claimed:
        assert jobs.claim_run(run, data_dir=tmp_path)
    attempt = jobs.list_attempts(run_id, data_dir=tmp_path)[0]
    cancellation = browser.post(path + "/cancel", json={"run_id": run_id})
    assert cancellation.status_code == 200
    before = browser.get(path).json()["execution"]
    assert before["status"] == expected_status
    assert before["terminal"] == (not claimed)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "late.json").write_text(json.dumps({
        "job_id": job_id, "run_id": run_id, "attempt_id": attempt.id, "route": attempt.route,
        "bundle": {
            "route": attempt.route, "candidate_sha256": "b" * 64,
            "results": {f"V{i}": "not_run" for i in range(6)},
        },
    }), encoding="utf-8")
    assert process_once(data_dir=tmp_path, inbox=inbox, trigger=False) == {
        "imported": 0, "failed": 1, "worker_processed": 0,
    }
    assert not (tmp_path / "jobs" / job_id / "runs" / run_id / "attempts" / f"{attempt.id}.json").exists()
    snapshot = browser.get(path).json()
    assert snapshot["execution"] == before
    evaluation = browser.get(path + "/evaluation", params={"run_id": run_id}).json()
    assert evaluation["finalized"] is False
    assert evaluation["verdict"]["outcome"] is None
    node = shutil.which("node")
    if node is not None:
        script = """
            import assert from 'node:assert/strict';
            import {readFileSync} from 'node:fs';
            import {repairProgress} from './app/static/ui/chat/chat-progress.mjs';
            import {label} from './app/static/ui/chat/chat-copy.mjs';
            const {snapshot, evaluation, claimed} = JSON.parse(readFileSync(0, 'utf8'));
            const progress = repairProgress({
                live: true, demo: false, jobId: snapshot.id, execution: snapshot.execution,
                data: {evaluation}, events: [{
                    type: 'run_state', run_id: snapshot.execution.run_id,
                    payload: {status: claimed ? 'running' : 'queued'},
                }],
            });
            assert.equal(label('en', progress.detail), claimed ? 'Cancellation requested' : 'Cancelled');
            assert.equal(progress.states[3], 'pending');
            assert.equal(progress.verdict, undefined);
        """
        subprocess.run(
            [node, "--input-type=module", "-e", script], cwd=Path(__file__).resolve().parents[1],
            input=json.dumps({"snapshot": snapshot, "evaluation": evaluation, "claimed": claimed}),
            text=True, check=True, timeout=15,
        )


def _record_comparison(data_dir, job_id, run_id, count):
    attempt = jobs.list_attempts(run_id, data_dir=data_dir)[0]
    relative = f"jobs/{job_id}/evidence/{attempt.id}/comparison.json"
    path = data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    side = {"returncode": None, "timed_out": False, "stdout_sha256": None, "stderr_sha256": None}
    path.write_text(json.dumps({
        "schema_version": 1,
        "scope": "behavior_only",
        "original_sha256": jobs.get_job(job_id, data_dir=data_dir).original_sha256,
        "candidate_sha256": "a" * 64,
        "cases": [{
            "id": f"case-{index}", "kind": "valid", "verdict": "not_run",
            "detail": "Synthetic fixture.", "original": side, "candidate": side,
        } for index in range(count)],
        "summary": {"total": count, "same": 0, "different": 0, "not_run": count, "unknown": 0},
        "not_claimed": ["Synthetic fixture. No verified repair."],
    }), encoding="utf-8")
    artifact = jobs.add_artifact(
        job_id, relative_path=relative, role="behavior_comparison",
        attempt_id=attempt.id, media_type="application/json", data_dir=data_dir,
    )
    return artifact["id"], path


def test_comparison_and_export_use_only_the_selected_run(browser, tmp_path):
    path = draft(browser)
    job_id = path.rsplit("/", 1)[1]
    digest = approve(browser, path)
    first_id = start(browser, path, digest, str(uuid4())).json()["run_id"]
    older, _ = _record_comparison(tmp_path, job_id, first_id, 1)
    final = Verdict(Outcome.INCONCLUSIVE, ReasonCode.REQUIRED_CHECK_NOT_RUN)
    orchestration.evaluate_run(job_id, run_id=first_id, route_a=final, route_b=final, data_dir=tmp_path)
    second_id = start(browser, path, digest, str(uuid4())).json()["run_id"]
    missing = browser.get(path + "/comparison", params={"run_id": second_id}).json()
    assert missing["run_id"] == second_id
    assert missing["status"] == "not_provided"
    assert missing["comparison"] is None and missing["artifacts"] == []
    newer, _ = _record_comparison(tmp_path, job_id, second_id, 2)

    relative = f"jobs/{job_id}/operator-deliverable.bin"
    (tmp_path / relative).write_bytes(b"synthetic deliverable")
    manual_id = jobs.add_artifact(
        job_id, relative_path=relative, role="operator_deliverable", data_dir=tmp_path,
    )["id"]
    unbound = f"jobs/{job_id}/unbound-comparison.json"
    (tmp_path / unbound).write_text("{}", encoding="utf-8")
    unbound_id = jobs.add_artifact(
        job_id, relative_path=unbound, role="behavior_comparison", data_dir=tmp_path,
    )["id"]

    for run_id, artifact_id, count, finalized in [
        (first_id, older, 1, True), (second_id, newer, 2, False),
    ]:
        params = {"run_id": run_id}
        result = browser.get(path + "/comparison", params=params)
        assert result.status_code == 200, result.text
        comparison = result.json()
        assert comparison["run_id"] == run_id and comparison["job_id"] == job_id
        assert comparison["status"] == "available"
        assert comparison["comparison"]["summary"].startswith(f"{count} recorded")
        assert [row["id"] for row in comparison["artifacts"]] == [artifact_id]
        exported = browser.get(path + "/export", params=params)
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-disposition"].startswith("attachment;")
        download = exported.json()
        assert download["run_id"] == download["evaluation"]["run_id"] == run_id
        assert download["evaluation"]["finalized"] is finalized
        assert download["evaluation"]["verdict"]["outcome"] == ("inconclusive" if finalized else None)
        assert download["comparison"] == comparison
        rows = {row["id"]: row for row in download["artifacts"]}
        originals = [row for row in rows.values() if row["kind"] == "original_input"]
        assert len(originals) == 1 and originals[0]["run_id"] is None
        assert set(rows) == {manual_id, artifact_id, originals[0]["id"]}
        assert rows[manual_id]["run_id"] is None
        assert rows[artifact_id]["run_id"] == run_id
        assert unbound_id not in rows

    current = browser.get(path + "/artifacts").json()["artifacts"]
    assert newer in {row["id"] for row in current}
    assert {older, unbound_id}.isdisjoint(row["id"] for row in current)
    other_path = draft(browser)
    other_run = start(browser, other_path, approve(browser, other_path), str(uuid4())).json()["run_id"]
    for route in ("/comparison", "/export"):
        for invalid_id in (other_run, str(uuid4())):
            rejected = browser.get(path + route, params={"run_id": invalid_id})
            assert rejected.status_code == 404
            assert rejected.json()["code"] == "not_found"


@pytest.mark.parametrize("tampered", [True, False])
def test_registered_comparison_with_invalid_evidence_is_invalid(browser, tmp_path, tampered):
    path = draft(browser)
    job_id = path.rsplit("/", 1)[1]
    run_id = start(browser, path, approve(browser, path), str(uuid4())).json()["run_id"]
    if tampered:
        artifact_id, registered = _record_comparison(tmp_path, job_id, run_id, 1)
        content = registered.read_bytes()
        registered.write_bytes(content.replace(b"Synthetic", b"TAMPERED!", 1))
        assert registered.stat().st_size == len(content)
    else:
        relative = f"jobs/{job_id}/malformed-comparison.json"
        (tmp_path / relative).write_text('{"schema_version": 1}', encoding="utf-8")
        attempt = jobs.list_attempts(run_id, data_dir=tmp_path)[0]
        artifact_id = jobs.add_artifact(
            job_id, relative_path=relative, role="behavior_comparison",
            attempt_id=attempt.id, data_dir=tmp_path,
        )["id"]
    response = browser.get(path + "/comparison", params={"run_id": run_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "invalid" and payload["comparison"] is None
    assert payload["artifacts"][0]["id"] == artifact_id
    assert payload["artifacts"][0]["available"] == (not tampered)


def _download_fixture(browser, data_dir):
    job_path = draft(browser)
    job_id = job_path.rsplit("/", 1)[1]
    relative = f"jobs/{job_id}/download fixture.txt"
    file = data_dir / relative
    file.write_bytes(b"Synthetic attachment.")
    row = jobs.add_artifact(
        job_id, relative_path=relative, role="operator_deliverable",
        media_type="text/plain", data_dir=data_dir,
    )
    return job_path, row["id"], file


def test_v1_artifact_download_verifies_ownership_and_attachment(browser, tmp_path):
    job_path, artifact_id, file = _download_fixture(browser, tmp_path)
    scoped = f"{job_path}/artifacts/{artifact_id}/download"
    global_path = f"/api/artifacts/{artifact_id}/download"
    unauthenticated = browser.build_request("GET", scoped)
    del unauthenticated.headers["cookie"]
    unauthorized = browser.send(unauthenticated)
    assert unauthorized.status_code == 401 and unauthorized.json()["code"] == "session_required"
    for path in (global_path, scoped):
        response = browser.get(path)
        assert response.status_code == 200, response.text
        assert response.content == file.read_bytes()
        assert response.headers["content-disposition"] == 'attachment; filename="download_fixture.txt"'
        assert response.headers[api_v1.VERSION_HEADER] == "1"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    other_job = draft(browser)
    unregistered = browser.get(f"{job_path}/artifacts/{uuid4().hex}/download")
    wrong_owner = browser.get(f"{other_job}/artifacts/{artifact_id}/download")
    unknown = browser.get(f"/api/artifacts/{uuid4().hex}/download")
    for denied in (unregistered, wrong_owner, unknown):
        assert denied.status_code == 404
        assert denied.json()["code"] == "not_found"
        assert denied.json()["detail"] == "Artifact not found."
        assert denied.json()["correlation_id"]


@pytest.mark.parametrize("missing", [True, False])
def test_v1_registered_artifact_with_missing_or_modified_bytes_returns_409(browser, tmp_path, missing):
    job_path, artifact_id, file = _download_fixture(browser, tmp_path)
    if missing:
        file.unlink()
    else:
        before = file.read_bytes()
        file.write_bytes(b"x" * len(before))
    for path in (f"/api/artifacts/{artifact_id}/download", f"{job_path}/artifacts/{artifact_id}/download"):
        response = browser.get(path)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "artifact_unavailable"
        assert response.json()["correlation_id"]
        assert response.headers[api_v1.VERSION_HEADER] == "1"
        assert "content-disposition" not in response.headers
    legacy_request = browser.build_request("GET", f"/api/artifacts/{artifact_id}/download")
    del legacy_request.headers[api_v1.VERSION_HEADER]
    legacy = browser.send(legacy_request)
    assert legacy.status_code == 502 and "code" not in legacy.json()


def test_waiting_requires_fresh_unclaimed_worker_observation(browser, tmp_path, monkeypatch):
    monkeypatch.delenv("BINARYREPAIR_ATTEMPT_CMD", raising=False)

    def no_executor(*args, **kwargs):
        pytest.fail("No executor should be invoked in this synthetic test.")

    monkeypatch.setattr(worker, "_run_executor", no_executor)
    path = draft(browser)
    job_id = path.rsplit("/", 1)[1]
    started = start(browser, path, approve(browser, path), str(uuid4()))
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    assert browser.get(path).json()["execution"]["status"] == "queued"
    assert worker.run_once(data_dir=tmp_path) == 0

    snapshot = browser.get(path).json()
    assert snapshot["execution"] == {
        "run_id": run_id, "status": "waiting_for_executor", "terminal": False,
        "reason_code": "executor_unavailable", "started_at": None, "finished_at": None,
    }
    assert main.BACKEND.get_snapshot(job_id)["execution"]["status"] == "queued"
    for attempt in jobs.list_attempts(run_id, data_dir=tmp_path):
        assert attempt.phase == "queued"
        assert not jobs.list_obligations(attempt.id, data_dir=tmp_path)

    connection = connect(tmp_path)
    try:
        with connection:
            connection.execute(
                "UPDATE run_executor_preflight SET observed_at = ? WHERE run_id = ?",
                ("2000-01-01T00:00:00+00:00", run_id),
            )
    finally:
        connection.close()
    assert browser.get(path).json()["execution"]["status"] == "queued"
    assert worker.run_once(data_dir=tmp_path) == 0
    assert browser.get(path).json()["execution"]["status"] == "waiting_for_executor"

    pending = jobs.get_run(run_id, data_dir=tmp_path)
    assert jobs.claim_run(pending, data_dir=tmp_path)
    jobs.record_executor_unavailable(pending, data_dir=tmp_path)
    assert browser.get(path).json()["execution"]["status"] == "running"


def test_waiting_run_can_cancel_without_a_termination_receipt(browser, tmp_path, monkeypatch):
    monkeypatch.delenv("BINARYREPAIR_ATTEMPT_CMD", raising=False)
    path = draft(browser)
    started = start(browser, path, approve(browser, path), str(uuid4()))
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    assert worker.run_once(data_dir=tmp_path) == 0
    assert browser.get(path).json()["execution"]["status"] == "waiting_for_executor"
    cancelled = browser.post(path + "/cancel", json={"run_id": run_id})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["execution"]["status"] == "cancelled"
    assert not jobs.claim_run(jobs.get_run(run_id, data_dir=tmp_path), data_dir=tmp_path)


def test_multipart_limit_without_content_length(browser, monkeypatch):
    prefix = (
        b"--fixture\r\nContent-Disposition: form-data; name=\"defect\"\r\n\r\nSynthetic.\r\n"
        b"--fixture\r\nContent-Disposition: form-data; name=\"binary\"; filename=\"test.elf\"\r\n"
        b"Content-Type: application/octet-stream\r\n\r\n\x7fELFsynthetic\r\n"
    )
    suffix = b"--fixture--\r\n"
    monkeypatch.setattr(api_v1, "MAX_REQUEST_BYTES", len(prefix) + len(suffix))
    headers = {"Content-Type": "multipart/form-data; boundary=fixture"}
    request = browser.build_request("POST", "/api/jobs", headers=headers, content=iter([prefix, suffix]))
    assert "content-length" not in request.headers
    assert browser.send(request).status_code == 201
    over = browser.post("/api/jobs", headers=headers, content=iter([prefix, suffix, b"x"]))
    assert over.status_code == 413
    assert over.json()["code"] == "payload_too_large"
    assert over.headers[api_v1.VERSION_HEADER] == "1"
    assert over.headers["Cache-Control"] == "no-store"
    assert len(browser.get("/api/jobs").json()["jobs"]) == 1


def test_review_edit_and_approval_serialize(browser):
    path = draft(browser)
    previous = browser.get(path).json()["contract"]
    digest = previous["review_binding_digest"]
    barrier = Barrier(2)

    def submit(route, body):
        barrier.wait(timeout=5)
        return browser.post(path + route, json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        edit = pool.submit(submit, "/contract", {"expected_revision": 1, "valid_domain": "Revised."})
        approval = pool.submit(submit, "/approve", {"expected_sha256": digest, "reviewer_label": "test"})
        assert edit.result(timeout=10).status_code == 200
        result = approval.result(timeout=10)
    assert result.status_code in {200, 409}
    if result.status_code == 409:
        assert result.json()["code"] == "stale_approval"
    current = browser.get(path).json()["contract"]
    assert current["revision"] == 2
    assert current["approved"] is False
    assert current["assumptions"] == previous["assumptions"]
    assert current["forbidden"] == previous["forbidden"]
    assert current["review_binding_digest"] != digest
    blocked = start(browser, path, current["review_binding_digest"], str(uuid4()))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "stale_approval"


def test_active_run_blocks_review_changes_across_both_versions(browser):
    path = draft(browser)
    job_id = path.rsplit("/", 1)[1]
    snapshot = browser.get(path).json()
    with pytest.raises(BackendError, match="through the v1 interface"):
        main.BACKEND.approve_contract(job_id, expected_sha256=snapshot["contract"]["sha256"], reviewer_label="legacy")
    with pytest.raises(BackendError, match="not approved"):
        main.BACKEND.start(job_id)

    assert start(browser, path, approve(browser, path), str(uuid4())).status_code == 200
    stale_edit = browser.post(path + "/contract", json={"expected_revision": 100, "valid_domain": "Changed."})
    stale_approval = browser.post(
        path + "/approve", json={"expected_sha256": "0" * 64, "reviewer_label": "test"},
    )
    for response in (stale_edit, stale_approval):
        assert response.status_code == 409
        assert response.json()["code"] == "invalid_transition"
    with pytest.raises(BackendError, match="already active"):
        main.BACKEND.update_contract(
            job_id, expected_revision="1", valid_domain="Legacy", forbidden="",
            invalid_outcome="", assumptions="",
        )


def test_approval_rolls_back_if_saving_the_review_fails(browser, tmp_path, monkeypatch):
    path = draft(browser)
    job_id = path.rsplit("/", 1)[1]

    def fail_write(job_id, meta, *, connection):
        assert connection.execute("SELECT 1 FROM contracts WHERE job_id = ?", (job_id,)).fetchone()
        raise RuntimeError("synthetic review write failure")

    with monkeypatch.context() as simulated:
        simulated.setattr(controller_api, "_write_meta", fail_write)
        with pytest.raises(RuntimeError, match="synthetic review write failure"):
            approve(browser, path)
    assert jobs.get_contract(job_id, 1, data_dir=tmp_path) is None
    assert browser.get(path).json()["contract"]["approved"] is False
    approve(browser, path)


def test_manifest_only_change_invalidates_approval_without_changing_content_hash(browser):
    path = draft(browser)
    digest = approve(browser, path)
    previous = browser.get(path).json()["contract"]
    changed = main.BACKEND._request(
        "POST", path + "/reviewed-contract", where="test manifest update",
        json={"expected_revision": previous["revision"], "valid_tests": "New declared test cases."},
    )
    current = browser.get(path).json()["contract"]
    assert changed["revision"] == previous["revision"] + 1
    assert current["sha256"] == previous["sha256"]
    assert current["review_binding"]["test_manifest_sha256"] != previous["review_binding"]["test_manifest_sha256"]
    assert current["review_binding_digest"] != digest
    assert current["approved"] is False
    blocked = start(browser, path, current["review_binding_digest"], str(uuid4()))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "stale_approval"
    assert blocked.json()["current"] == changed


def test_review_edit_and_start_cannot_both_commit(browser):
    path = draft(browser)
    digest = approve(browser, path)
    barrier = Barrier(2)

    def edit():
        barrier.wait(timeout=5)
        return browser.post(path + "/contract", json={"expected_revision": 1, "valid_domain": "Revised."})

    def queue():
        barrier.wait(timeout=5)
        return start(browser, path, digest, str(uuid4()))

    with ThreadPoolExecutor(max_workers=2) as pool:
        edit_result = pool.submit(edit)
        start_result = pool.submit(queue)
        edit_reply = edit_result.result(timeout=10)
        start_reply = start_result.result(timeout=10)
    assert sorted([edit_reply.status_code, start_reply.status_code]) == [200, 409]
    if start_reply.status_code == 200:
        assert edit_reply.json()["code"] == "invalid_transition"
    else:
        assert start_reply.json()["code"] == "stale_approval"


def test_legacy_sidecar_draft_remains_readable_but_needs_a_v1_approval(browser, tmp_path):
    record = jobs.create_job(
        original_bytes=b"synthetic input bytes", filename="example.elf",
        defect_description="Synthetic historical job.", data_dir=tmp_path,
    )
    jobs.approve_contract(
        record.id, contract_revision=1, reviewer_label="legacy", data_dir=tmp_path,
        manifest_sha256="a" * 64,
    )
    contract = controller_api._contract_body(valid_domain="Legacy review scope.")
    contract["approved"] = True
    sidecar = tmp_path / "jobs" / record.id / "meta.json"
    sidecar.write_text(json.dumps({
        "contract": contract,
        "approval": {"revision": 1, "sha256": contract["sha256"]},
        "environment": "Synthetic test environment.",
        "manifest_sha256": "a" * 64,
    }), encoding="utf-8")
    path = f"/api/jobs/{record.id}"
    snapshot = browser.get(path).json()
    assert snapshot["contract"]["valid_domain"] == "Legacy review scope."
    assert snapshot["contract"]["approved"] is False
    assert main.BACKEND.get_snapshot(record.id)["contract"]["approved"] is True

    blocked = browser.post(
        path + "/approve",
        json={
            "expected_sha256": snapshot["contract"]["review_binding_digest"],
            "reviewer_label": "legacy",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "invalid_transition"

    attached = browser.post(path + "/typed-contract", json={"contract": sample_typed_contract()})
    assert attached.status_code == 200, attached.text
    assert (
        browser.get(path).json()["contract"]["valid_domain"]
        == "Bounds-checked input of at most 49 bytes"
    )
    approve(browser, path)
    assert browser.get(path).json()["contract"]["approved"] is True
    assert sidecar.exists()
