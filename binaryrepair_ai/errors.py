"""Typed failures.

Design rule from the tech design: expected failure modes are *values* returned to the
job controller, not exceptions that the GUI has to guess about. Exceptions are reserved
for programming errors. A failure always carries a stable ``reason_code`` that the
acceptance engine can map to unsupported / rejected / inconclusive / error.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Stable reason codes. Keep them in one place; the GUI and ledger will display them.
REASON_AUTH = "provider_auth_failed"          # 401
REASON_BALANCE = "provider_insufficient_balance"  # 402
REASON_RATE_LIMIT = "provider_rate_limited"   # 429 (after retries)
REASON_SERVER = "provider_server_error"       # 5xx / 503 (after retries)
REASON_TIMEOUT = "provider_timeout"
REASON_NETWORK = "provider_network_error"
REASON_BAD_REQUEST = "provider_bad_request"   # 400 / 422
REASON_EMPTY_CONTENT = "empty_content"
REASON_INVALID_JSON = "invalid_json"
REASON_SCHEMA = "schema_invalid"
REASON_IDENTITY = "identity_mismatch"
REASON_SCOPE = "scope_violation"
REASON_BUDGET = "budget_exceeded"
REASON_CONTEXT_TOO_LARGE = "context_too_large"
REASON_UNAUTHORIZED_DATA = "unauthorized_data_transfer"


@dataclass(frozen=True)
class Failure:
    """A typed, reportable failure. ``message`` is safe to show (never contains the key)."""

    reason_code: str
    message: str
    retryable: bool = False
    http_status: int | None = None
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "reason_code": self.reason_code,
            "message": self.message,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "details": self.details,
        }


class ProviderError(Exception):
    """Internal transport-level error; converted to Failure by the provider."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        retryable: bool = False,
        http_status: int | None = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable
        self.http_status = http_status
        self.attempts = attempts

    def to_failure(self) -> Failure:
        return Failure(
            reason_code=self.reason_code,
            message=str(self),
            retryable=self.retryable,
            http_status=self.http_status,
        )
