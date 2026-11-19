from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import CSRF_TOKEN, SESSION_TOKEN, app

client = TestClient(app, base_url="http://127.0.0.1")
client.get("/?token=" + SESSION_TOKEN)
HEADERS = {"X-CSRF-Token": CSRF_TOKEN}
V1_HEADERS = {**HEADERS, "X-BinaryRepair-Contract-Version": "1"}


def _clean_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BINARYREPAIR_ASSISTANT_KEY_FILE", str(tmp_path / "assistant.key"))
    monkeypatch.delenv("BINARYREPAIR_ASSISTANT_PROVIDER_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("BINARYREPAIR_ASSISTANT_MODE", raising=False)


def test_key_api_never_returns_or_echoes_key(tmp_path: Path, monkeypatch) -> None:
    _clean_env(monkeypatch, tmp_path)
    raw_key = "sk-api-0123456789abcdef"

    response = client.post("/api/assistant/key", json={"key": raw_key}, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"]["configured"] is True
    assert body["key"]["source"] == "stored"
    assert raw_key not in response.text

    status = client.get("/api/assistant/key")
    assert status.status_code == 200
    assert status.json()["key"]["configured"] is True
    assert raw_key not in status.text

    cleared = client.delete("/api/assistant/key", headers=HEADERS)
    assert cleared.status_code == 200
    assert cleared.json()["key"]["configured"] is False
    assert raw_key not in cleared.text


def test_key_endpoints_require_csrf(tmp_path: Path, monkeypatch) -> None:
    _clean_env(monkeypatch, tmp_path)
    response = client.post("/api/assistant/key", json={"key": "sk-csrf-0123456789"})
    assert response.status_code == 403
    assert "sk-csrf" not in response.text


def test_conversation_key_save_returns_only_configuration_status(tmp_path: Path, monkeypatch) -> None:
    _clean_env(monkeypatch, tmp_path)
    raw_key = "sk-proj-synthetic-key-0123456789"
    response = client.post("/api/assistant/key", json={"key": raw_key}, headers=V1_HEADERS)
    assert response.status_code == 200
    assert response.headers["X-BinaryRepair-Contract-Version"] == "1"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"configured": True}
    assert client.get("/api/assistant/key", headers=V1_HEADERS).json()["configured"] is True


def test_key_test_route_uses_probe(tmp_path: Path, monkeypatch) -> None:
    _clean_env(monkeypatch, tmp_path)
    assistant_key_store = __import__("app.services.assistant_key_store", fromlist=["save_key"])
    assistant_key_store.save_key("sk-probe-0123456789")

    import app.main as main

    monkeypatch.setattr(main, "test_provider_connection", lambda: {"ok": True, "model": "fake-model"})
    response = client.post("/api/assistant/key/test", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "model": "fake-model"}


def test_key_ui_is_password_input_and_has_no_prefilled_value() -> None:
    from app.main import BASE_DIR

    template = (BASE_DIR / "templates" / "agent.html").read_text(encoding="utf-8")
    assert 'id="assistant-key-input"' in template
    assert 'type="password"' in template
    assert 'id="assistant-key-save"' in template
    assert 'id="assistant-key-clear"' in template
    assert 'id="assistant-key-input" value=' not in template


def test_provider_probe_uses_the_configured_client() -> None:
    from app.services import assistant_llm

    class Result:
        content = '{"ok": true}'
        model = "fake-model"
        request_id = None
        usage = {"total_tokens": 2}

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def chat_json(self, messages, *, max_tokens=8, thinking=False):
            self.calls.append((messages, max_tokens, thinking))
            return Result()

    fake = FakeClient()
    result = assistant_llm.test_provider_connection(fake)
    assert result["ok"] is True
    assert result["model"] == "fake-model"
    assert fake.calls and fake.calls[0][1] == 8
