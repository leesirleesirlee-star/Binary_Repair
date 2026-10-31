"""Smoke test: pages and status endpoint open; Host check is enforced."""

from fastapi.testclient import TestClient

from app.main import CSRF_TOKEN, SESSION_TOKEN, app

client = TestClient(app, base_url="http://127.0.0.1")
assert client.get("/?token=" + SESSION_TOKEN).status_code == 200

CONTRACT_FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (test: execution disabled)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
    "csrf": CSRF_TOKEN,
}


def _create_job() -> str:
    response = client.post(
        "/jobs",
        data=CONTRACT_FORM,
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def test_landing_page():
    response = client.get("/")
    assert response.status_code == 200
    assert "二进制漏洞修复" in response.text
    assert 'data-testid="landing-cta"' in response.text
    assert 'href="/jobs/new"' in response.text


def test_setup_page():
    response = client.get("/jobs/new")
    assert response.status_code == 200
    assert "新建任务与契约复核" in response.text
    assert 'data-testid="setup-form"' in response.text


def test_host_check_rejects_foreign_host():
    response = client.get("/jobs/new", headers={"host": "evil.example"})
    assert response.status_code == 403


def _approve_current_contract(job_id: str) -> None:
    contract = client.get(f"/api/jobs/{job_id}").json()["contract"]
    response = client.post(
        f"/jobs/{job_id}/approve",
        data={
            "expected_sha256": contract["sha256"],
            "reviewer_label": "reviewer",
            "csrf": CSRF_TOKEN,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def test_create_job_and_three_screens():
    job_id = _create_job()

    comparison = client.get(f"/jobs/{job_id}")
    assert comparison.status_code == 200
    assert "Route A" in comparison.text and "Route B" in comparison.text

    status = client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    data = status.json()
    assert data["demo"] is True
    assert data["phase"] == "draft"
    assert len(data["original_sha256"]) == 64
    assert data["contract"]["approved"] is False

    # Starting before approval must be rejected with the explicit 409 conflict page.
    blocked = client.post(
        f"/jobs/{job_id}/start", data={"csrf": CSRF_TOKEN}, follow_redirects=False
    )
    assert blocked.status_code == 409
    assert 'data-testid="job-error"' in blocked.text

    _approve_current_contract(job_id)
    started = client.post(
        f"/jobs/{job_id}/start", data={"csrf": CSRF_TOKEN}, follow_redirects=False
    )
    assert started.status_code == 303
    assert client.get(f"/api/jobs/{job_id}").json()["phase"] != "draft"

    evidence = client.get(f"/jobs/{job_id}/evidence")
    assert evidence.status_code == 200
    assert "V0" in evidence.text and "diagnostic candidate" in evidence.text.lower()

    export = client.get(f"/api/jobs/{job_id}/export")
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
    assert '"demo": true' in export.text


def test_missing_job_returns_404():
    assert client.get("/jobs/does-not-exist").status_code == 404
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_cancel_marks_cancelled():
    job_id = _create_job()
    _approve_current_contract(job_id)
    client.post(
        f"/jobs/{job_id}/start", data={"csrf": CSRF_TOKEN}, follow_redirects=False
    )
    client.post(
        f"/jobs/{job_id}/cancel", data={"csrf": CSRF_TOKEN}, follow_redirects=False
    )
    data = client.get(f"/api/jobs/{job_id}").json()
    assert data["cancelled"] is True
    assert data["routes"]["A"]["outcome"] == "cancelled"
