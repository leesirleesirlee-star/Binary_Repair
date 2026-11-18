"""Contract tests for the controller HTTP API."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.controller_api import app

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (test: execution disabled)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
}
CJK = re.compile(r"[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BINARYREPAIR_DATA_DIR", str(tmp_path / "var"))
    return TestClient(app, base_url="http://127.0.0.1")


def _create(client: TestClient) -> dict:
    response = client.post(
        "/api/jobs",
        data=FORM,
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    assert not CJK.search(response.text)
    return response.json()


def test_create_and_snapshot_contract(client: TestClient) -> None:
    created = _create(client)
    job_id = created["id"]
    assert len(created["original_sha256"]) == 64

    snapshot = client.get(f"/api/jobs/{job_id}")
    assert snapshot.status_code == 200
    payload = snapshot.json()
    for key in ("id", "phase", "routes", "contract", "original_sha256", "valid_tests"):
        assert key in payload
    assert set(payload["routes"]) == {"A", "B"}
    assert payload["phase"] == "draft"
    assert payload["demo"] is False
    assert payload["contract"]["approved"] is False
    assert payload["contract"]["revision"] == 1
    assert len(payload["contract"]["sha256"]) == 64
    assert not CJK.search(snapshot.text)


def _approve_current(client: TestClient, job_id: str) -> dict:
    contract = client.get(f"/api/jobs/{job_id}").json()["contract"]
    response = client.post(
        f"/api/jobs/{job_id}/approve",
        data={"expected_sha256": contract["sha256"], "reviewer_label": "reviewer"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_start_requires_approval_and_is_idempotent(client: TestClient) -> None:
    job_id = _create(client)["id"]

    blocked = client.post(f"/api/jobs/{job_id}/start")
    assert blocked.status_code == 409

    _approve_current(client, job_id)
    first = client.post(f"/api/jobs/{job_id}/start")
    second = client.post(f"/api/jobs/{job_id}/start")
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]

    snapshot = client.get(f"/api/jobs/{job_id}").json()
    assert snapshot["phase"] == "queued"
    assert snapshot["execution"]["status"] == "queued"
    assert snapshot["execution"]["source"] == "controller-derived"
    assert snapshot["execution"]["sidecar"] is None
    assert snapshot["execution_phase"] == "queued"


def test_contract_edit_invalidates_approval(client: TestClient) -> None:
    job_id = _create(client)["id"]
    _approve_current(client, job_id)

    stale = client.post(
        f"/api/jobs/{job_id}/contract",
        data={"expected_revision": 0, "valid_domain": "changed"},
    )
    assert stale.status_code == 409

    updated = client.post(
        f"/api/jobs/{job_id}/contract",
        data={"expected_revision": 1, "valid_domain": "Input length 0-49 bytes"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["contract"]["revision"] == 2
    assert updated.json()["contract"]["approved"] is False
    assert client.post(f"/api/jobs/{job_id}/start").status_code == 409

    stale_hash = client.post(
        f"/api/jobs/{job_id}/approve",
        data={"expected_sha256": "0" * 64, "reviewer_label": "reviewer"},
    )
    assert stale_hash.status_code == 409


def test_same_key_with_new_contract_returns_409(client: TestClient) -> None:
    job_id = _create(client)["id"]
    _approve_current(client, job_id)
    first = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 1,
            "manifest_sha256": "1" * 64,
            "idempotency_key": "same-key",
        },
    )
    assert first.status_code == 202, first.text

    updated = client.post(
        f"/api/jobs/{job_id}/contract",
        data={"expected_revision": 1, "valid_domain": "revised contract"},
    )
    assert updated.status_code == 200, updated.text
    _approve_current(client, job_id)
    conflict = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 2,
            "manifest_sha256": "1" * 64,
            "idempotency_key": "same-key",
        },
    )
    assert conflict.status_code == 409


def test_historical_run_evaluation_is_run_scoped(client: TestClient) -> None:
    job_id = _create(client)["id"]
    _approve_current(client, job_id)
    started = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 1,
            "manifest_sha256": "1" * 64,
            "idempotency_key": "history-1",
        },
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    evaluation = client.get(f"/api/jobs/{job_id}/runs/{run_id}/evaluation")
    assert evaluation.status_code == 200, evaluation.text
    assert evaluation.json()["run_id"] == run_id
    assert evaluation.json()["finalized"] is False

    missing = client.get(f"/api/jobs/{job_id}/runs/not-a-run/evaluation")
    assert missing.status_code == 404


def test_artifact_metadata_and_download(client: TestClient) -> None:
    job_id = _create(client)["id"]
    listing = client.get(f"/api/jobs/{job_id}/artifacts")
    assert listing.status_code == 200
    artifact = listing.json()["A"][0]
    assert artifact["id"]
    meta = client.get(f"/api/artifacts/{artifact['id']}")
    assert meta.status_code == 200, meta.text
    payload = meta.json()
    assert payload["id"] == artifact["id"]
    assert payload["available"] is True
    assert payload["sha256"] == artifact["sha256"]

    download = client.get(f"/api/artifacts/{artifact['id']}/download")
    assert download.status_code == 200
    assert download.content == b"\x7fELF demo bytes"
    assert "attachment" in download.headers.get("content-disposition", "")

    assert client.get("/api/artifacts/does-not-exist").status_code == 404


def test_visuals_endpoint(client: TestClient) -> None:
    job_id = _create(client)["id"]
    response = client.get(f"/api/jobs/{job_id}/visuals")
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_canonical_runs_creates_attempts_and_evidence(client: TestClient) -> None:
    created = _create(client)
    job_id = created["id"]
    contract = {
        "schema_version": 2,
        "target_entry": "badSink",
        "target_signature": "void badSink(char *data)",
        "valid_domain": "C strings of length 0..49; free-text revision note: reviewer clarified NUL handling",
        "valid_behavior": "copy and print input",
        "forbidden_behavior": "write past dest[50]",
        "invalid_input_response": "exit 1 with exact stderr",
        "dest_capacity_bytes": 50,
        "max_valid_input_bytes": 49,
        "capacity_includes_terminator": True,
        "invalid_exit_code": 1,
        "invalid_stdout": "",
        "invalid_stderr": "ERROR: input exceeds destination capacity\n",
        "authorized_edits": ["add a capacity guard"],
        "allowed_calls": ["fprintf", "strcpy"],
        "environment_assumptions": ["Linux x86-64"],
        "required_obligations": [
            "identity", "structure", "interface", "behaviour", "symbolic", "reproducibility"
        ],
    }
    approved = client.post(
        f"/api/jobs/{job_id}/approve",
        json={
            "contract": contract,
            "contract_revision": 2,
            "manifest_sha256": "1" * 64,
            "reviewer_label": "test",
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["revision"] == 2
    reviewed_scope = approved.json()["sha256"]

    started = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 2,
            "manifest_sha256": "1" * 64,
            "idempotency_key": "e2e-1",
        },
    )
    assert started.status_code == 202, started.text
    run = started.json()
    assert run["reviewed_scope_sha256"] == reviewed_scope
    assert {item["route"] for item in run["attempts"]} == {"route_a", "route_b"}

    attempt = run["attempts"][0]
    evidence = client.get(f"/api/attempts/{attempt['id']}/evidence")
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["attempt_id"] == attempt["id"]
    assert evidence.json()["outcome"] is None


def test_obligations_and_artifacts(client: TestClient) -> None:
    created = _create(client)
    job_id = created["id"]

    obligations = client.get(f"/api/jobs/{job_id}/obligations")
    assert obligations.status_code == 200
    data = obligations.json()
    assert set(data) == {"A", "B"}
    # DEFECT-A fix, not a relaxation: the controller serves only persisted
    # obligation rows, so a job with no attempts gets empty groups and must never
    # invent a V0 "passed" placeholder.
    assert data == {"A": [], "B": []}
    assert "passed" not in obligations.text
    assert not CJK.search(obligations.text)

    # The same holds once attempts exist but the worker has persisted no rows.
    _approve_current(client, job_id)
    started = client.post(f"/api/jobs/{job_id}/start")
    assert started.status_code == 202
    queued = client.get(f"/api/jobs/{job_id}/obligations")
    assert queued.status_code == 200
    assert queued.json() == {"A": [], "B": []}
    assert "passed" not in queued.text

    artifacts = client.get(f"/api/jobs/{job_id}/artifacts")
    assert artifacts.status_code == 200
    assert artifacts.json()["A"][0]["sha256"] == created["original_sha256"]


def test_cancel_and_restart_state(client: TestClient, tmp_path, monkeypatch) -> None:
    job_id = _create(client)["id"]
    _approve_current(client, job_id)
    started = client.post(f"/api/jobs/{job_id}/start")
    assert started.status_code == 202

    cancelled = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancelled.status_code == 202
    snapshot = client.get(f"/api/jobs/{job_id}").json()
    assert snapshot["phase"] == "cancelled"
    assert snapshot["cancelled"] is True

    blocked = client.post(f"/api/jobs/{job_id}/start")
    assert blocked.status_code == 409

    # A new client (new connections) sees the same persisted state.
    restarted = TestClient(app, base_url="http://127.0.0.1")
    assert restarted.get(f"/api/jobs/{job_id}").json()["phase"] == "cancelled"


def test_missing_job_returns_404(client: TestClient) -> None:
    response = client.get("/api/jobs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found."


def test_non_ascii_form_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        data={**FORM, "defect": "stack buffer overflow"},
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
    )
    assert response.status_code == 201

    response = client.post(
        "/api/jobs",
        data={**FORM, "defect": "越界写"},
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
    )
    assert response.status_code == 422


def test_host_check(client: TestClient) -> None:
    response = client.get("/healthz", headers={"host": "evil.example"})
    assert response.status_code == 403


def test_job_scoped_artifact_is_verified_and_downloadable(client: TestClient) -> None:
    job_id = _create(client)["id"]
    artifact = client.get(f"/api/jobs/{job_id}/artifacts").json()["A"][0]

    meta = client.get(f"/api/jobs/{job_id}/artifacts/{artifact['id']}")
    assert meta.status_code == 200, meta.text
    payload = meta.json()
    assert payload["role"] == "original"
    assert payload["available"] is True
    assert payload["integrity"] == "sha256"
    assert payload["unavailable_reason"] == ""
    assert payload["job_content_url"].endswith(
        f"/api/jobs/{job_id}/artifacts/{artifact['id']}/download"
    )

    download = client.get(f"/api/jobs/{job_id}/artifacts/{artifact['id']}/download")
    assert download.status_code == 200
    assert download.content == b"\x7fELF demo bytes"
    assert "attachment" in download.headers.get("content-disposition", "")


def test_job_scoped_artifact_hides_other_jobs(client: TestClient) -> None:
    first = _create(client)["id"]
    second = _create(client)["id"]
    artifact = client.get(f"/api/jobs/{first}/artifacts").json()["A"][0]

    assert client.get(f"/api/jobs/{second}/artifacts/{artifact['id']}").status_code == 404
    assert (
        client.get(f"/api/jobs/{second}/artifacts/{artifact['id']}/download").status_code
        == 404
    )
    assert client.get(f"/api/jobs/{first}/artifacts/does-not-exist").status_code == 404


def test_tampered_artifact_bytes_are_never_served(client: TestClient, tmp_path) -> None:
    created = _create(client)
    job_id = created["id"]
    artifact = client.get(f"/api/jobs/{job_id}/artifacts").json()["A"][0]

    original = tmp_path / "var" / "jobs" / job_id / "original.bin"
    original.write_bytes(b"\x7fELF different bytes")

    listing = client.get(f"/api/jobs/{job_id}/artifacts").json()["A"][0]
    assert listing["available"] is False
    assert listing["unavailable_reason"] == "artifact size does not match the registered size"

    meta = client.get(f"/api/jobs/{job_id}/artifacts/{artifact['id']}")
    assert meta.status_code == 200
    assert meta.json()["available"] is False

    download = client.get(f"/api/jobs/{job_id}/artifacts/{artifact['id']}/download")
    assert download.status_code == 409
    assert "does not match" in download.json()["detail"]


def test_same_size_tamper_is_caught_by_hash(client: TestClient, tmp_path) -> None:
    created = _create(client)
    job_id = created["id"]
    artifact = client.get(f"/api/jobs/{job_id}/artifacts").json()["A"][0]

    original = tmp_path / "var" / "jobs" / job_id / "original.bin"
    original.write_bytes(b"\x7fELF DEMO BYTES")

    download = client.get(f"/api/jobs/{job_id}/artifacts/{artifact['id']}/download")
    assert download.status_code == 409
    assert download.json()["detail"] == "artifact sha256 does not match the registered hash"


def test_artifact_list_never_fabricates_a_placeholder(client: TestClient, tmp_path) -> None:
    created = _create(client)
    job_id = created["id"]

    from app.db.connection import connect

    connection = connect(tmp_path / "var")
    try:
        with connection:
            connection.execute("DELETE FROM artifacts WHERE job_id = ?", (job_id,))
    finally:
        connection.close()

    listing = client.get(f"/api/jobs/{job_id}/artifacts")
    assert listing.status_code == 200
    assert listing.json() == {"A": [], "B": []}


def test_visuals_report_registered_analysis_only(client: TestClient) -> None:
    job_id = _create(client)["id"]
    body = client.get(f"/api/jobs/{job_id}/visuals").json()
    assert body["available"] is False
    assert body["synthetic"] is False
    assert body["sources"] == []
    assert body["reason"]
