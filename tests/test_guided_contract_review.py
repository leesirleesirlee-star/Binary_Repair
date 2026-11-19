"""Exercise the conversation contract against the real controller using synthetic files."""

import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import controller_api, main
from app.backend import HttpBackend
from app.services import jobs
from app.services.worker import _executor_inputs

HEADERS = {"X-BinaryRepair-Contract-Version": "1", "x-csrf-token": "guided-csrf"}
ORIGINAL = b"\x7fELF synthetic contract test; do not execute"


@pytest.fixture
def clients(monkeypatch, tmp_path):
    monkeypatch.setattr(controller_api, "_data_dir", lambda: tmp_path)
    controller = TestClient(controller_api.app, base_url="http://127.0.0.1")
    backend = HttpBackend(
        "http://127.0.0.1", transport=httpx.MockTransport(
            lambda request: controller.request(
                request.method, str(request.url), headers=request.headers, content=request.read()
            )
        ),
    )
    monkeypatch.setattr(main, "BACKEND", backend)
    monkeypatch.setattr(main, "SESSION_TOKEN", "guided-session")
    monkeypatch.setattr(main, "CSRF_TOKEN", "guided-csrf")
    gui = TestClient(main.app, base_url="http://127.0.0.1")
    gui.get("/welcome?token=guided-session", follow_redirects=False)
    try:
        yield gui, controller, tmp_path
    finally:
        backend.close()
        gui.close()
        controller.close()


def create(gui):
    response = gui.post("/api/jobs", headers=HEADERS, data={
        "defect": "Synthetic review example", "guided_contract": "true",
        "valid_domain": "Known sample", "forbidden": "External side effects",
        "invalid_outcome": "Return a defined error", "assumptions": "Synthetic environment",
    }, files={"binary": ("synthetic.elf", ORIGINAL, "application/octet-stream")})
    assert response.status_code == 201, response.text
    return "/api/jobs/" + response.json()["id"]


def post(gui, path, payload, *, status=200, headers=None):
    response = gui.post(path, json=payload, headers={**HEADERS, **(headers or {})})
    assert response.status_code == status, response.text
    return response.json()


def snapshot(gui, path):
    response = gui.get(path, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def setup(revision, *, mode="none", original_sha256=None):
    original_sha256 = original_sha256 or hashlib.sha256(ORIGINAL).hexdigest()
    fields = {
        "target_entry": "synthetic_target", "target_signature": "int synthetic_target(void)",
        "authorized_edits": ["Reviewed target only"], "allowed_calls": [], "environment": "Synthetic",
        "valid_behavior": "Return the configured result", "input_mode": mode,
        "dest_capacity_bytes": 16, "capacity_includes_terminator": False,
    }
    manifests = {}
    if mode == "argv":
        fields.update(max_valid_input_bytes=8, invalid_exit_code=1, invalid_stdout="", invalid_stderr="")
    for kind in (("valid", "invalid") if mode == "argv" else ("valid",)):
        manifests[kind + ".json"] = json.dumps({
            "schema_version": 1, "case_id": "synthetic-review", "original_sha256": original_sha256,
            "tests": [{"id": kind, "kind": kind, "argv": [],
                       "expected": {"return_code": int(kind == "invalid"), "stdout": "", "stderr": ""}}],
        })
    return {"expected_revision": revision, "fields": fields, "manifests": manifests,
            "reviewer_label": "setup reviewer"}


def test_multilingual_review_is_bound_to_the_worker_contract(clients):
    gui, controller, data_dir = clients
    path = create(gui)
    initial = snapshot(gui, path)
    assert initial["technical_setup"]["blockers"] == [
        "repair_target", "execution_settings", "test_manifests",
    ]
    assert initial["contract"]["valid_domain"] == "Known sample"
    post(gui, path + "/approve", {
        "expected_sha256": initial["contract"]["review_binding_digest"], "reviewer_label": "reviewer",
    }, status=409)

    fields = {
        "valid_domain": "Sample A 或 B", "forbidden": "不得写入 external files",
        "invalid_outcome": "返回 error", "assumptions": "Local only\n合成数据",
    }
    saved = post(gui, path + "/contract", {"expected_revision": 1, **fields})
    assert saved["revision"] == 2
    assert post(gui, path + "/contract", {"expected_revision": 2, **fields}) == saved
    stale = post(gui, path + "/contract", {"expected_revision": 1, **fields}, status=409)
    assert stale["code"] == "stale_revision"
    assert stale["current"]["revision"] == 2

    post(gui, path + "/technical-setup", setup(2, original_sha256="0" * 64), status=422)
    configured = post(gui, path + "/technical-setup", setup(2, mode="argv"))
    reviewed = snapshot(gui, path)
    assert reviewed["technical_setup"]["blockers"] == []
    assert reviewed["contract"]["forbidden"] == fields["forbidden"]
    assert reviewed["contract"]["invalid_outcome"] == fields["invalid_outcome"]
    binding = configured["review_binding_digest"]
    post(gui, path + "/approve", {"expected_sha256": binding, "reviewer_label": "reviewer"})
    revised = post(gui, path + "/contract", {"expected_revision": 3, **fields, "valid_domain": "新版"})
    assert revised["revision"] == 4
    assert not snapshot(gui, path)["contract"]["approved"]
    assert controller.post(path + "/start").status_code == 409
    start_headers = {"Idempotency-Key": "synthetic-start"}
    post(gui, path + "/start", {"expected_sha256": binding}, status=409, headers=start_headers)
    new_binding = revised["review_binding_digest"]
    post(gui, path + "/approve", {"expected_sha256": new_binding, "reviewer_label": "reviewer"})
    started = post(gui, path + "/start", {"expected_sha256": new_binding}, headers=start_headers)
    assert post(gui, path + "/start", {"expected_sha256": new_binding}, headers=start_headers) == started
    assert started["phase"] == "queued"
    job_id = path.rsplit("/", 1)[-1]
    run = jobs.get_run(started["run_id"], data_dir=data_dir)
    _, contract_path = _executor_inputs(data_dir, job_id, run.id)
    contract_bytes = contract_path.read_bytes()
    contract = json.loads(contract_bytes)
    assert hashlib.sha256(contract_bytes).hexdigest() == revised["sha256"] == run.reviewed_scope_sha256
    assert contract["valid_domain"] == "新版"
    assert contract["forbidden_behavior"] == fields["forbidden"]
    assert contract["invalid_input_response"] == fields["invalid_outcome"]
    assert contract["environment_assumptions"] == reviewed["contract"]["assumptions"]
    assert jobs.get_contract(job_id, 3, data_dir=data_dir) is not None
    assert run.manifest_sha256 == reviewed["contract"]["review_binding"]["test_manifest_sha256"]


def test_no_input_omits_rejection_fields(clients):
    gui, _, data_dir = clients
    path = create(gui)
    configured = post(gui, path + "/technical-setup", setup(1))
    viewed = snapshot(gui, path)
    assert viewed["contract"]["invalid_outcome"] == ""
    assert viewed["technical_setup"]["fields"]["input_mode"] == "none"
    post(gui, path + "/approve", {
        "expected_sha256": configured["review_binding_digest"], "reviewer_label": "reviewer",
    })
    stored = jobs.get_contract(path.rsplit("/", 1)[-1], 2, data_dir=data_dir)
    body = json.loads(stored["body_json"])
    assert body["input_mode"] == "none"
    assert not any(key.startswith("invalid_") for key in body)
