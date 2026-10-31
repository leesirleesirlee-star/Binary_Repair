"""Local controller mode tests (GUI <-> member-1 SQLite job store).

What this proves:
- The GUI can create a job through the real controller (`app/services/jobs.py`),
  with the original bytes and SHA-256 persisted under the data directory.
- The snapshot is honest: job identity is real, but Route A/B execution is not
  implemented, so phases stay `draft` and obligations stay `not_run`.
- The GUI suppresses the "start" action in local mode and the export remains
  English-only (language policy).
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.backend import BackendError, LocalBackend, _has_cjk_in_payload
from app.services import jobs as job_store

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (execution not implemented)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
}
PAYLOAD = b"\x7fELF local-controller demo bytes"


def _create(backend: LocalBackend) -> str:
    return backend.create_job(
        binary_bytes=PAYLOAD,
        filename="sample.bin",
        defect=FORM["defect"],
        valid_tests=FORM["valid_tests"],
        environment=FORM["environment"],
        valid_domain=FORM["valid_domain"],
        forbidden=FORM["forbidden"],
        invalid_outcome=FORM["invalid_outcome"],
        assumptions=FORM["assumptions"],
    )


def test_local_backend_persists_real_job(tmp_path):
    backend = LocalBackend(data_dir=tmp_path)
    job_id = _create(backend)

    assert (tmp_path / "app.db").exists()
    original = (tmp_path / "jobs" / job_id / "original.bin").read_bytes()
    assert original == PAYLOAD
    expected_sha = hashlib.sha256(PAYLOAD).hexdigest()

    record = job_store.get_job(job_id, data_dir=tmp_path)
    assert record is not None
    assert record.original_sha256 == expected_sha

    snapshot = backend.get_snapshot(job_id)
    assert snapshot["demo"] is False
    assert snapshot["data_source"] == "local-controller"
    assert snapshot["execution"] == "not_started"
    assert snapshot["phase"] == "draft"
    assert snapshot["original_sha256"] == expected_sha
    assert snapshot["filename"] == "sample.bin"
    assert not _has_cjk_in_payload(snapshot)

    for key in ("A", "B"):
        route = snapshot["routes"][key]
        assert route["outcome"] is None
        assert all(stage["state"] == "pending" for stage in route["stages"])


def test_local_backend_reports_honest_obligations(tmp_path):
    backend = LocalBackend(data_dir=tmp_path)
    job_id = _create(backend)

    obligations = backend.get_obligations(job_id)
    for key in ("A", "B"):
        rows = obligations[key]
        assert rows[0]["id"] == "V0"
        assert rows[0]["result"] == "passed"
        assert all(row["result"] == "not_run" for row in rows[1:])
        assert not _has_cjk_in_payload(rows)

    bundle = backend.export_bundle(job_id)
    assert bundle["demo"] is False
    assert bundle["content_language"] == "en"
    assert "not production approval" in bundle["note"]
    assert not _has_cjk_in_payload(bundle)


def test_local_backend_execution_is_not_implemented(tmp_path):
    backend = LocalBackend(data_dir=tmp_path)
    job_id = _create(backend)

    with pytest.raises(BackendError) as excinfo:
        backend.start(job_id)
    assert excinfo.value.status_code == 409
    assert "not implement" in excinfo.value.detail

    with pytest.raises(BackendError) as excinfo:
        backend.cancel(job_id)
    assert excinfo.value.status_code == 409


def test_local_backend_unknown_job(tmp_path):
    with pytest.raises(BackendError) as excinfo:
        LocalBackend(data_dir=tmp_path).get_snapshot("does-not-exist")
    assert excinfo.value.status_code == 404


def test_gui_local_mode_uses_real_controller(tmp_path, monkeypatch):
    import app.main as gui

    backend = LocalBackend(data_dir=tmp_path)
    monkeypatch.setattr(gui, "BACKEND", backend)
    monkeypatch.setattr(gui, "BACKEND_MODE", "local")

    client = TestClient(gui.app, base_url="http://127.0.0.1")
    assert client.get("/?token=" + gui.SESSION_TOKEN).status_code == 200
    created = client.post(
        "/jobs",
        data={**FORM, "csrf": gui.CSRF_TOKEN},
        files={"binary": ("sample.bin", PAYLOAD, "application/octet-stream")},
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text
    location = created.headers["location"]
    job_id = location.rstrip("/").rsplit("/", 1)[-1]

    page = client.get(location)
    assert page.status_code == 200
    assert 'data-testid="start-unavailable"' in page.text
    assert "尚未提供修复执行接口" in page.text

    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["demo"] is False
    assert status["execution"] == "not_started"
    assert len(status["original_sha256"]) == 64

    export = client.get(f"/api/jobs/{job_id}/export")
    assert export.status_code == 200
    assert not _has_cjk_in_payload(json.loads(export.text))

    setup = client.get("/jobs/new")
    assert "local controller" in setup.text

    health = client.get("/healthz")
    assert health.status_code == 200
    assert not _has_cjk_in_payload(json.loads(health.text))
