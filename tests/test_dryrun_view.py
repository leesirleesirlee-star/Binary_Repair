"""Dry-run evidence view tests: raw V0-V5 only, no computed outcome."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main


def _client() -> TestClient:
    client = TestClient(main.app, base_url="http://127.0.0.1")
    assert client.get("/?token=" + main.SESSION_TOKEN).status_code == 200
    return client


def test_dry_run_page_renders_raw_gates_without_outcome():
    page = _client().get("/dryrun/route-a")
    assert page.status_code == 200
    text = page.text
    assert "HOST DRY-RUN" in text
    assert "不是 formal gate 证据" in text
    assert "V0" in text and "V1" in text and "V5" in text
    assert "pass" in text and "inconclusive" in text and "not_run" in text
    assert "c4a159" in text
    assert "尚无 decision.json" in text
    assert "UI 不计算六种 outcome" in text
    # The UI must not invent a success verdict.
    assert "成功（声明范围内）" not in text
