"""Report sync v1: evidence state propagation and controller artifact proxying.

These tests pin the honest-reporting rules for the GUI side:
- ``evidence: not_persisted`` must survive into the export verbatim;
- an empty obligation table is never reported as a pass;
- artifact metadata keeps the controller ``content_url``;
- GUI proxies artifact bytes instead of linking to a local path that 404s.
"""

from __future__ import annotations

import json
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.backend import BackendError, HttpBackend, build_export_bundle
from app.main import SESSION_TOKEN, app

REAL_SNAPSHOT = {
    "id": "job-real",
    "demo": False,
    "data_source": "controller-http",
    "execution": "queued",
    "filename": "original.elf",
    "original_sha256": "a" * 64,
    "defect": "strcpy in stack_buf does not check length",
    "valid_tests": ["valid: hello", "invalid: 64 bytes"],
    "environment": "Ubuntu x86-64 VM",
    "created_at": "2026-11-05T08:31:00+00:00",
    "phase": "queued",
    "cancelled": False,
    "contract": {
        "revision": 2,
        "sha256": "b" * 64,
        "approved": True,
        "approved_at": "2026-11-05T08:31:25+00:00",
        "reviewer_label": "m2-probe",
        "valid_domain": "Input length 0-64 bytes",
        "forbidden": "Out-of-bounds write",
        "invalid_outcome": "Return error code 1",
        "assumptions": ["Single process"],
    },
    "routes": {
        "A": {
            "name": "route_a",
            "phase": "queued",
            "outcome": None,
            "outcome_reason": "",
            "verdict": {"outcome": None, "reason": None, "failing_obligation": None, "detail": None},
            "obligations": [],
            "evidence": "not_persisted",
            "verification": "not_run",
            "compatibility": "not_run",
            "stages": [{"name": "screening", "state": "pending"}],
            "tests": {"passed": 0, "total": 0},
            "changed_regions": [],
            "elapsed_s": 0,
            "manual_interventions": 0,
            "ai_usage": "unavailable",
        },
        "B": {
            "name": "route_b",
            "phase": "queued",
            "outcome": None,
            "outcome_reason": "",
            "verdict": {"outcome": None, "reason": None, "failing_obligation": None, "detail": None},
            "obligations": [],
            "evidence": "not_persisted",
            "verification": "not_run",
            "compatibility": "not_run",
            "stages": [],
            "tests": {"passed": 0, "total": 0},
            "changed_regions": [],
            "elapsed_s": 0,
            "manual_interventions": 0,
            "ai_usage": "unavailable",
        },
    },
    "run": {
        "id": "run-real",
        "phase": "queued",
        "manifest_sha256": "c" * 64,
        "reviewed_scope_sha256": "d" * 64,
    },
    "verdict": {"outcome": None, "reason": None, "failing_obligation": None, "detail": None},
}

REAL_ARTIFACTS = {
    "A": [
        {
            "id": "artifact-original",
            "name": "original.bin",
            "role": "original",
            "sha256": "a" * 64,
            "size_bytes": 26792,
            "available": True,
            "content_url": "/api/artifacts/artifact-original/download",
            "media_type": "application/octet-stream",
        }
    ],
    "B": [],
}


def _bundle() -> dict:
    return build_export_bundle(
        REAL_SNAPSHOT,
        {"A": [], "B": []},
        REAL_ARTIFACTS,
        demo=False,
        generated_at="2026-11-05T00:00:00+00:00",
    )


def test_evidence_state_is_propagated_verbatim():
    bundle = _bundle()
    assert bundle["routes"]["A"]["evidence_state"] == "not_persisted"
    assert bundle["routes"]["B"]["evidence_state"] == "not_persisted"


def test_empty_obligations_are_reported_as_missing_evidence():
    bundle = _bundle()
    assert bundle["routes"]["A"]["obligations"] == []
    # An empty table must never look like "nothing failed".
    assert bundle["not_run"] == []
    joined = " ".join(bundle["limitations"])
    assert "no obligation results have been received" in joined
    assert "not a pass" in joined


def test_run_block_and_sync_block_are_present():
    bundle = _bundle()
    assert bundle["run"]["id"] == "run-real"
    assert bundle["run"]["manifest_sha256"] == "c" * 64
    assert bundle["sync"]["data_source"] == "controller-http"
    assert bundle["sync"]["execution"] == "queued"
    assert bundle["sync"]["gui_computes_outcome"] is False


def test_artifact_keeps_content_url_and_availability():
    bundle = _bundle()
    artifact = bundle["artifacts"][0]
    assert artifact["artifact_id"] == "artifact-original"
    assert artifact["content_url"] == "/api/artifacts/artifact-original/download"
    assert artifact["available"] is True
    assert artifact["download"] is True


def test_artifact_without_id_is_not_downloadable():
    bundle = build_export_bundle(
        REAL_SNAPSHOT,
        {"A": [], "B": []},
        {"A": [{"name": "original.bin", "sha256": "e" * 64, "available": True}], "B": []},
        demo=False,
    )
    artifact = bundle["artifacts"][0]
    assert artifact["artifact_id"] is None
    assert artifact["available"] is False
    assert artifact["download"] is False


def test_reason_is_null_not_empty_string():
    bundle = _bundle()
    assert bundle["routes"]["A"]["reason"] is None


def _http_backend(handler) -> HttpBackend:
    return HttpBackend(base_url="http://controller", transport=httpx.MockTransport(handler))


def test_http_backend_fetches_artifact_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/artifacts/abc/download"
        return httpx.Response(
            200,
            content=b"\x7fELF-bytes",
            headers={
                "content-type": "application/octet-stream",
                "content-disposition": 'attachment; filename="candidate.elf"',
            },
        )

    backend = _http_backend(handler)
    content, media_type, filename = backend.get_artifact_bytes("abc")
    assert content == b"\x7fELF-bytes"
    assert media_type == "application/octet-stream"
    assert filename == "candidate.elf"


def test_http_backend_missing_artifact_is_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Artifact not found."})

    backend = _http_backend(handler)
    with pytest.raises(BackendError) as excinfo:
        backend.get_artifact_bytes("missing")
    assert excinfo.value.status_code == 404


@pytest.mark.parametrize(("verified_digest", "status"), [
    (hashlib.sha256(b"Registered synthetic bytes.").hexdigest(), 409),
    (None, 503),
])
def test_versioned_download_refuses_unverified_transport_bytes(verified_digest, status):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/jobs/job-a/artifacts/artifact-a/download"
        assert request.headers["X-BinaryRepair-Contract-Version"] == "1"
        headers = {} if verified_digest is None else {"X-BinaryRepair-Artifact-SHA256": verified_digest}
        return httpx.Response(200, content=b"Unexpected synthetic bytes.", headers=headers)

    backend = _http_backend(handler)
    try:
        with pytest.raises(BackendError) as error:
            backend.get_verified_artifact_bytes("artifact-a", job_id="job-a")
        assert error.value.status_code == status
    finally:
        backend.close()


def _client() -> TestClient:
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/?token=" + SESSION_TOKEN).status_code == 200
    return client


class _FakeArtifactBackend:
    mode = "http"
    base_url = "http://controller"

    def get_artifact_metadata(self, artifact_id: str) -> dict:
        return {
            "id": artifact_id,
            "name": "candidate.elf",
            "role": "candidate",
            "sha256": "f" * 64,
            "size_bytes": 12,
            "media_type": "application/octet-stream",
        }

    def get_artifact_bytes(self, artifact_id: str):
        return b"\x7fELFproxy", "application/octet-stream", "candidate.elf"


def test_gui_proxies_artifact_metadata_and_bytes(monkeypatch):
    monkeypatch.setattr(main_module, "BACKEND", _FakeArtifactBackend())
    client = _client()

    meta = client.get("/api/artifacts/artifact-1")
    assert meta.status_code == 200
    assert meta.json()["name"] == "candidate.elf"

    download = client.get("/api/artifacts/artifact-1/download")
    assert download.status_code == 200
    assert download.content == b"\x7fELFproxy"
    assert "candidate.elf" in download.headers["content-disposition"]


def test_gui_artifact_routes_404_when_backend_cannot_serve(monkeypatch):
    class _NoArtifacts:
        mode = "http"
        base_url = "http://controller"

    monkeypatch.setattr(main_module, "BACKEND", _NoArtifacts())
    client = _client()
    assert client.get("/api/artifacts/artifact-1").status_code == 404
    assert client.get("/api/artifacts/artifact-1/download").status_code == 404


def test_bundle_stays_english_ascii():
    bundle = _bundle()
    payload = json.dumps(bundle, ensure_ascii=False)
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in payload)
