from __future__ import annotations

import json

import httpx

from binaryrepair_ai.client import DeepSeekClient
from binaryrepair_ai.errors import (
    REASON_BUDGET,
    REASON_EMPTY_CONTENT,
    REASON_IDENTITY,
    REASON_INVALID_JSON,
    REASON_SCHEMA,
)
from binaryrepair_ai.provider import DeepSeekProvider
from binaryrepair_ai.settings import Settings
from tests.test_schemas import cand_a, ctx_a


class FakeTransport:
    def __init__(self, items):
        self.items = list(items)
        self.posts: list[dict] = []
        self.gets = 0

    def post(self, url, *, headers, json, timeout):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, *, headers, timeout):
        self.gets += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok_response(obj) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(obj)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 250,
                "total_tokens": 1450,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 1200,
            },
        },
    )


def content_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "deepseek-flash",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def make_provider(items, **settings_over):
    settings = Settings(
        api_key="sk-test-not-real",
        model="deepseek-flash",
        max_retries=1,
        budget_usd=settings_over.pop("budget_usd", 2.0),
        **settings_over,
    )
    transport = FakeTransport(items)
    client = DeepSeekClient(settings, transport=transport, sleep=lambda _s: None)
    provider = DeepSeekProvider(settings, client=client)
    return provider, transport


def test_accepts_valid_candidate_and_records_cost() -> None:
    provider, transport = make_provider([ok_response(cand_a())])
    result = provider.request_candidate(ctx_a())

    assert result.ok and result.candidate is not None
    assert result.candidate.template_id == "guard_capacity"
    assert result.record.status == "accepted_for_validation"
    assert result.record.usage["prompt_tokens"] == 1200
    assert result.record.model_returned == "deepseek-flash"
    assert result.record.estimated_cost_usd is not None
    assert result.record.estimated_cost_usd > 0
    assert "sk-test-not-real" not in json.dumps(result.record.as_dict())

    payload = transport.posts[0]["json"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0.0
    assert payload["stream"] is False


def test_thinking_mode_payload_omits_temperature() -> None:
    provider, transport = make_provider([ok_response(cand_a())])
    provider.request_candidate(ctx_a(), thinking=True)
    payload = transport.posts[0]["json"]
    assert payload["thinking"] == {"type": "enabled"}
    assert "temperature" not in payload


def test_empty_content_is_retried_once_then_reported() -> None:
    provider, transport = make_provider([content_response(""), content_response("   ")])
    result = provider.request_candidate(ctx_a())

    assert not result.ok
    assert result.failure is not None
    assert result.failure.reason_code == REASON_EMPTY_CONTENT
    assert len(transport.posts) == 2
    assert result.record.attempts == 2


def test_rate_limit_then_success() -> None:
    provider, transport = make_provider(
        [httpx.Response(429, text="slow down"), ok_response(cand_a())]
    )
    result = provider.request_candidate(ctx_a())
    assert result.ok
    assert result.record.attempts == 2
    assert len(transport.posts) == 2


def test_insufficient_balance_is_not_retried() -> None:
    provider, transport = make_provider([httpx.Response(402, text="no balance")])
    result = provider.request_candidate(ctx_a())
    assert not result.ok
    assert result.failure is not None
    assert result.failure.reason_code == "provider_insufficient_balance"
    assert len(transport.posts) == 1


def test_invalid_json_is_rejected() -> None:
    provider, _ = make_provider([content_response("this is not json at all")])
    result = provider.request_candidate(ctx_a())
    assert not result.ok
    assert result.failure is not None
    assert result.failure.reason_code == REASON_INVALID_JSON


def test_schema_invalid_is_rejected() -> None:
    bad = cand_a()
    bad["parameters"] = {"capacity": 16, "error_return": -1, "unexpected": 3}
    provider, _ = make_provider([ok_response(bad)])
    result = provider.request_candidate(ctx_a())
    assert not result.ok
    assert result.failure is not None
    assert result.failure.reason_code == REASON_SCHEMA


def test_identity_mismatch_is_rejected() -> None:
    bad = cand_a()
    bad["contract_sha256"] = "0" * 64
    provider, _ = make_provider([ok_response(bad)])
    result = provider.request_candidate(ctx_a())
    assert not result.ok
    assert result.failure is not None
    assert result.failure.reason_code == REASON_IDENTITY


def test_budget_blocks_before_any_http_call() -> None:
    provider, transport = make_provider([], budget_usd=0.000001)
    result = provider.request_candidate(ctx_a())
    assert not result.ok
    assert result.failure is not None
    assert result.failure.reason_code == REASON_BUDGET
    assert transport.posts == []


def test_prose_wrapped_json_is_recovered() -> None:
    text = "Here is the json:\n" + json.dumps(cand_a()) + "\nThanks."
    provider, _ = make_provider([content_response(text)])
    result = provider.request_candidate(ctx_a())
    assert result.ok


def test_model_log_is_jsonl_and_excludes_key(tmp_path) -> None:
    log = tmp_path / "model_calls.jsonl"
    settings = Settings(api_key="sk-secret-value", model="deepseek-flash", budget_usd=5.0)
    transport = FakeTransport([ok_response(cand_a())])
    provider = DeepSeekProvider(
        settings,
        client=DeepSeekClient(settings, transport=transport, sleep=lambda _s: None),
        log_path=log,
    )
    provider.request_candidate(ctx_a())

    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["status"] == "accepted_for_validation"
    assert "sk-secret-value" not in log.read_text()
    assert len(record["prompt_sha256"]) == 64
