"""Provider adapter: the only door between the job controller and DeepSeek.

Contract used by the rest of the app:

    result = provider.request_candidate(ctx)
    result.ok        -> result.candidate is a validated CandidateV1
    not result.ok    -> result.failure is a typed Failure (unsupported/rejected/error)

The provider NEVER raises for expected conditions, never fabricates a candidate,
and never reports success from the model's own words.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .client import ChatResponse, DeepSeekClient
from .errors import (
    REASON_BUDGET,
    REASON_CONTEXT_TOO_LARGE,
    REASON_EMPTY_CONTENT,
    REASON_IDENTITY,
    REASON_INVALID_JSON,
    REASON_SCHEMA,
    Failure,
    ProviderError,
)
from .pricing import estimate_cost_usd, worst_case_cost_usd
from .prompts import build_messages, messages_to_text
from .schemas import CandidateRejected, CandidateV1, RepairContext, check_candidate
from .settings import Settings

MAX_PROMPT_CHARS = 60_000  # ~20k tokens; well under the documented 1M context
MAX_RESPONSE_CHARS = 200_000


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class CallRecord:
    request_id: str
    model: str
    model_returned: str | None
    prompt_sha256: str
    response_sha256: str | None
    status: str
    latency_ms: int
    usage: dict[str, Any] | None
    estimated_cost_usd: float | None
    attempts: int
    thinking: bool
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProviderResult:
    candidate: CandidateV1 | None
    failure: Failure | None
    record: CallRecord

    @property
    def ok(self) -> bool:
        return self.candidate is not None


def _extract_json_object(content: str) -> tuple[dict, bool]:
    """Parse the model's content. Returns (object, needed_brace_recovery)."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        obj = json.loads(text[start : end + 1])
        if not isinstance(obj, dict):
            raise json.JSONDecodeError("top-level json is not an object", text, start)
        return obj, True
    if not isinstance(obj, dict):
        raise json.JSONDecodeError("top-level json is not an object", text, 0)
    return obj, False


class DeepSeekProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client: DeepSeekClient | None = None,
        log_path: str | Path | None = None,
        budget_usd: float | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or DeepSeekClient(settings)
        raw_log = log_path if log_path is not None else os.environ.get("BINARYREPAIR_MODEL_LOG")
        self.log_path = Path(raw_log) if raw_log else None
        self.budget_usd = settings.budget_usd if budget_usd is None else budget_usd
        self.spent_usd = 0.0

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> "DeepSeekProvider":
        return cls(Settings.from_env(env_file))

    # -- public API -----------------------------------------------------------
    def request_candidate(
        self,
        ctx: RepairContext,
        *,
        max_tokens: int | None = None,
        thinking: bool | None = None,
    ) -> ProviderResult:
        request_id = uuid.uuid4().hex
        messages = build_messages(ctx)
        prompt_text = messages_to_text(messages)
        prompt_sha = sha256_text(prompt_text)
        max_tokens = max_tokens or self.settings.max_output_tokens
        thinking = self.settings.thinking if thinking is None else thinking

        def record(
            *,
            status: str,
            model_returned: str | None = None,
            response_sha: str | None = None,
            latency_ms: int = 0,
            usage: dict | None = None,
            cost: float | None = None,
            attempts: int = 0,
        ) -> CallRecord:
            return CallRecord(
                request_id=request_id,
                model=self.settings.model,
                model_returned=model_returned,
                prompt_sha256=prompt_sha,
                response_sha256=response_sha,
                status=status,
                latency_ms=latency_ms,
                usage=usage,
                estimated_cost_usd=cost,
                attempts=attempts,
                thinking=thinking,
            )

        # 0. Request-size guard: refuse to silently truncate reviewed context.
        if len(prompt_text) > MAX_PROMPT_CHARS:
            return self._fail(
                record(status="not_sent"),
                REASON_CONTEXT_TOO_LARGE,
                f"prompt is {len(prompt_text)} chars, limit {MAX_PROMPT_CHARS}",
            )

        # 1. Reserve a conservative worst case before spending anything.
        worst = worst_case_cost_usd(
            self.settings.model,
            prompt_chars=len(prompt_text),
            max_output_tokens=max_tokens,
        )
        if self.spent_usd + worst > self.budget_usd:
            return self._fail(
                record(status="budget_blocked"),
                REASON_BUDGET,
                f"estimated worst case ${worst:.4f} would exceed remaining "
                f"${max(self.budget_usd - self.spent_usd, 0):.4f}",
                details={"worst_case_usd": round(worst, 6), "spent_usd": round(self.spent_usd, 6)},
            )

        # 2. Call.
        try:
            resp: ChatResponse = self.client.chat_json(
                messages, max_tokens=max_tokens, thinking=thinking
            )
        except ProviderError as exc:
            return self._fail(
                record(status=exc.reason_code, attempts=exc.attempts),
                exc.reason_code,
                str(exc),
                retryable=exc.retryable,
                http_status=exc.http_status,
            )

        cost = self._cost(resp.usage)
        if cost is not None:
            self.spent_usd += cost
        base_record = record(
            status="received",
            model_returned=resp.model,
            response_sha=sha256_text(resp.content),
            latency_ms=resp.latency_ms,
            usage=resp.usage,
            cost=cost,
            attempts=resp.attempts,
        )

        # 3. Content guards.
        if not resp.content.strip():
            return self._fail(base_record, REASON_EMPTY_CONTENT, "empty content")
        if len(resp.content) > MAX_RESPONSE_CHARS:
            return self._fail(base_record, REASON_SCHEMA, "response too large")

        # 4. JSON parse.
        try:
            obj, recovered = _extract_json_object(resp.content)
        except json.JSONDecodeError as exc:
            return self._fail(
                base_record,
                REASON_INVALID_JSON,
                f"response is not a json object: {exc.msg} at char {exc.pos}",
                details={"content_sha256": base_record.response_sha256},
            )

        # 5. Schema + identity/scope validation.
        try:
            cand = CandidateV1.model_validate(obj)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            loc = ".".join(str(x) for x in first.get("loc", ()))
            return self._fail(
                base_record,
                REASON_SCHEMA,
                f"schema validation failed at {loc}: {first.get('msg', 'invalid')}",
                details={"recovered_from_prose": recovered},
            )

        try:
            check_candidate(cand, ctx)
        except CandidateRejected as exc:
            code = REASON_IDENTITY if exc.reason_code == "identity_mismatch" else exc.reason_code
            return self._fail(base_record, code, str(exc))

        # 6. Success: record and return. No claim of correctness is made here.
        base_record.status = "accepted_for_validation"
        self._append_log(base_record)
        return ProviderResult(candidate=cand, failure=None, record=base_record)

    # -- helpers --------------------------------------------------------------
    def _cost(self, usage: dict | None) -> float | None:
        if not usage:
            return None
        try:
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            hit = int(usage.get("prompt_cache_hit_tokens") or 0)
            miss = usage.get("prompt_cache_miss_tokens")
            return round(
                estimate_cost_usd(
                    self.settings.model,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    cache_hit_tokens=hit,
                    cache_miss_tokens=int(miss) if miss is not None else None,
                ),
                8,
            )
        except (TypeError, ValueError):
            return None

    def _fail(
        self,
        record: CallRecord,
        reason_code: str,
        message: str,
        *,
        retryable: bool = False,
        http_status: int | None = None,
        details: dict | None = None,
    ) -> ProviderResult:
        record.status = reason_code
        self._append_log(record)
        return ProviderResult(
            candidate=None,
            failure=Failure(
                reason_code=reason_code,
                message=message,
                retryable=retryable,
                http_status=http_status,
                details=details or {},
            ),
            record=record,
        )

    def _append_log(self, record: CallRecord) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")
