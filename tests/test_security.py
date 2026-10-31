"""Local session and CSRF guard tests (Technical Design section 11)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import CSRF_TOKEN, SESSION_TOKEN, app

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello",
    "environment": "Ubuntu x86-64 VM (test)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write",
    "invalid_outcome": "Return error code 1",
    "assumptions": "Single process",
}


def _client() -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


def test_healthz_is_public_but_pages_require_a_session():
    client = _client()
    assert client.get("/jobs/new").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/?token=wrong-token").status_code == 401

    bootstrapped = _client()
    assert bootstrapped.get("/?token=" + SESSION_TOKEN).status_code == 200
    assert bootstrapped.get("/jobs/new").status_code == 200


def test_unauthenticated_browser_gets_a_help_page():
    client = _client()
    response = client.get("/jobs/new", headers={"accept": "text/html"})
    assert response.status_code == 401
    assert "需要本地会话" in response.text
    assert "?token=" in response.text


def test_csrf_token_is_required_for_post_forms():
    client = _client()
    client.get("/?token=" + SESSION_TOKEN)

    missing = client.post(
        "/jobs",
        data=FORM,
        files={"binary": ("sample.bin", b"\x7fELF demo", "application/octet-stream")},
        follow_redirects=False,
    )
    assert missing.status_code == 403
    assert "CSRF" in missing.json()["detail"]

    accepted = client.post(
        "/jobs",
        data={**FORM, "csrf": CSRF_TOKEN},
        files={"binary": ("sample.bin", b"\x7fELF demo", "application/octet-stream")},
        follow_redirects=False,
    )
    assert accepted.status_code == 303


def test_cross_origin_post_is_rejected():
    client = _client()
    client.get("/?token=" + SESSION_TOKEN)

    rejected = client.post(
        "/jobs",
        data={**FORM, "csrf": CSRF_TOKEN},
        files={"binary": ("sample.bin", b"\x7fELF demo", "application/octet-stream")},
        headers={"origin": "http://evil.example"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403
    assert "Cross-origin" in rejected.json()["detail"]
