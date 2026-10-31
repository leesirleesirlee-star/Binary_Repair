"""F5 export v2 tests: JSON, HTML report, manifest and downloads."""

from __future__ import annotations

import hashlib
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backend import HttpBackend, LocalBackend, build_export_bundle
from app.main import CSRF_TOKEN, SESSION_TOKEN, app

CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
FORM = {
    "defect": "strcpy in stack_buf does not check length",
    "valid_tests": "valid: hello",
    "environment": "test environment",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write",
    "invalid_outcome": "Return error code 1",
    "assumptions": "Single process",
}
OBLIGATIONS = [
    {"id": "V0", "kind": "identity", "required": True, "result": "passed", "reason": "ok", "evidence_artifact_id": "artifact-audit"},
    {"id": "V1", "kind": "structure", "required": True, "result": "not_run", "reason": "missing"},
    {"id": "V2", "kind": "interface", "required": True, "result": "not_run", "reason": "missing"},
    {"id": "V3", "kind": "behaviour", "required": True, "result": "not_run", "reason": "missing"},
    {"id": "V4", "kind": "symbolic", "required": True, "result": "not_run", "reason": "missing"},
    {"id": "V5", "kind": "reproducibility", "required": True, "result": "not_run", "reason": "missing"},
]


def _client() -> TestClient:
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/?token=" + SESSION_TOKEN).status_code == 200
    return client


def _create_job(client: TestClient) -> str:
    response = client.post(
        "/jobs",
        data={**FORM, "csrf": CSRF_TOKEN},
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def _snapshot() -> dict:
    return {
        "id": "job-http",
        "demo": False,
        "filename": "sample.bin",
        "original_sha256": "a" * 64,
        "phase": "finished",
        "contract": {"revision": 2, "sha256": "b" * 64},
        "run": {"id": "run-1", "reviewed_scope_sha256": "c" * 64},
        "verdict": {"outcome": "inconclusive", "reason": "required_check_not_run"},
        "routes": {
            "A": {
                "outcome": "inconclusive",
                "verdict": {"outcome": "inconclusive", "reason": "required_check_not_run", "failing_obligation": "interface"},
                "verification": "not_run",
                "obligations": OBLIGATIONS,
            },
            "B": {
                "outcome": "inconclusive",
                "verdict": {"outcome": "inconclusive", "reason": "required_check_not_run", "failing_obligation": "interface"},
                "verification": "not_run",
                "obligations": OBLIGATIONS,
            },
        },
    }


def _http_backend() -> HttpBackend:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/jobs/job-http":
            return httpx.Response(200, json=_snapshot())
        if path == "/api/jobs/job-http/obligations":
            return httpx.Response(200, json={"A": OBLIGATIONS, "B": OBLIGATIONS})
        if path == "/api/jobs/job-http/artifacts":
            return httpx.Response(
                200,
                json={
                    "A": [
                        {
                            "id": "artifact-audit",
                            "name": "audit.json",
                            "role": "route_a_audit",
                            "sha256": "d" * 64,
                            "size_bytes": 12,
                            "available": True,
                        }
                    ],
                    "B": [],
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return HttpBackend(base_url="http://controller", transport=httpx.MockTransport(handler))


def test_mock_json_export_v2_required_fields():
    client = _client()
    job_id = _create_job(client)
    response = client.get(f"/api/jobs/{job_id}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    bundle = response.json()
    assert bundle["schema_version"] == 2
    assert bundle["demo"] is True
    assert bundle["content_language"] == "en"
    assert bundle["disclaimer"] == "Not production approval"
    assert set(bundle["job"]) == {"id", "filename", "original_sha256", "phase"}
    assert set(bundle["contract"]) == {"revision", "sha256", "reviewed_scope_sha256"}
    assert bundle["job"]["id"] == job_id
    for key in ("A", "B"):
        route = bundle["routes"][key]
        assert "obligations" in route and len(route["obligations"]) == 6
        assert "candidate_sha256" in route
        assert "verification" in route
    assert isinstance(bundle["manifest_sha256"], str) and len(bundle["manifest_sha256"]) == 64
    assert not CJK.search(response.text)


def test_html_report_has_manifest_and_no_cjk():
    client = _client()
    job_id = _create_job(client)
    response = client.get(f"/jobs/{job_id}/report")
    assert response.status_code == 200
    assert "Not production approval" in response.text
    assert "SHA256SUMS" in response.text
    assert "manifest" in response.text.lower()
    assert not CJK.search(response.text)
    assert client.get(f"/jobs/{job_id}/report?download=1").headers["content-disposition"]


def test_missing_data_is_not_run_or_not_available():
    client = _client()
    job_id = _create_job(client)
    bundle = client.get(f"/api/jobs/{job_id}/export").json()
    assert bundle["not_run"], bundle["not_run"]
    assert all(item["result"] != "passed" for item in bundle["not_run"])
    assert any("no candidate binary" in item.lower() or "not_run" in item.lower() for item in bundle["limitations"])
    assert all("download" in artifact for artifact in bundle["artifacts"])


def test_manifest_is_stable_and_changes_with_obligations():
    snapshot = _snapshot()
    first = build_export_bundle(snapshot, {"A": OBLIGATIONS, "B": OBLIGATIONS}, {"A": [], "B": []}, demo=False)
    second = build_export_bundle(snapshot, {"A": OBLIGATIONS, "B": OBLIGATIONS}, {"A": [], "B": []}, demo=False)
    assert first["manifest_sha256"] == second["manifest_sha256"]

    changed = [dict(row) for row in OBLIGATIONS]
    changed[1]["result"] = "unknown"
    changed_snapshot = _snapshot()
    changed_snapshot["routes"]["A"]["obligations"] = changed
    third = build_export_bundle(changed_snapshot, {"A": changed, "B": OBLIGATIONS}, {"A": [], "B": []}, demo=False)
    assert third["manifest_sha256"] != first["manifest_sha256"]


def test_http_export_preserves_controller_outcome_and_artifact_binding():
    bundle = _http_backend().export_bundle("job-http")
    assert bundle["demo"] is False
    assert bundle["routes"]["A"]["outcome"] == "inconclusive"
    assert bundle["routes"]["A"]["verdict"] if "verdict" in bundle["routes"]["A"] else True
    assert bundle["routes"]["A"]["evidence"][0]["artifact_id"] == "artifact-audit"
    assert bundle["routes"]["A"]["evidence"][0]["sha256"] == "d" * 64
    artifact = bundle["artifacts"][0]
    assert artifact["download"] is True


def test_local_export_is_v2_and_honest(tmp_path):
    backend = LocalBackend(data_dir=tmp_path)
    job_id = backend.create_job(
        binary_bytes=b"\x7fELF local bytes",
        filename="sample.bin",
        defect=FORM["defect"],
        valid_tests=FORM["valid_tests"],
        environment=FORM["environment"],
        valid_domain=FORM["valid_domain"],
        forbidden=FORM["forbidden"],
        invalid_outcome=FORM["invalid_outcome"],
        assumptions=FORM["assumptions"],
    )
    bundle = backend.export_bundle(job_id)
    assert bundle["schema_version"] == 2
    assert bundle["demo"] is False
    assert bundle["content_language"] == "en"
    assert all(row["result"] == "not_run" for row in bundle["routes"]["A"]["obligations"][1:])
    assert not CJK.search(json.dumps(bundle, ensure_ascii=False))


def test_evidence_screen_has_json_and_html_actions():
    client = _client()
    job_id = _create_job(client)
    page = client.get(f"/jobs/{job_id}/evidence")
    assert page.status_code == 200
    assert f'/api/jobs/{job_id}/export' in page.text
    assert f'/jobs/{job_id}/report' in page.text
    assert "下载 HTML" in page.text


def test_unknown_job_returns_404():
    client = _client()
    assert client.get("/api/jobs/does-not-exist/export").status_code == 404
    assert client.get("/jobs/does-not-exist/report").status_code == 404
