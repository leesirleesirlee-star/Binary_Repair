"""Contract-v1 browser API tests against the synthetic GUI backend."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import demo, main
from app.backend import MockBackend

V1 = {"X-BinaryRepair-Contract-Version": "1"}
TOKEN = "test-session-token"


@pytest.fixture()
def client(monkeypatch):
    demo.JOBS.clear()
    monkeypatch.setattr(main, "BACKEND", MockBackend())
    monkeypatch.setattr(main, "SESSION_TOKEN", TOKEN)
    monkeypatch.setattr(main, "CSRF_TOKEN", "csrf-test")
    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.get(f"/ui?token={TOKEN}", follow_redirects=False)
    return client


def create_job(client: TestClient) -> dict:
    response = client.post(
        "/api/jobs",
        headers={**V1, "x-csrf-token": "csrf-test"},
        files={"binary": ("sample.elf", b"\x7fELFdemo", "application/octet-stream")},
        data={"defect": "Bounds check missing"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_session_requires_v1_and_returns_csrf(client):
    response = client.get("/api/session", headers=V1)
    assert response.status_code == 200
    assert response.headers["X-BinaryRepair-Contract-Version"] == "1"
    assert response.headers["Cache-Control"] == "no-store"
    assert "X-BinaryRepair-Contract-Version" in response.headers["Vary"]
    assert response.json() == {"session": True, "csrf_token": "csrf-test"}


def test_unsupported_version_is_rejected_without_echo(client):
    response = client.get("/api/session", headers={"X-BinaryRepair-Contract-Version": "2"})
    assert response.status_code == 400
    assert response.json()["code"] == "unsupported_contract_version"
    assert "X-BinaryRepair-Contract-Version" not in response.headers


def test_new_routes_require_the_version_header(client):
    response = client.get("/api/jobs?limit=50")
    assert response.status_code == 400
    assert response.json()["code"] == "unsupported_contract_version"


def test_write_requires_csrf(client):
    response = client.post(
        "/api/jobs",
        headers=V1,
        files={"binary": ("sample.elf", b"\x7fELFdemo", "application/octet-stream")},
        data={"defect": "Bounds check missing"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "request_forbidden"


def test_pending_typed_contract_backend_does_not_drop_create_attachment(client):
    for contract, status, code in (
        ("", 422, "invalid_input"),
        ("{}", 409, "invalid_transition"),
        (" " * (16 * 1024 + 1), 413, "payload_too_large"),
    ):
        response = client.post(
            "/api/jobs", headers={**V1, "x-csrf-token": "csrf-test"},
            files={"binary": ("synthetic.elf", b"\x7fELFsynthetic", "application/octet-stream")},
            data={"defect": "Synthetic typed contract", "contract": contract},
        )
        assert response.status_code == status, response.text
        assert response.json()["code"] == code
    assert client.get("/api/jobs?limit=50", headers=V1).json()["jobs"] == []


def test_welcome_and_both_destinations_are_served(client):
    welcome = client.get("/welcome")
    assert welcome.status_code == 200
    assert "BinaryRepair" in welcome.text
    assert "/ui/chat/" in welcome.text and "/ui/" in welcome.text

    chat = client.get("/ui/chat/")
    assert chat.status_code == 200
    assert "chat/chat-app.mjs" in chat.text

    archive = client.get("/ui/")
    assert archive.status_code == 200
    assert "workbench/workbench.mjs" in archive.text


def test_job_list_and_snapshot_shape(client):
    job = create_job(client)
    listed = client.get("/api/jobs?limit=50", headers=V1).json()["jobs"]
    assert listed[0]["id"] == job["id"]
    assert listed[0]["defect"] == "Bounds check missing"
    assert listed[0]["phase"] == "draft"

    snapshot = client.get(f"/api/jobs/{job['id']}", headers=V1).json()
    assert snapshot["demo"] is True
    assert snapshot["data_source"] == "synthetic"
    assert snapshot["execution"] == {
        "run_id": None,
        "status": "not_started",
        "terminal": False,
        "reason_code": None,
        "started_at": None,
        "finished_at": None,
    }
    assert snapshot["filename"] == "sample.elf"
    contract = snapshot["contract"]
    assert contract["review_binding"]["original_sha256"]
    assert contract["review_binding"]["test_manifest_sha256"]
    assert len(contract["review_binding_digest"]) == 64
    assert contract["approved"] is False


def test_contract_revision_advances_and_stale_revision_reports_current(client):
    job = create_job(client)
    snapshot = client.get(f"/api/jobs/{job['id']}", headers=V1).json()
    revision = snapshot["contract"]["revision"]
    response = client.post(
        f"/api/jobs/{job['id']}/contract",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"expected_revision": revision, "valid_domain": "Input length 0-64 bytes"},
    )
    assert response.status_code == 200
    assert response.json()["revision"] == revision + 1

    stale = client.post(
        f"/api/jobs/{job['id']}/contract",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"expected_revision": revision, "valid_domain": "Different"},
    )
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "stale_revision"
    assert body["current"]["revision"] == revision + 1


def test_approve_uses_the_review_binding_digest_not_the_content_hash(client):
    job = create_job(client)
    contract = client.get(f"/api/jobs/{job['id']}", headers=V1).json()["contract"]
    stale = client.post(
        f"/api/jobs/{job['id']}/approve",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"expected_sha256": contract["sha256"], "reviewer_label": "reviewer"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_approval"

    response = client.post(
        f"/api/jobs/{job['id']}/approve",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"expected_sha256": contract["review_binding_digest"], "reviewer_label": "reviewer"},
    )
    assert response.status_code == 200
    assert response.json()["approved"] is True


def test_start_requires_current_approval_and_the_digest(client):
    job = create_job(client)
    contract = client.get(f"/api/jobs/{job['id']}", headers=V1).json()["contract"]
    blocked = client.post(
        f"/api/jobs/{job['id']}/start",
        headers={**V1, "x-csrf-token": "csrf-test", "Idempotency-Key": "key-one"},
        json={"expected_sha256": contract["review_binding_digest"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "stale_approval"

    client.post(
        f"/api/jobs/{job['id']}/approve",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"expected_sha256": contract["review_binding_digest"], "reviewer_label": "reviewer"},
    )
    started = client.post(
        f"/api/jobs/{job['id']}/start",
        headers={**V1, "x-csrf-token": "csrf-test", "Idempotency-Key": "key-one"},
        json={"expected_sha256": contract["review_binding_digest"]},
    )
    assert started.status_code == 200
    assert started.json()["run_id"] == "demo-run-" + job["id"]

    again = client.post(
        f"/api/jobs/{job['id']}/start",
        headers={**V1, "x-csrf-token": "csrf-test", "Idempotency-Key": "key-two"},
        json={"expected_sha256": contract["review_binding_digest"]},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"


def test_cancel_names_the_current_run_and_is_repeatable(client):
    job = create_job(client)
    contract = client.get(f"/api/jobs/{job['id']}", headers=V1).json()["contract"]
    client.post(
        f"/api/jobs/{job['id']}/approve",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"expected_sha256": contract["review_binding_digest"], "reviewer_label": "reviewer"},
    )
    run_id = client.post(
        f"/api/jobs/{job['id']}/start",
        headers={**V1, "x-csrf-token": "csrf-test", "Idempotency-Key": "key-one"},
        json={"expected_sha256": contract["review_binding_digest"]},
    ).json()["run_id"]
    first = client.post(
        f"/api/jobs/{job['id']}/cancel",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"run_id": run_id},
    )
    assert first.status_code == 200
    assert first.json()["execution"]["status"] == "cancelled"

    other = client.post(
        f"/api/jobs/{job['id']}/cancel",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"run_id": "not-the-current-run"},
    )
    assert other.status_code == 409
    assert other.json()["code"] == "invalid_transition"


def test_evaluation_is_pending_after_start(client):
    job = create_job(client)
    contract = client.get(f"/api/jobs/{job['id']}", headers=V1).json()["contract"]
    client.post(
        f"/api/jobs/{job['id']}/approve",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"expected_sha256": contract["review_binding_digest"], "reviewer_label": "reviewer"},
    )
    client.post(
        f"/api/jobs/{job['id']}/start",
        headers={**V1, "x-csrf-token": "csrf-test", "Idempotency-Key": "key-one"},
        json={"expected_sha256": contract["review_binding_digest"]},
    )
    evaluation = client.get(f"/api/jobs/{job['id']}/evaluation", headers=V1).json()
    assert evaluation["finalized"] is False
    assert evaluation["verdict"] == {"outcome": None, "reason": None}
    assert {row["code"] for row in evaluation["limitations"]} >= {"evaluation_pending"}


def test_llm_plan_fails_closed_without_transfer_authorization(client):
    job = create_job(client)
    response = client.post(
        f"/api/jobs/{job['id']}/plan",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={"mode": "llm"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "plan_transfer_not_authorized"

    local = client.post(
        f"/api/jobs/{job['id']}/plan",
        headers={**V1, "x-csrf-token": "csrf-test"},
        json={},
    )
    assert local.status_code == 200
    assert local.json()["plan"]["mode"] == "deterministic"
    assert local.json()["agent"]["fallback"] is False


def test_legacy_no_header_shape_is_preserved(client):
    job = create_job(client)
    legacy = client.get(f"/api/jobs/{job['id']}")
    assert legacy.status_code == 200
    assert legacy.headers.get("X-BinaryRepair-Contract-Version") is None
    body = legacy.json()
    assert "routes" in body and "contract" in body
    assert "review_binding_digest" not in body["contract"]
