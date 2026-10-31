"""Tests for the real-store controller HTTP bridge (P0 wiring)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tools.controller_service import create_app

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (execution not implemented)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
}
PAYLOAD = b"\x7fELF controller bridge demo bytes"


def _create(client: TestClient) -> dict:
    response = client.post(
        "/api/jobs",
        data=FORM,
        files={"binary": ("sample.bin", PAYLOAD, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_job_is_persisted_by_the_real_store_and_not_demo(tmp_path):
    client = TestClient(create_app(tmp_path))
    created = _create(client)
    job_id = created["id"]
    assert len(created["original_sha256"]) == 64

    # Real persistence: SQLite store + original binary on disk.
    assert (tmp_path / "jobs" / job_id / "original.bin").read_bytes() == PAYLOAD
    assert (tmp_path / "app.db").exists()

    snapshot = client.get(f"/api/jobs/{job_id}").json()
    assert snapshot["demo"] is False
    assert snapshot["execution"] == "not_started"
    assert snapshot["phase"] == "draft"
    assert snapshot["original_sha256"] == created["original_sha256"]
    assert set(snapshot["routes"]) == {"A", "B"}
    assert snapshot["contract"]["revision"] == 1
    assert snapshot["contract"]["approved"] is False

    health = client.get("/healthz").json()
    assert health["backend"] == "controller"
    assert health["demo"] is False

    # A fresh app instance over the same data dir still sees the job.
    other = TestClient(create_app(tmp_path))
    assert other.get(f"/api/jobs/{job_id}").json()["id"] == job_id


def test_contract_revision_and_approval_are_bound(tmp_path):
    client = TestClient(create_app(tmp_path))
    job_id = _create(client)["id"]
    original = client.get(f"/api/jobs/{job_id}").json()["contract"]

    stale = client.post(
        f"/api/jobs/{job_id}/contract",
        data={"expected_revision": "9", "valid_domain": "Input length 0-32 bytes"},
    )
    assert stale.status_code == 409

    updated = client.post(
        f"/api/jobs/{job_id}/contract",
        data={
            "expected_revision": "1",
            "valid_domain": "Input length 0-32 bytes",
            "assumptions": "Single process",
        },
    )
    assert updated.status_code == 200
    contract = updated.json()
    assert contract["revision"] == 2
    assert contract["approved"] is False
    assert contract["sha256"] != original["sha256"]

    stale_approval = client.post(
        f"/api/jobs/{job_id}/approve",
        data={"expected_sha256": original["sha256"], "reviewer_label": "reviewer"},
    )
    assert stale_approval.status_code == 409

    approved = client.post(
        f"/api/jobs/{job_id}/approve",
        data={"expected_sha256": contract["sha256"], "reviewer_label": "reviewer"},
    )
    assert approved.status_code == 200
    assert approved.json()["approved"] is True

    started = client.post(f"/api/jobs/{job_id}/start")
    assert started.status_code == 409
    assert "not implemented" in started.json()["detail"]


def test_start_requires_approval_and_execution_is_honestly_missing(tmp_path):
    client = TestClient(create_app(tmp_path))
    job_id = _create(client)["id"]

    blocked = client.post(f"/api/jobs/{job_id}/start")
    assert blocked.status_code == 409
    assert "not approved" in blocked.json()["detail"]

    obligations = client.get(f"/api/jobs/{job_id}/obligations")
    assert obligations.status_code == 200
    assert obligations.json() == {"A": [], "B": []}

    visuals = client.get(f"/api/jobs/{job_id}/visuals")
    assert visuals.status_code == 200
    assert visuals.json()["available"] is False


def test_non_ascii_upload_is_rejected(tmp_path):
    client = TestClient(create_app(tmp_path))
    data = dict(FORM)
    data["defect"] = "栈缓冲区越界写入"
    response = client.post(
        "/api/jobs",
        data=data,
        files={"binary": ("sample.bin", PAYLOAD, "application/octet-stream")},
    )
    assert response.status_code == 422
