"""Provider entry point with typed failures (verification-design §4.3.4).

``binaryrepair_ai.provider`` returns failures as values, which is what the job
controller wants. Callers that prefer exceptions (workers, tests, CLI) use
``require_candidate(result)``, which raises ``ProviderFailure`` carrying a stable
``FailureReason``.

The four failure families the verification slice will test:

    timeout   -> FailureReason.TIMEOUT
    quota     -> FailureReason.QUOTA (insufficient balance) or BUDGET (local cap)
    empty     -> FailureReason.EMPTY
    malformed -> FailureReason.MALFORMED_JSON (and SCHEMA for structurally wrong JSON)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from binaryrepair_ai.provider import DeepSeekProvider, ProviderResult
from binaryrepair_ai.schemas import CandidateV1

__all__ = [
    "DeepSeekProvider",
    "FailureReason",
    "ProviderFailure",
    "ProviderResult",
    "failure_from_result",
    "require_candidate",
]


class FailureReason(str, Enum):
    TIMEOUT = "provider_timeout"
    RATE_LIMIT = "provider_rate_limited"
    QUOTA = "provider_insufficient_balance"
    BUDGET = "budget_exceeded"
    AUTH = "provider_auth_failed"
    EMPTY = "empty_content"
    MALFORMED_JSON = "invalid_json"
    SCHEMA = "schema_invalid"
    IDENTITY = "identity_mismatch"
    SCOPE = "scope_violation"
    CONTEXT_TOO_LARGE = "context_too_large"
    NETWORK = "provider_network_error"
    SERVER = "provider_server_error"
    BAD_REQUEST = "provider_bad_request"
    UNAUTHORIZED_DATA = "unauthorized_data_transfer"
    UNKNOWN = "unknown_provider_failure"


class ProviderFailure(Exception):
    """A reportable provider failure. Never contains the API key or raw prompt."""

    def __init__(
        self,
        reason: FailureReason,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable
        self.details = details or {}

    @property
    def reason_code(self) -> str:
        return self.reason.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason.value,
            "message": str(self),
            "retryable": self.retryable,
            "details": self.details,
        }


_BY_VALUE = {reason.value: reason for reason in FailureReason}


def failure_from_result(result: ProviderResult) -> ProviderFailure | None:
    """Return the typed failure for a failed result, or None when it succeeded."""
    if result.ok or result.failure is None:
        return None
    reason = _BY_VALUE.get(result.failure.reason_code, FailureReason.UNKNOWN)
    return ProviderFailure(
        reason,
        result.failure.message,
        retryable=result.failure.retryable,
        details=dict(result.failure.details),
    )


def require_candidate(result: ProviderResult) -> CandidateV1:
    """Return the candidate or raise ``ProviderFailure``. Never fabricates one."""
    failure = failure_from_result(result)
    if failure is not None:
        raise failure
    assert result.candidate is not None
    return result.candidate
