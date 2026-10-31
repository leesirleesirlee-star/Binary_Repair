"""Contract revision and approval-binding tests (PRD F1, Technical Design section 6).

Rules covered:
- Editing the contract creates a new revision and clears the approval.
- A stale ``expected_revision`` or a stale approval hash returns 409.
- Starting a run requires an approved contract.
- The GUI renders the conflict as a visible page, not an opaque JSON error.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.backend import BackendError, LocalBackend, MockBackend
from app.main import CSRF_TOKEN, SESSION_TOKEN, app as gui_app

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (test)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
}
PAYLOAD = b"\x7fELF contract demo bytes"


def _create(backend: MockBackend) -> str:
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


def test_mock_contract_revision_and_approval_binding():
    backend = MockBackend()
    job_id = _create(backend)
    original = backend.get_snapshot(job_id)["contract"]
    assert original["revision"] == 1
    assert original["approved"] is False

    with pytest.raises(BackendError) as stale_revision:
        backend.update_contract(
            job_id,
            expected_revision="9",
            valid_domain="Input length 0-32 bytes",
            forbidden="Out-of-bounds write",
            invalid_outcome="Return error code 1",
            assumptions="Single process",
        )
    assert stale_revision.value.status_code == 409

    revised = backend.update_contract(
        job_id,
        expected_revision="1",
        valid_domain="Input length 0-32 bytes",
        forbidden="Out-of-bounds write",
        invalid_outcome="Return error code 1",
        assumptions="Single process",
    )
    assert revised["revision"] == 2
    assert revised["approved"] is False
    assert revised["sha256"] != original["sha256"]

    with pytest.raises(BackendError) as stale_approval:
        backend.approve_contract(
            job_id, expected_sha256=original["sha256"], reviewer_label="reviewer"
        )
    assert stale_approval.value.status_code == 409

    approved = backend.approve_contract(
        job_id, expected_sha256=revised["sha256"], reviewer_label="reviewer"
    )
    assert approved["approved"] is True
    assert approved["reviewer_label"] == "reviewer"

    backend.start(job_id)
    assert backend.get_snapshot(job_id)["phase"] != "draft"

    # Editing the contract again must invalidate the approval and block new runs.
    third = backend.update_contract(
        job_id,
        expected_revision="2",
        valid_domain="Input length 0-16 bytes",
        forbidden="Out-of-bounds write",
        invalid_outcome="Return error code 1",
        assumptions="Single process",
    )
    assert third["revision"] == 3
    assert third["approved"] is False

    with pytest.raises(BackendError) as not_approved:
        backend.start(job_id)
    assert not_approved.value.status_code == 409


def test_local_backend_contract_revision_persists(tmp_path):
    backend = LocalBackend(data_dir=tmp_path)
    job_id = _create(backend)
    original = backend.get_snapshot(job_id)["contract"]

    revised = backend.update_contract(
        job_id,
        expected_revision="1",
        valid_domain="Input length 0-32 bytes",
        forbidden="Out-of-bounds write",
        invalid_outcome="Return error code 1",
        assumptions="Single process",
    )
    assert revised["revision"] == 2

    approved = backend.approve_contract(
        job_id, expected_sha256=revised["sha256"], reviewer_label="reviewer"
    )
    assert approved["approved"] is True

    # The revision and approval are persisted in the controller job store.
    stored = backend.get_snapshot(job_id)["contract"]
    assert stored["revision"] == 2
    assert stored["approved"] is True
    assert stored["sha256"] == revised["sha256"]

    # Execution is still not implemented, so start keeps returning 409.
    with pytest.raises(BackendError) as not_implemented:
        backend.start(job_id)
    assert not_implemented.value.status_code == 409


def test_gui_renders_contract_conflicts_as_pages():
    client = TestClient(gui_app, base_url="http://127.0.0.1")
    assert client.get("/?token=" + SESSION_TOKEN).status_code == 200
    created = client.post(
        "/jobs",
        data={**FORM, "csrf": CSRF_TOKEN},
        files={"binary": ("sample.bin", PAYLOAD, "application/octet-stream")},
        follow_redirects=False,
    )
    assert created.status_code == 303
    job_id = created.headers["location"].rstrip("/").rsplit("/", 1)[-1]

    # Stale revision: the page renders the 409 with a visible error, not JSON.
    conflict = client.post(
        f"/jobs/{job_id}/contract",
        data={"expected_revision": "9", "valid_domain": "x", "csrf": CSRF_TOKEN},
    )
    assert conflict.status_code == 409
    assert 'data-testid="job-error"' in conflict.text
    assert "Contract revision is stale" in conflict.text

    updated = client.post(
        f"/jobs/{job_id}/contract",
        data={
            "expected_revision": "1",
            "valid_domain": "Input length 0-32 bytes",
            "forbidden": "Out-of-bounds write",
            "invalid_outcome": "Return error code 1",
            "assumptions": "Single process",
            "csrf": CSRF_TOKEN,
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303
    contract = client.get(f"/api/jobs/{job_id}").json()["contract"]
    assert contract["revision"] == 2
    assert contract["approved"] is False

    stale_approval = client.post(
        f"/jobs/{job_id}/approve",
        data={"expected_sha256": "0" * 64, "reviewer_label": "reviewer", "csrf": CSRF_TOKEN},
    )
    assert stale_approval.status_code == 409

    approved = client.post(
        f"/jobs/{job_id}/approve",
        data={
            "expected_sha256": contract["sha256"],
            "reviewer_label": "reviewer",
            "csrf": CSRF_TOKEN,
        },
        follow_redirects=False,
    )
    assert approved.status_code == 303

    started = client.post(
        f"/jobs/{job_id}/start",
        data={"csrf": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert started.status_code == 303

    # Reviewer labels are uploaded data: non-ASCII must be rejected.
    rejected = client.post(
        f"/jobs/{job_id}/approve",
        data={
            "expected_sha256": contract["sha256"],
            "reviewer_label": "评审",
            "csrf": CSRF_TOKEN,
        },
    )
    assert rejected.status_code == 422
