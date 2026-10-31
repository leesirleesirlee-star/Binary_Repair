"""Language-policy tests (2026-10-18).

The UI may be Chinese; upload/API/export data must be English ASCII."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import CSRF_TOKEN, SESSION_TOKEN, app

client = TestClient(app, base_url="http://127.0.0.1")
assert client.get("/?token=" + SESSION_TOKEN).status_code == 200

CJK_PATTERN = re.compile(
    r"[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (test: execution disabled)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
    "csrf": CSRF_TOKEN,
}


def _create_job(filename: str = "sample.bin", overrides: dict | None = None) -> str:
    data = dict(FORM)
    if overrides:
        data.update(overrides)
    response = client.post(
        "/jobs",
        data=data,
        files={"binary": (filename, b"\x7fELF demo bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def test_api_and_export_are_english_only():
    """API and export JSON must contain no CJK characters."""
    job_id = _create_job()
    client.post(
        f"/jobs/{job_id}/start", data={"csrf": CSRF_TOKEN}, follow_redirects=False
    )

    api = client.get(f"/api/jobs/{job_id}")
    assert api.status_code == 200
    assert not CJK_PATTERN.search(api.text), "API response contains CJK characters"

    export = client.get(f"/api/jobs/{job_id}/export")
    assert export.status_code == 200
    assert not CJK_PATTERN.search(export.text), "Export JSON contains CJK characters"
    assert '"content_language": "en"' in export.text
    assert '"demo": true' in export.text


def test_evidence_page_data_is_english_while_ui_is_chinese():
    job_id = _create_job()
    page = client.get(f"/jobs/{job_id}/evidence")
    assert page.status_code == 200
    assert "证据与导出" in page.text
    assert "Identity and preconditions" in page.text
    assert "Bounded symbolic security" in page.text


def test_chinese_form_content_is_rejected():
    """Non-ASCII upload text is rejected with 422."""
    response = client.post(
        "/jobs",
        data={**FORM, "defect": "栈缓冲区越界写"},
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "English" in detail or "ASCII" in detail


def test_contract_field_rejected_when_chinese():
    response = client.post(
        "/jobs",
        data={**FORM, "forbidden": "越界写目标缓冲区"},
        files={"binary": ("sample.bin", b"\x7fELF demo bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 422


def test_uploaded_filename_is_sanitized_to_ascii():
    """Uploaded filenames are sanitized to ASCII."""
    job_id = _create_job(filename="样本.bin")
    data = client.get(f"/api/jobs/{job_id}").json()
    assert data["filename"].isascii()
    assert not CJK_PATTERN.search(data["filename"])


def test_ui_pages_remain_chinese():
    assert "新建任务与契约复核" in client.get("/jobs/new").text
    job_id = _create_job()
    assert "进度与对比" in client.get(f"/jobs/{job_id}").text
