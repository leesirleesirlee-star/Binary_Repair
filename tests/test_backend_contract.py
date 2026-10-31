"""Backend contract tests (2026-10-18).

Covers HttpBackend against a mocked controller transport and the local stub controller."""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.backend import BackendError, HttpBackend, _has_cjk_in_payload
from tools.stub_backend import app as stub_app

ENGLISH_FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (test: execution disabled)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
}

SNAPSHOT = {
    "id": "job-abc123",
    "demo": False,
    "filename": "sample.bin",
    "original_sha256": "0" * 64,
    "defect": ENGLISH_FORM["defect"],
    "valid_tests": ["valid: hello", "boundary: 8 bytes"],
    "environment": ENGLISH_FORM["environment"],
    "created_at": "2026-10-18T10:00:00Z",
    "phase": "validating",
    "cancelled": False,
    "contract": {
        "revision": 1,
        "sha256": "1" * 64,
        "valid_domain": "Input length 0-64 bytes",
        "forbidden": "Out-of-bounds write to the target buffer",
        "invalid_outcome": "Return error code 1 without out-of-bounds access",
        "assumptions": ["Single process", "Stack protector disabled"],
    },
    "routes": {
        "A": {
            "name": "Route A - Direct instruction repair",
            "phase": "validating",
            "outcome": None,
            "outcome_reason": "",
            "stages": [{"name": "screening", "state": "done"}],
            "tests": {"passed": 6, "total": 8},
            "verification": "not_run",
            "compatibility": "passed",
            "changed_regions": [],
            "manual_interventions": 1,
            "ai_usage": "unavailable",
            "elapsed_s": 4.2,
        },
        "B": {
            "name": "Route B - Recovered C reintegration",
            "phase": "generating",
            "outcome": None,
            "outcome_reason": "",
            "stages": [{"name": "screening", "state": "done"}],
            "tests": {"passed": 8, "total": 8},
            "verification": "not_run",
            "compatibility": "not_run",
            "changed_regions": [],
            "manual_interventions": 0,
            "ai_usage": "8.2k input / 1.4k output tokens",
            "elapsed_s": 3.0,
        },
    },
}

OBLIGATIONS = {
    "A": [
        {
            "id": "V0",
            "kind": "Identity and preconditions",
            "description": "Input hash and contract revision match",
            "required": True,
            "result": "passed",
            "scope": "frozen job inputs",
            "reason": "controller data: passed",
        }
    ],
    "B": [],
}

ARTIFACTS = {
    "A": [{"name": "candidate-A.elf", "role": "candidate binary", "sha256": "a" * 64}],
    "B": [],
}


def _make_http_backend(handler) -> tuple[HttpBackend, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording_handler)
    backend = HttpBackend("http://controller.test:8000", transport=transport)
    return backend, requests


def _controller_router(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path == "/api/jobs":
        return httpx.Response(201, json={"id": "job-abc123", "original_sha256": "0" * 64})
    if request.method == "GET" and path == "/api/jobs/job-abc123":
        return httpx.Response(200, json=SNAPSHOT)
    if request.method == "POST" and path == "/api/jobs/job-abc123/start":
        return httpx.Response(202, json={"ok": True})
    if request.method == "POST" and path == "/api/jobs/job-abc123/cancel":
        return httpx.Response(202, json={"ok": True})
    if request.method == "POST" and path == "/api/jobs/job-abc123/contract":
        return httpx.Response(200, json={**SNAPSHOT["contract"], "revision": 2, "approved": False})
    if request.method == "POST" and path == "/api/jobs/job-abc123/approve":
        return httpx.Response(
            200,
            json={**SNAPSHOT["contract"], "approved": True, "reviewer_label": "reviewer"},
        )
    if request.method == "GET" and path == "/api/jobs/job-abc123/obligations":
        return httpx.Response(200, json=OBLIGATIONS)
    if request.method == "GET" and path == "/api/jobs/job-abc123/artifacts":
        return httpx.Response(200, json=ARTIFACTS)
    if request.method == "GET" and path == "/api/jobs/job-abc123/visuals":
        return httpx.Response(200, json={"available": True, "routes": {"A": {"mode": "asm"}}})
    return httpx.Response(404, json={"detail": "Job not found."})


def test_http_backend_maps_controller_contract():
    backend, requests = _make_http_backend(_controller_router)

    job_id = backend.create_job(
        binary_bytes=b"\x7fELF demo bytes",
        filename="sample.bin",
        defect=ENGLISH_FORM["defect"],
        valid_tests=ENGLISH_FORM["valid_tests"],
        environment=ENGLISH_FORM["environment"],
        valid_domain=ENGLISH_FORM["valid_domain"],
        forbidden=ENGLISH_FORM["forbidden"],
        invalid_outcome=ENGLISH_FORM["invalid_outcome"],
        assumptions=ENGLISH_FORM["assumptions"],
    )
    assert job_id == "job-abc123"

    snapshot = backend.get_snapshot(job_id)
    assert snapshot["phase"] == "validating"
    assert set(snapshot["routes"]) == {"A", "B"}
    assert snapshot["routes"]["A"]["tests"]["passed"] == 6

    backend.start(job_id)
    backend.cancel(job_id)
    assert backend.get_obligations(job_id)["A"][0]["id"] == "V0"
    assert backend.get_artifacts(job_id)["A"][0]["name"] == "candidate-A.elf"

    updated = backend.update_contract(
        job_id,
        expected_revision="1",
        valid_domain="Input length 0-32 bytes",
        forbidden="Out-of-bounds write",
        invalid_outcome="Return error code 1",
        assumptions="Single process",
    )
    assert updated["revision"] == 2
    approved = backend.approve_contract(
        job_id,
        expected_sha256=SNAPSHOT["contract"]["sha256"],
        reviewer_label="reviewer",
    )
    assert approved["approved"] is True

    visuals_payload = backend.get_visuals(job_id)
    assert visuals_payload["available"] is True

    paths = [(request.method, request.url.path) for request in requests]
    assert ("POST", "/api/jobs") in paths
    assert ("GET", "/api/jobs/job-abc123") in paths
    assert ("POST", "/api/jobs/job-abc123/start") in paths
    assert ("POST", "/api/jobs/job-abc123/cancel") in paths
    assert ("POST", "/api/jobs/job-abc123/contract") in paths
    assert ("POST", "/api/jobs/job-abc123/approve") in paths
    assert ("GET", "/api/jobs/job-abc123/visuals") in paths
    assert ("GET", "/api/jobs/job-abc123/obligations") in paths
    assert ("GET", "/api/jobs/job-abc123/artifacts") in paths

    create_request = requests[0]
    assert create_request.headers["content-type"].startswith("multipart/form-data")
    body = create_request.content
    assert b'name="defect"' in body

    bundle = backend.export_bundle(job_id)
    assert bundle["demo"] is False
    assert bundle["content_language"] == "en"
    assert "approval" in bundle["note"]
    assert not _has_cjk_in_payload(bundle)


def test_http_backend_unwraps_wrapped_contract_response():
    """Member 1's final controller returns {"id": ..., "contract": {...}}."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/jobs/job-abc123/contract":
            return httpx.Response(
                200,
                json={
                    "id": "job-abc123",
                    "contract": {
                        "revision": 2,
                        "sha256": "2" * 64,
                        "approved": False,
                    },
                },
            )
        return httpx.Response(404, json={"detail": "Job not found."})

    backend, _ = _make_http_backend(handler)
    try:
        contract = backend.update_contract(
            "job-abc123",
            expected_revision="1",
            valid_domain="Input length 0-32 bytes",
            forbidden="Out-of-bounds write",
            invalid_outcome="Return error code 1",
            assumptions="Single process",
        )
        assert contract["revision"] == 2
    except BackendError as exc:
        raise AssertionError(f"unexpected BackendError: {exc}") from exc


def test_http_backend_maps_errors():
    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Job not found."})

    backend, _ = _make_http_backend(not_found)
    try:
        backend.get_snapshot("missing")
    except BackendError as exc:
        assert exc.status_code == 404
        assert exc.detail == "Job not found."
    else:
        raise AssertionError("expected BackendError")

    def network_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    backend, _ = _make_http_backend(network_failure)
    try:
        backend.get_snapshot("job-abc123")
    except BackendError as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("expected BackendError")

    def chinese_payload(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"phase": "draft", "routes": {}, "defect": "越界写"})

    backend, _ = _make_http_backend(chinese_payload)
    try:
        backend.get_snapshot("job-abc123")
    except BackendError as exc:
        assert "language policy" in exc.detail
    else:
        raise AssertionError("expected BackendError")


def test_stub_controller_matches_gui_contract():
    client = TestClient(stub_app, base_url="http://127.0.0.1")

    created = client.post(
        "/api/jobs",
        data=ENGLISH_FORM,
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    snapshot = client.get(f"/api/jobs/{job_id}")
    assert snapshot.status_code == 200
    payload = snapshot.json()
    for key in ("id", "phase", "routes", "contract", "original_sha256"):
        assert key in payload
    assert not _has_cjk_in_payload(payload)

    blocked = client.post(f"/api/jobs/{job_id}/start")
    assert blocked.status_code == 409

    stale_contract = client.post(
        f"/api/jobs/{job_id}/contract",
        data={
            "expected_revision": "9",
            "valid_domain": "Input length 0-32 bytes",
            "forbidden": "Out-of-bounds write",
            "invalid_outcome": "Return error code 1",
            "assumptions": "Single process",
        },
    )
    assert stale_contract.status_code == 409

    updated = client.post(
        f"/api/jobs/{job_id}/contract",
        data={
            "expected_revision": "1",
            "valid_domain": "Input length 0-32 bytes",
            "forbidden": "Out-of-bounds write",
            "invalid_outcome": "Return error code 1",
            "assumptions": "Single process",
        },
    )
    assert updated.status_code == 200
    contract = updated.json()
    assert contract["revision"] == 2
    assert contract["approved"] is False

    stale_approval = client.post(
        f"/api/jobs/{job_id}/approve",
        data={"expected_sha256": "0" * 64, "reviewer_label": "reviewer"},
    )
    assert stale_approval.status_code == 409

    approved = client.post(
        f"/api/jobs/{job_id}/approve",
        data={"expected_sha256": contract["sha256"], "reviewer_label": "reviewer"},
    )
    assert approved.status_code == 200
    assert approved.json()["approved"] is True

    started = client.post(f"/api/jobs/{job_id}/start")
    assert started.status_code == 202

    obligations = client.get(f"/api/jobs/{job_id}/obligations")
    assert obligations.status_code == 200
    assert obligations.json()["A"][0]["id"] == "V0"
    assert not _has_cjk_in_payload(obligations.json())

    artifacts = client.get(f"/api/jobs/{job_id}/artifacts")
    assert artifacts.status_code == 200
    assert artifacts.json()["A"][0]["name"] == "candidate-A.elf"

    missing = client.get("/api/jobs/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Job not found."
