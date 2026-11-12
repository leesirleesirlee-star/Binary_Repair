"""T8.2 LLM planner implementation (owner: M2).

M5 owns the stable seam (``app/services/assistant.py``) and persistence
(``assistant_plans``).  This module only fills the ``client`` branch and must
return an object that validates as :class:`app.domain.assistant.AssistantPlan`
— the wire shape the GUI consumes.

Rules kept from the M2 draft:

* a model/schema/transport failure never raises and never returns half a plan;
  it returns the deterministic guide marked ``mode="llm"``, ``fallback=True``
  and a stable ``fallback_reason``;
* ``plan_id`` is always present (the model may omit it, the schema requires it);
* ``model`` / ``tokens`` are filled from the real response, never from what the
  model claims; missing usage stays ``None`` (never ``0``);
* ``model_calls`` gets one row per attempt, including fallbacks; no prompt,
  raw content or key is written to the row or to logs.

Not in scope: persistence, the deterministic planner, the HTTP surface, and the
``POST /api/assistant/plan`` endpoint (M1).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from app.domain.assistant import AssistantPlan, deterministic_plan
from app.services import model_calls

__all__ = [
    "ASSISTANT_MODE_ENV",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER_BASE",
    "FallbackReason",
    "HttpJsonClient",
    "load_env_file",
    "ensure_env_loaded",
    "PROVIDER_BASE_ENV",
    "PROVIDER_KEY_ENV",
    "PROVIDER_TIMEOUT_ENV",
    "assistant_status",
    "build_messages",
    "client_from_env",
    "generate_plan",
    "resolve_mode",
    "resolve_model",
    "operator_username",
    "validate_plan",
]

ASSISTANT_MODE_ENV = "BINARYREPAIR_ASSISTANT_MODE"
ASSISTANT_MODEL_ENV = "BINARYREPAIR_ASSISTANT_MODEL"
PROVIDER_KEY_ENV = "BINARYREPAIR_ASSISTANT_PROVIDER_KEY"
PROVIDER_BASE_ENV = "BINARYREPAIR_ASSISTANT_PROVIDER_BASE_URL"
PROVIDER_TIMEOUT_ENV = "BINARYREPAIR_ASSISTANT_PROVIDER_TIMEOUT"
ASSISTANT_USERNAME_ENV = "BINARYREPAIR_ASSISTANT_USERNAME"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_PROVIDER_BASE = "https://api.deepseek.com/v1"
DEFAULT_TIMEOUT = 30.0
MAX_OUTPUT_TOKENS = 1200

MODES = ("llm", "deterministic", "fixture")

#: ``model_calls.status`` values.  A fallback is recorded as a fallback; it is
#: never stored as an accepted plan.
STATUS_OK = "accepted_for_assistant"
STATUS_FALLBACK = "fallback"


class FallbackReason:
    """Stable reason codes; the GUI translates them, it never infers them."""

    NO_CLIENT = "llm_client_not_configured"
    DISABLED = "assistant_mode_not_llm"
    TIMEOUT = "llm_timeout"
    NETWORK = "llm_network_error"
    EMPTY = "llm_empty_content"
    MALFORMED = "llm_invalid_json"
    SCHEMA = "llm_plan_schema_invalid"
    HTTP = "llm_http_error"
    UNKNOWN = "llm_unknown_error"


class _TransportMissing(TypeError):
    """The injected client exposes neither ``chat_json`` nor ``complete``."""


@dataclass(frozen=True)
class _Response:
    content: Any
    model: str | None
    usage: dict[str, Any]
    request_id: str | None


def resolve_mode(value: str | None = None) -> str:
    """Read the assistant mode; unknown values are deterministic, never llm."""
    raw = (value if value is not None else os.environ.get(ASSISTANT_MODE_ENV, "")).strip().lower()
    return raw if raw in MODES else "deterministic"


def resolve_model(value: str | None = None) -> str:
    return (value or os.environ.get(ASSISTANT_MODEL_ENV, "")).strip() or DEFAULT_MODEL


def assistant_status(*, mode: str | None = None, client_configured: bool | None = None) -> dict[str, Any]:
    """Mode badge for the workbench.

    ``available`` is true only in llm mode with an injected client.  Without an
    explicit signal the page says "not wired", never "enabled".
    """
    resolved = resolve_mode(mode)
    if client_configured is None:
        # Read the environment here so the badge answers "is a provider key
        # present?" without the caller guessing.
        client_configured = client_from_env() is not None
    return {
        "mode": resolved,
        "model": resolve_model() if resolved == "llm" else None,
        "available": resolved == "llm" and bool(client_configured),
        "fallback_reason": None if resolved != "llm" or client_configured else FallbackReason.NO_CLIENT,
    }


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _json_safe(value: Any) -> bool:
    """True when ``value`` can be serialised without inventing a repr."""
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return True


def build_messages(description: str, *, context: Mapping[str, Any] | None = None, username: str = "") -> list[dict[str, str]]:
    """Build the planning messages.

    The prompt contains only the user description and structured context.  It
    asks for one JSON object; the caller validates it, so a half-written answer
    falls back instead of being rendered.
    """
    system = (
        "You are the planning assistant of a binary-repair workbench.\n"
        "Return ONLY a JSON object matching this shape:\n"
        '{"plan_id":"string","mode":"llm","fallback":false,"fallback_reason":null,'
        '"model":null,"tokens":null,"steps":[{"id":"string","kind":"upload|question|review",'
        '"title":"string","prompt":"string","questions":[{"key":"string","label":"string",'
        '"input":"text|textarea","required":true,"ascii":true,"default":"string"}]}]}\n'
        "Rules:\n"
        "- Every step needs a non-empty id, title and prompt; ids are unique.\n"
        "- kind is upload, question or review; a question step needs >= 1 question.\n"
        "- questions are only allowed on question steps; keys are unique per step.\n"
        "- The last step must be kind=review.\n"
        "- 1 to 6 steps, in the order the user should follow.\n"
        "- Do not add fields outside this shape.\n"
        "- You guide the user; you never create jobs, start runs or judge repairs.\n"
        "- Never invent hashes, verdicts, evidence or token counts; model and tokens are filled by the caller.\n"
        "- Use ASCII English for any value that will be submitted to the backend.\n"
        "- No markdown fences, no commentary."
    )
    payload: dict[str, Any] = {"description": description}
    if context:
        # data_dir and job_id are routing/audit hints, not prompt material.
        # Anything the JSON encoder cannot represent is dropped instead of
        # being sent as a Python repr.
        safe = {
            key: value
            for key, value in dict(context).items()
            if key not in ("data_dir", "job_id") and _json_safe(value)
        }
        if safe:
            payload["context"] = safe
    # The operator username is request context for the model, never an
    # authorization signal and never the provider key.
    operator_username = (username or "").strip()
    if operator_username:
        payload["operator_username"] = operator_username
    user = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _errors_of(payload: Any) -> list[str]:
    """Schema errors without echoing model output.

    ``include_input=False`` matters: pydantic's default errors carry the offending
    value, which would put model text (and possibly user data) into a response.
    """
    if isinstance(payload, AssistantPlan):
        return []
    try:
        AssistantPlan.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - ValidationError and its subclasses
        errors = getattr(exc, "errors", None)
        if callable(errors):
            try:
                details = errors(include_input=False)
            except TypeError:  # pragma: no cover - older pydantic
                details = []
            result: list[str] = []
            for item in details[:20]:
                loc = ".".join(str(part) for part in item.get("loc", ())) or "plan"
                result.append(f"{loc}: {item.get('msg', 'invalid')}")
            return result or ["plan does not match AssistantPlan v1"]
        return ["plan does not match AssistantPlan v1"]
    return []


def validate_plan(plan: Any) -> list[str]:
    """Validate against AssistantPlan v1; ``[]`` means the GUI can render it."""
    return _errors_of(plan)


def _field(response: Any, name: str) -> Any:
    if isinstance(response, Mapping):
        return response.get(name)
    return getattr(response, name, None)


def _usage_of(response: Any) -> dict[str, Any]:
    usage = _field(response, "usage")
    if not isinstance(usage, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "latency_ms"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    return result


def _tokens_of(usage: Mapping[str, Any]) -> dict[str, int] | None:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    if prompt < 0 or completion < 0:
        return None
    return {"prompt": prompt, "completion": completion}


def _invoke(client: Any, messages: Sequence[Mapping[str, str]], model: str) -> Any:
    chat_json = getattr(client, "chat_json", None)
    if callable(chat_json):
        # The provider adapter used by M5: returns content/model/usage.
        return chat_json(messages, max_tokens=MAX_OUTPUT_TOKENS, thinking=False)
    complete = getattr(client, "complete", None)
    if callable(complete):
        return complete(messages, model=model)
    raise _TransportMissing("client exposes neither chat_json nor complete")


def _response_of(raw: Any) -> _Response:
    content = _field(raw, "content")
    model = _field(raw, "model")
    request_id = _field(raw, "request_id")
    return _Response(
        content=content,
        model=model if isinstance(model, str) and model else None,
        usage=_usage_of(raw),
        request_id=request_id if isinstance(request_id, str) and request_id else None,
    )


def _parse_content(content: Any) -> tuple[Any | None, str | None]:
    if isinstance(content, Mapping):
        return dict(content), None
    if not isinstance(content, str) or not content.strip():
        return None, FallbackReason.EMPTY
    text = content.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, FallbackReason.MALFORMED
    if not isinstance(parsed, dict):
        return None, FallbackReason.SCHEMA
    return parsed, None


def _fallback(
    description: str,
    context: Mapping[str, Any] | None,
    *,
    reason: str,
    requested: str,
    returned: str | None = None,
    usage: Mapping[str, Any] | None = None,
) -> AssistantPlan:
    base = deterministic_plan(description, dict(context) if context else None)
    return base.model_copy(update={
        "mode": "llm",
        "fallback": True,
        "fallback_reason": reason,
        "model": returned,
        "tokens": _tokens_of(usage or {}),
    })


def _record(
    plan: AssistantPlan,
    *,
    context: Mapping[str, Any] | None,
    requested: str,
    returned: str | None,
    usage: Mapping[str, Any],
    request_id: str | None,
    status: str,
) -> None:
    data_dir = context.get("data_dir") if isinstance(context, Mapping) else None
    job_id = context.get("job_id") if isinstance(context, Mapping) else None
    if not data_dir:
        # A job-linked call must not fall back to a project-root "var": the row
        # would be unreadable where the job lives. A job-less preview has
        # nothing to persist, so it simply writes nothing.
        if job_id:
            raise ValueError("data_dir is required to record model usage")
        return
    usage = dict(usage) if usage else {}
    latency = usage.pop("latency_ms", None)
    if not isinstance(latency, int) or isinstance(latency, bool) or latency < 0:
        latency = None
    try:
        model_calls.record_call(
            data_dir=data_dir,
            model_requested=requested,
            model_returned=returned,
            status=status,
            route="assistant",
            usage=usage or None,
            latency_ms=latency,
            request_id=request_id,
            job_id=job_id,
            plan_id=plan.plan_id,
        )
    except Exception:  # noqa: BLE001 - audit failure must not break planning
        # A missing audit row is reported by ai_usage being unavailable; it is
        # never replaced by a fabricated zero.
        pass


@dataclass(frozen=True)
class _ProviderResult:
    content: Any
    model: str | None
    usage: dict[str, Any]
    request_id: str | None


class _ProviderHTTPError(RuntimeError):
    """A provider answered with a non-2xx status; the body is not kept."""

    def __init__(self, status: int) -> None:
        super().__init__(f"provider returned HTTP {status}")
        self.status = status


class HttpJsonClient:
    """Minimal OpenAI-compatible chat client.

    Only used when ``BINARYREPAIR_ASSISTANT_PROVIDER_KEY`` is present.  The key
    never leaves this object: ``repr`` hides it, errors carry only the status,
    and no prompt or raw answer is logged.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_PROVIDER_BASE,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Any | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"HttpJsonClient(base_url={self._base_url!r}, model={self._model!r})"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    def chat_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = MAX_OUTPUT_TOKENS,
        thinking: bool = False,
    ) -> _ProviderResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [dict(message) for message in messages],
            "max_tokens": int(max_tokens),
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError("provider timed out") from exc
        except httpx.TransportError as exc:
            raise ConnectionError("provider unreachable") from exc
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        if response.status_code >= 400:
            raise _ProviderHTTPError(response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise _ProviderHTTPError(response.status_code) from exc
        choices = data.get("choices") if isinstance(data, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        usage_fields = {
            key: usage[key]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool)
        }
        # Measured here, never asked from the model.
        usage_fields["latency_ms"] = latency_ms
        return _ProviderResult(
            content=message.get("content") if isinstance(message, dict) else None,
            model=(
                data.get("model")
                if isinstance(data, dict) and isinstance(data.get("model"), str)
                else self._model
            ),
            usage=usage_fields,
            request_id=response.headers.get("x-request-id"),
        )


#: Documented aliases.  ``.env.example`` shipped these names for a while and
#: the code never read them, so a filled-in ``.env`` silently produced the
#: deterministic fallback.  Keep them accepted, but let the ``BINARYREPAIR_*``
#: names win when both are set.
COMPAT_KEY_ENVS = ("DEEPSEEK_API_KEY",)
COMPAT_BASE_ENVS = ("DEEPSEEK_BASE_URL",)
COMPAT_MODEL_ENVS = ("DEEPSEEK_MODEL",)
COMPAT_TIMEOUT_ENVS = ("DEEPSEEK_TIMEOUT_S",)

_ENV_FILE_LOADED = False


def _first_env(env: Mapping[str, str], names: Sequence[str]) -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def load_env_file(
    path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Load ``.env`` into the environment without overwriting anything set.

    Deliberately dependency-free: ``KEY=VALUE`` lines, ``export`` prefixes,
    ``#`` comments, blank lines, and matching single/double quotes.  A missing
    file is normal (the app then simply has no provider key) and returns ``{}``.
    """

    target = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[2] / ".env"
    )
    env = os.environ if environ is None else environ
    loaded: dict[str, str] = {}
    try:
        text = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, OSError):
        return loaded
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not (name[0].isalpha() or name[0] == "_"):
            continue
        if not all(char.isalnum() or char == "_" for char in name):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if (env.get(name) or "").strip():
            continue
        env[name] = value  # type: ignore[index]
        loaded[name] = value
    return loaded


def ensure_env_loaded(path: str | Path | None = None) -> dict[str, str]:
    """Load the repository ``.env`` once per process; later calls are no-ops."""

    global _ENV_FILE_LOADED
    if _ENV_FILE_LOADED:
        return {}
    _ENV_FILE_LOADED = True
    return load_env_file(path)


def client_from_env(environ: Mapping[str, str] | None = None) -> HttpJsonClient | None:
    """Build a provider client from the environment, or ``None``.

    ``None`` is a real answer: the caller must record a fallback, never a
    fabricated plan and never a zero token count.
    """
    if environ is None:
        ensure_env_loaded()
    env = os.environ if environ is None else environ
    key = _first_env(env, (PROVIDER_KEY_ENV, *COMPAT_KEY_ENVS))
    if not key and environ is None:
        # A key saved from the local operator UI is the next fallback; the
        # environment still wins when both exist.
        try:
            from app.services.assistant_key_store import load_key

            key = (load_key() or "").strip()
        except Exception:
            key = ""
    if not key:
        return None
    base = _first_env(
        env, (PROVIDER_BASE_ENV, *COMPAT_BASE_ENVS)
    ).rstrip("/") or DEFAULT_PROVIDER_BASE
    model = _first_env(env, (ASSISTANT_MODEL_ENV, *COMPAT_MODEL_ENVS)) or DEFAULT_MODEL
    raw_timeout = _first_env(env, (PROVIDER_TIMEOUT_ENV, *COMPAT_TIMEOUT_ENVS))
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT
    return HttpJsonClient(api_key=key, base_url=base, model=model, timeout=timeout)


def test_provider_connection(client: Any | None = None) -> dict[str, Any]:
    """Make one tiny provider call to prove the configured key works.

    This is deliberately a real call (the operator asked to test the key); it
    uses a small token limit and returns only non-secret diagnostics.  It never
    raises and never includes the key or provider body in the response.
    """

    if client is None:
        client = client_from_env()
    if client is None:
        return {"ok": False, "error": "llm_client_not_configured"}
    try:
        result = client.chat_json(
            [
                {"role": "system", "content": "Reply with JSON only."},
                {"role": "user", "content": "Return {\"ok\": true}."},
            ],
            max_tokens=8,
            thinking=False,
        )
    except TimeoutError:
        return {"ok": False, "error": "llm_timeout"}
    except ConnectionError:
        return {"ok": False, "error": "llm_network_error"}
    except Exception as exc:  # provider HTTP/transport/schema
        if type(exc).__name__ == "_ProviderHTTPError":
            return {"ok": False, "error": "llm_http_error"}
        return {"ok": False, "error": "llm_unknown_error"}
    content = getattr(result, "content", None)
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "error": "llm_empty_content"}
    return {
        "ok": True,
        "model": getattr(result, "model", None),
        "request_id": getattr(result, "request_id", None),
        "usage": getattr(result, "usage", None),
    }


def operator_username() -> str:
    env_user = (os.environ.get(ASSISTANT_USERNAME_ENV) or "").strip()
    if env_user:
        return env_user
    try:
        from app.services.assistant_key_store import load_username
        return (load_username() or "").strip()
    except Exception:
        return ""


def generate_plan(
    description: str,
    *,
    context: Mapping[str, Any] | None = None,
    client: Any | None = None,
    mode: str | None = "llm",
) -> AssistantPlan:
    """Return a plan for the ``client`` branch of the M5 seam.

    Called as ``assistant_llm.generate_plan(description, context=..., client=...,
    mode="llm")`` from ``app/services/assistant.py``.  Never raises on model
    failure; the caller persists whatever comes back.
    """
    resolved = resolve_mode(mode)
    requested = resolve_model()

    if resolved != "llm":
        # Defensive: the seam only routes here in llm mode.
        return deterministic_plan(description, dict(context) if context else None)
    if client is None:
        client = client_from_env()
    if client is None:
        plan = _fallback(description, context, reason=FallbackReason.NO_CLIENT,
                         requested=requested)
        _record(plan, context=context, requested=requested, returned=None,
                usage={}, request_id=None, status=STATUS_FALLBACK)
        return plan

    try:
        messages = build_messages(description, context=context,
                                  username=operator_username())
        raw = _invoke(client, messages, requested)
    except TimeoutError:
        plan = _fallback(description, context, reason=FallbackReason.TIMEOUT,
                         requested=requested)
        _record(plan, context=context, requested=requested, returned=None,
                usage={}, request_id=None, status=STATUS_FALLBACK)
        return plan
    except ConnectionError:
        plan = _fallback(description, context, reason=FallbackReason.NETWORK,
                         requested=requested)
        _record(plan, context=context, requested=requested, returned=None,
                usage={}, request_id=None, status=STATUS_FALLBACK)
        return plan
    except _TransportMissing:
        plan = _fallback(description, context, reason=FallbackReason.NO_CLIENT,
                         requested=requested)
        _record(plan, context=context, requested=requested, returned=None,
                usage={}, request_id=None, status=STATUS_FALLBACK)
        return plan
    except _ProviderHTTPError:
        plan = _fallback(description, context, reason=FallbackReason.HTTP,
                         requested=requested)
        _record(plan, context=context, requested=requested, returned=None,
                usage={}, request_id=None, status=STATUS_FALLBACK)
        return plan
    except Exception:  # noqa: BLE001 - transport failures downgrade, never leak
        plan = _fallback(description, context, reason=FallbackReason.UNKNOWN,
                         requested=requested)
        _record(plan, context=context, requested=requested, returned=None,
                usage={}, request_id=None, status=STATUS_FALLBACK)
        return plan

    response = _response_of(raw)
    payload, parse_reason = _parse_content(response.content)
    if payload is None:
        plan = _fallback(description, context, reason=parse_reason or FallbackReason.EMPTY,
                         requested=requested, returned=response.model,
                         usage=response.usage)
        _record(plan, context=context, requested=requested, returned=response.model,
                usage=response.usage, request_id=response.request_id,
                status=STATUS_FALLBACK)
        return plan

    payload = dict(payload)
    # Identity and accounting are server-side facts; do not trust the model's.
    plan_id = payload.get("plan_id")
    payload["plan_id"] = (
        plan_id.strip() if isinstance(plan_id, str) and plan_id.strip() else uuid.uuid4().hex
    )
    payload["mode"] = "llm"
    payload["fallback"] = False
    payload["fallback_reason"] = None
    payload["model"] = response.model
    payload["tokens"] = _tokens_of(response.usage)

    errors = _errors_of(payload)
    if errors:
        plan = _fallback(description, context, reason=FallbackReason.SCHEMA,
                         requested=requested, returned=response.model,
                         usage=response.usage)
        _record(plan, context=context, requested=requested, returned=response.model,
                usage=response.usage, request_id=response.request_id,
                status=STATUS_FALLBACK)
        return plan

    plan = AssistantPlan.model_validate(payload)
    _record(plan, context=context, requested=requested, returned=response.model,
            usage=response.usage, request_id=response.request_id, status=STATUS_OK)
    return plan
