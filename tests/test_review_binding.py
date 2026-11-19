"""Reviewed-scope binding regressions for the versioned lifecycle.

Covers the 2026-11-19 follow-up acceptance checks: the approved canonical
contract bytes and both digests agree with the run, a missing or placeholder
scope cannot authorize execution, and a legitimate legacy typed approval keeps
working. Synthetic data only; no binary is executed.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import api_v1, controller_api, main
from app.backend import BackendError, HttpBackend
from app.controller_api import app as controller_app
from app.db.connection import connect
from app.domain.contracts import SecurityContract
from app.services import jobs, worker
from tests.support_typed_contract import sample_typed_contract, typed_contract_form
from tools.audit_review_binding import audit


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setenv("BINARYREPAIR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BINARYREPAIR_CONTROLLER_TOKEN", raising=False)
    monkeypatch.setattr(main, "SESSION_TOKEN", "binding-session")
    monkeypatch.setattr(main, "CSRF_TOKEN", "binding-csrf")
    with TestClient(controller_app, base_url="http://127.0.0.1:8000") as controller:
        def forward(request):
            response = controller.request(
                request.method, str(request.url), headers=request.headers,
                content=request.read(),
            )
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content,
            )

        backend = HttpBackend(transport=httpx.MockTransport(forward))
        monkeypatch.setattr(main, "BACKEND", backend)
        try:
            with TestClient(main.app, base_url="http://127.0.0.1:8125") as client:
                client.cookies.set(main.SESSION_COOKIE, main.SESSION_TOKEN)
                client.headers.update({
                    api_v1.VERSION_HEADER: "1", "x-csrf-token": main.CSRF_TOKEN,
                })
                yield client
        finally:
            backend.close()


def _create(browser, *, typed: dict | None = None):
    data = {"defect": "Synthetic binding task.", "valid_tests": "Keep valid inputs."}
    if typed is not False:
        data["contract"] = json.dumps(typed or sample_typed_contract(), sort_keys=True)
    response = browser.post(
        "/api/jobs",
        files={"binary": ("synthetic.elf", b"\x7fELFsynthetic")},
        data=data,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _contract(browser, job_id):
    return browser.get(f"/api/jobs/{job_id}").json()["contract"]


@pytest.mark.parametrize(
    ("contract_fields", "status", "code"),
    [
        ({"contract": "{}"}, 422, "invalid_input"),
        (
            {"contract": typed_contract_form(), "valid_domain": "Conflicting scope."},
            422,
            "invalid_input",
        ),
        ({"contract": " " * (api_v1.MAX_FIELD_BYTES + 1)}, 413, "payload_too_large"),
    ],
)
def test_rejected_typed_contract_does_not_create_a_job(browser, tmp_path, contract_fields, status, code):
    response = browser.post(
        "/api/jobs",
        files={"binary": ("synthetic.elf", b"\x7fELFsynthetic")},
        data={"defect": "Synthetic typed contract validation.", **contract_fields},
    )
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert browser.get("/api/jobs?limit=50").json()["jobs"] == []
    original_files = list(tmp_path.glob("jobs/*/original.bin"))
    assert original_files == []


def _edit_and_approve(browser, job_id, valid_domain):
    revision = _contract(browser, job_id)["revision"]
    edited = browser.post(
        f"/api/jobs/{job_id}/contract",
        json={"expected_revision": revision, "valid_domain": valid_domain},
    )
    assert edited.status_code == 200, edited.text
    digest = _contract(browser, job_id)["review_binding_digest"]
    approved = browser.post(
        f"/api/jobs/{job_id}/approve",
        json={"expected_sha256": digest, "reviewer_label": "binding-reviewer"},
    )
    assert approved.status_code == 200, approved.text
    return digest


def _start(browser, job_id, digest):
    return browser.post(
        f"/api/jobs/{job_id}/start",
        json={"expected_sha256": digest},
        headers={"Idempotency-Key": str(uuid4())},
    )


def _legacy_job(browser) -> str:
    payload = main.BACKEND._request(
        "POST", "/api/jobs", where="legacy create",
        files={"binary": ("legacy.elf", b"\x7fELFlegacy")},
        data={"defect": "Historical synthetic job.", "valid_tests": "Keep valid inputs."},
    )
    return payload["id"]


def test_edited_scope_persists_the_exact_reviewed_canonical_contract(browser, tmp_path):
    job_id = _create(browser)
    digest = _edit_and_approve(browser, job_id, "Probe revised scope.")
    started = _start(browser, job_id, digest)
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]

    connection = connect(tmp_path)
    try:
        row = connection.execute(
            "SELECT * FROM contracts WHERE job_id = ? AND revision = 2", (job_id,),
        ).fetchone()
        run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        ack = connection.execute(
            "SELECT * FROM v1_run_starts WHERE run_id = ?", (run_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None and run is not None and ack is not None

    canonical = SecurityContract.from_dict(json.loads(row["body_json"]))
    assert row["body_json"] == canonical.canonical_json()
    assert canonical.valid_domain == "Probe revised scope."
    assert canonical.forbidden_behavior == sample_typed_contract()["forbidden_behavior"]
    body_sha = hashlib.sha256(row["body_json"].encode("utf-8")).hexdigest()
    assert row["sha256"] == canonical.sha256() == body_sha
    assert run["reviewed_scope_sha256"] == row["sha256"]
    assert run["contract_id"] == row["id"]
    assert ack["review_digest"] == digest

    meta = controller_api._read_meta(job_id)
    typed_sha = SecurityContract.from_dict(sample_typed_contract()).sha256()
    assert meta["approval"]["canonical_contract_sha256"] == row["sha256"]
    assert meta["approval"]["typed_contract_sha256"] == typed_sha
    current = _contract(browser, job_id)
    assert current["review_binding_digest"] == digest
    assert current["review_binding"]["canonical_contract_sha256"] == row["sha256"]

    report = audit(tmp_path)
    assert report["affected_jobs"] == []
    assert report["affected_runs"] == []


def test_missing_typed_scope_blocks_approval_and_both_start_entry_points(browser):
    job_id = _create(browser, typed=False)
    contract = _contract(browser, job_id)
    assert contract["review_binding"]["canonical_contract_sha256"] is None

    approved = browser.post(
        f"/api/jobs/{job_id}/approve",
        json={
            "expected_sha256": contract["review_binding_digest"],
            "reviewer_label": "binding-reviewer",
        },
    )
    assert approved.status_code == 409
    assert approved.json()["code"] == "invalid_transition"

    versioned = _start(browser, job_id, contract["review_binding_digest"])
    assert versioned.status_code == 409
    assert versioned.json()["code"] == "invalid_transition"

    with pytest.raises(BackendError) as legacy_start:
        main.BACKEND.start(job_id, idempotency_key="legacy-entry")
    assert legacy_start.value.status_code == 409

    with pytest.raises(BackendError) as runs_entry:
        main.BACKEND._request(
            "POST", f"/api/jobs/{job_id}/runs", where="runs entry",
            json={
                "contract_revision": 1,
                "manifest_sha256": contract["review_binding"]["test_manifest_sha256"],
                "idempotency_key": "runs-entry",
            },
        )
    assert runs_entry.value.status_code == 409


def test_same_revision_row_with_different_scope_blocks_approval(browser, tmp_path):
    job_id = _create(browser)
    jobs.approve_contract(
        job_id, contract_revision=1, reviewer_label="pre-existing", data_dir=tmp_path,
        contract_body=sample_typed_contract(valid_domain="Different approved scope"),
        manifest_sha256="0" * 64,
    )
    contract = _contract(browser, job_id)
    approved = browser.post(
        f"/api/jobs/{job_id}/approve",
        json={
            "expected_sha256": contract["review_binding_digest"],
            "reviewer_label": "binding-reviewer",
        },
    )
    assert approved.status_code == 409
    assert approved.json()["code"] == "invalid_transition"
    assert _start(browser, job_id, contract["review_binding_digest"]).status_code == 409


def test_placeholder_scope_cannot_reach_the_executor(browser, tmp_path, monkeypatch):
    job_id = _legacy_job(browser)
    legacy_contract = main.BACKEND._request(
        "GET", f"/api/jobs/{job_id}", where="legacy snapshot",
    )["contract"]
    approved = main.BACKEND._request(
        "POST", f"/api/jobs/{job_id}/approve", where="legacy approve",
        data={
            "expected_sha256": legacy_contract["sha256"],
            "reviewer_label": "legacy-reviewer",
        },
    )
    assert approved["approved"] is True
    started = main.BACKEND.start(job_id, idempotency_key="placeholder-run")
    assert started and started["run_id"]
    run_id = started["run_id"]

    connection = connect(tmp_path)
    try:
        run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        connection.close()
    assert run["reviewed_scope_sha256"] == jobs.placeholder_contract_sha()

    monkeypatch.setenv("BINARYREPAIR_ATTEMPT_CMD", "synthetic-executor")
    invoked: list[dict] = []

    def must_not_run(**kwargs):
        invoked.append(kwargs)
        pytest.fail("The executor must not be called for a placeholder scope.")

    monkeypatch.setattr(worker, "_run_executor", must_not_run)
    assert worker.run_once(data_dir=tmp_path) == 0
    assert invoked == []

    connection = connect(tmp_path)
    try:
        run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        attempts = connection.execute(
            "SELECT * FROM attempts WHERE run_id = ?", (run_id,),
        ).fetchall()
    finally:
        connection.close()
    assert run["phase"] == "finished"
    assert run["outcome"] == "error"
    assert run["reason"] == "unbound_review_scope"
    assert attempts and all(attempt["phase"] == "finished" for attempt in attempts)
    assert not (tmp_path / "jobs" / job_id / "run-verdict.json").exists()

    report = audit(tmp_path)
    assert any(row["run_id"] == run_id for row in report["affected_runs"])
    assert report["unusable_as_evidence"] is True


def test_legacy_typed_approval_still_binds_and_starts(browser, tmp_path):
    job_id = _legacy_job(browser)
    typed = sample_typed_contract()
    approved = main.BACKEND._request(
        "POST", f"/api/jobs/{job_id}/approve", where="legacy typed approve",
        json={
            "contract": typed,
            "contract_revision": 1,
            "manifest_sha256": "b" * 64,
            "reviewer_label": "legacy-typed",
        },
    )
    assert approved["approved"] is True
    started = main.BACKEND.start(job_id, idempotency_key="legacy-typed-key")
    assert started and started["run_id"]
    run_id = started["run_id"]
    expected_sha = SecurityContract.from_dict(typed).sha256()

    connection = connect(tmp_path)
    try:
        run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        connection.close()
    assert run["reviewed_scope_sha256"] == expected_sha
    ok, detail = jobs.reviewed_scope_state(job_id, run_id, data_dir=tmp_path)
    assert ok, detail


def test_audit_flags_managed_approval_without_canonical_binding(browser, tmp_path):
    job_id = _create(browser)
    _edit_and_approve(browser, job_id, "Bound scope.")
    with controller_api._job_write() as connection:
        meta = controller_api._read_meta(job_id, connection=connection)
        meta["approval"].pop("canonical_contract_sha256", None)
        meta["approval"].pop("typed_contract_sha256", None)
        controller_api._write_meta(job_id, meta, connection=connection)

    report = audit(tmp_path)
    flagged = {row["job_id"] for row in report["affected_jobs"]}
    assert job_id in flagged
    assert report["unusable_as_evidence"] is True
