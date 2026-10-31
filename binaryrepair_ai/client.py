"""Thin, testable DeepSeek chat client.

Why not the OpenAI SDK: the raw HTTP surface is small, and a hand-written client
makes timeouts, retry accounting, usage recording and failure mapping explicit.
The request format is the OpenAI-compatible one documented by DeepSeek.

Verified against the docs on 2026-10-18:
  POST {base_url}/chat/completions
  response_format={"type": "json_object"}  + the word "json" in the prompt
  thinking: {"type": "enabled"|"disabled"} (enabled by default server-side)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .errors import (
    REASON_AUTH,
    REASON_BAD_REQUEST,
    REASON_BALANCE,
    REASON_EMPTY_CONTENT,
    REASON_NETWORK,
    REASON_RATE_LIMIT,
    REASON_SERVER,
    REASON_TIMEOUT,
    ProviderError,
)
from .settings import Settings


@dataclass
class ChatResponse:
    content: str
    model: str
    usage: dict[str, Any] | None
    latency_ms: int
    http_status: int
    attempts: int
    raw: dict[str, Any] | None = None


class Transport(Protocol):
    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> httpx.Response: ...

    def get(self, url: str, *, headers: dict, timeout: float) -> httpx.Response: ...


class HttpxTransport:
    def __init__(self) -> None:
        self._client = httpx.Client(follow_redirects=False)

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> httpx.Response:
        return self._client.post(url, headers=headers, json=json, timeout=timeout)

    def get(self, url: str, *, headers: dict, timeout: float) -> httpx.Response:
        return self._client.get(url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()


class DeepSeekClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: Transport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self._transport = transport or HttpxTransport()
        self._sleep = sleep

    # -- URLs -----------------------------------------------------------------
    @property
    def chat_url(self) -> str:
        return f"{self.settings.base_url}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.settings.base_url}/models"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- models ---------------------------------------------------------------
    def list_models(self) -> list[str]:
        """Cheap account/connectivity check. Never returns the key."""
        resp = self._transport.get(self.models_url, headers=self._headers(), timeout=20.0)
        if resp.status_code == 401:
            raise ProviderError(REASON_AUTH, "authentication failed (401)", http_status=401)
        if resp.status_code != 200:
            raise ProviderError(
                REASON_SERVER,
                f"unexpected status {resp.status_code}",
                retryable=resp.status_code >= 500,
                http_status=resp.status_code,
            )
        data = resp.json()
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]

    # -- chat -----------------------------------------------------------------
    def chat_json(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        """One JSON-mode chat completion, with at most one transient retry."""
        max_tokens = max_tokens or self.settings.max_output_tokens
        thinking = self.settings.thinking if thinking is None else thinking

        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }
        if not thinking:
            # Temperature is ignored in thinking mode, so only send it when it applies.
            payload["temperature"] = temperature

        attempts = 0
        last: ProviderError | None = None
        started = time.monotonic()

        while attempts <= self.settings.max_retries:
            attempts += 1
            try:
                resp = self._transport.post(
                    self.chat_url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.settings.timeout_s,
                )
            except httpx.TimeoutException as exc:
                last = ProviderError(
                    REASON_TIMEOUT, f"request timed out: {exc}", retryable=True, attempts=attempts
                )
            except httpx.HTTPError as exc:
                last = ProviderError(
                    REASON_NETWORK, f"network error: {exc}", retryable=True, attempts=attempts
                )
            else:
                latency_ms = int((time.monotonic() - started) * 1000)
                if resp.status_code == 200:
                    try:
                        return self._parse_success(resp, latency_ms, attempts)
                    except ProviderError as exc:
                        exc.attempts = attempts
                        last = exc
                        if not exc.retryable:
                            raise
                else:
                    last = self._error_from_status(resp)
                    last.attempts = attempts
                    if not last.retryable:
                        raise last

            if attempts <= self.settings.max_retries:
                self._sleep(min(1.5 * (2 ** (attempts - 1)), 8.0))

        assert last is not None
        raise last

    # -- parsing --------------------------------------------------------------
    def _parse_success(
        self, resp: httpx.Response, latency_ms: int, attempts: int
    ) -> ChatResponse:
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(REASON_SERVER, f"response was not json: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(REASON_SERVER, "response contained no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""

        if not content.strip():
            # Documented JSON-mode caveat: content can occasionally be empty.
            raise ProviderError(REASON_EMPTY_CONTENT, "model returned empty content", retryable=True)

        return ChatResponse(
            content=content,
            model=data.get("model") or self.settings.model,
            usage=data.get("usage"),
            latency_ms=latency_ms,
            http_status=200,
            attempts=attempts,
            raw=data if self.settings.store_raw_responses else None,
        )

    def _error_from_status(self, resp: httpx.Response) -> ProviderError:
        body = ""
        try:
            body = resp.text[:300]
        except Exception:  # pragma: no cover - defensive
            pass
        detail = f"HTTP {resp.status_code}: {body}".strip()

        if resp.status_code == 401:
            return ProviderError(REASON_AUTH, detail, http_status=401)
        if resp.status_code == 402:
            return ProviderError(REASON_BALANCE, detail, http_status=402)
        if resp.status_code == 429:
            return ProviderError(REASON_RATE_LIMIT, detail, retryable=True, http_status=429)
        if resp.status_code in (400, 422):
            return ProviderError(REASON_BAD_REQUEST, detail, http_status=resp.status_code)
        if resp.status_code >= 500:
            return ProviderError(REASON_SERVER, detail, retryable=True, http_status=resp.status_code)
        return ProviderError(REASON_SERVER, detail, http_status=resp.status_code)
