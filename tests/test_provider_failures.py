"""Six typed provider failures, one test per reason.

Member 5's contract (docs/route-b/02-member-5-candidate-provider.md):
every provider failure is a ``ProviderFailure`` carrying a ``FailureReason``;
no failure may produce a candidate. These tests use a fake transport only:
no API key, no network, no cost.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.provider import FailureReason, ProviderFailure, require_candidate
from binaryrepair_ai.client import DeepSeekClient
from binaryrepair_ai.provider import DeepSeekProvider
from binaryrepair_ai.settings import Settings
from tests.test_provider import FakeTransport, content_response, ok_response
from tests.test_schemas import cand_a, ctx_a


def _provider(items, *, budget_usd: float = 2.0) -> DeepSeekProvider:
    settings = Settings(
        api_key="sk-test-not-real",
        model="deepseek-flash",
        budget_usd=budget_usd,
        max_retries=1,
    )
    transport = FakeTransport(items)
    client = DeepSeekClient(settings, transport=transport, sleep=lambda _s: None)
    return DeepSeekProvider(settings, client=client)


def _timeout() -> list:
    return [httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")]


def _quota() -> list:
    return [httpx.Response(402, text="Insufficient Balance")]


def _empty() -> list:
    return [content_response(""), content_response("  ")]


def _malformed_json() -> list:
    return [content_response("this is not json")]


def _schema_invalid() -> list:
    bad = cand_a()
    bad["parameters"] = {"capacity": 16, "error_return": -1, "unexpected": 3}
    return [ok_response(bad)]


CASES = [
    pytest.param("timeout", _timeout(), 2.0, FailureReason.TIMEOUT, id="timeout"),
    pytest.param("quota", _quota(), 2.0, FailureReason.QUOTA, id="quota"),
    pytest.param("budget", [], 1e-9, FailureReason.BUDGET, id="budget"),
    pytest.param("empty", _empty(), 2.0, FailureReason.EMPTY, id="empty"),
    pytest.param(
        "malformed", _malformed_json(), 2.0, FailureReason.MALFORMED_JSON, id="malformed"
    ),
    pytest.param("schema", _schema_invalid(), 2.0, FailureReason.SCHEMA, id="schema"),
]


@pytest.mark.parametrize("case,items,budget,expected", CASES)
def test_provider_failure_is_typed(
    case: str, items: list, budget: float, expected: FailureReason
) -> None:
    result = _provider(items, budget_usd=budget).request_candidate(ctx_a())

    # A failure never yields a candidate.
    assert result.ok is False
    assert result.candidate is None
    assert result.failure is not None

    with pytest.raises(ProviderFailure) as exc:
        require_candidate(result)

    assert exc.value.reason is expected
    assert exc.value.reason_code == expected.value
    assert str(exc.value)  # human-readable message is present
    assert result.record.status != "accepted_for_validation"


def test_failure_reason_enum_has_all_six() -> None:
    for name in ("TIMEOUT", "QUOTA", "BUDGET", "EMPTY", "MALFORMED_JSON", "SCHEMA"):
        assert hasattr(FailureReason, name)


def test_success_is_not_wrapped_as_failure() -> None:
    result = _provider([ok_response(cand_a())]).request_candidate(ctx_a())
    candidate = require_candidate(result)
    assert candidate.template_id == "guard_capacity"
