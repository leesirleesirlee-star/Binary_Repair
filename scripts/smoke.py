#!/usr/bin/env python3
"""One harmless DeepSeek API request with public text.

Purpose (today's assignment): prove the account, endpoint, model name, JSON mode,
usage reporting and cost arithmetic work *before* any binary content is involved.

Run:
    .venv/bin/python scripts/smoke.py            # real request
    .venv/bin/python scripts/smoke.py --mock     # offline plumbing check, no key
    .venv/bin/python scripts/smoke.py --models   # account + model list only

The text below is synthetic, written for this test. No client or binary data is sent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from binaryrepair_ai.client import DeepSeekClient  # noqa: E402
from binaryrepair_ai.pricing import estimate_cost_usd, is_peak  # noqa: E402
from binaryrepair_ai.settings import Settings  # noqa: E402

PUBLIC_TEXT = (
    "A binary patch changes a small number of bytes inside an executable. "
    "Correctness verification asks two separate questions: does the patched program "
    "still behave as before on valid inputs, and is the targeted unsafe behavior now "
    "blocked? A test that merely fails to crash does not answer either question. "
    "This paragraph is synthetic and contains no client data."
)

SYSTEM = (
    "You summarize public text. Reply with exactly one json object and no prose. "
    "The object has keys: summary (one short sentence), word_count (integer)."
)

USER_TEMPLATE = (
    "Summarize the text below in one sentence and count its words.\n"
    "Return json only.\n\nTEXT:\n{text}\n"
)


class MockTransport:
    """Offline transport so the plumbing can be tested without an API key."""

    def post(self, url, *, headers, json, timeout):
        import httpx

        return httpx.Response(
            200,
            json={
                "model": json["model"],
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"summary": "A patch must be checked for '
                            'preserved behavior and blocked exploitation.", '
                            '"word_count": 36}',
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 210,
                    "completion_tokens": 28,
                    "total_tokens": 238,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 210,
                },
            },
        )

    def get(self, url, *, headers, timeout):
        import httpx

        return httpx.Response(200, json={"data": [{"id": "mock-model"}]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="no network, no key")
    ap.add_argument("--models", action="store_true", help="only check account/model list")
    ap.add_argument("--thinking", action="store_true", help="enable thinking mode")
    ap.add_argument("--max-tokens", type=int, default=300)
    args = ap.parse_args()

    if args.mock:
        settings = Settings(api_key="mock-key", model="deepseek-flash")
        transport = MockTransport()
    else:
        settings = Settings.from_env()
        transport = None

    print("== configuration ==")
    for k, v in settings.redacted().items():
        print(f"  {k}: {v}")
    print(f"  api_key: {'set (' + str(len(settings.api_key)) + ' chars)' if settings.api_key else 'missing'}")
    print(f"  peak window now: {is_peak()}")

    client = DeepSeekClient(settings, transport=transport)

    if not args.mock:
        print("\n== account check (GET /models) ==")
        try:
            models = client.list_models()
            print("  reachable; models: " + ", ".join(models))
        except Exception as exc:  # noqa: BLE001 - the point is to report it plainly
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            return 2
        if args.models:
            return 0

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER_TEMPLATE.format(text=PUBLIC_TEXT)},
    ]

    print("\n== one json request ==")
    started = time.monotonic()
    try:
        resp = client.chat_json(
            messages, max_tokens=args.max_tokens, thinking=args.thinking
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return 3
    elapsed = time.monotonic() - started

    print(f"  http_status: {resp.http_status}")
    print(f"  model_returned: {resp.model}")
    print(f"  latency: {resp.latency_ms} ms (wall {elapsed:.2f} s)")
    print(f"  attempts: {resp.attempts}")
    print(f"  thinking: {args.thinking}")

    usage = resp.usage or {}
    print("\n== usage ==")
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    ):
        if key in usage:
            print(f"  {key}: {usage[key]}")

    cost = None
    if usage:
        cost = estimate_cost_usd(
            resp.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens") or 0),
            cache_miss_tokens=(
                int(usage["prompt_cache_miss_tokens"])
                if usage.get("prompt_cache_miss_tokens") is not None
                else None
            ),
        )
        print(f"\n  estimated_cost_usd: ${cost:.8f}")
        print("  (estimate from the published price table, not an invoice)")

    print("\n== content ==")
    print(resp.content[:1000])
    try:
        obj = json.loads(resp.content)
        print("\n  json_parse: OK")
        print(f"  keys: {sorted(obj)}")
    except json.JSONDecodeError as exc:
        print(f"\n  json_parse: FAILED ({exc})")
        return 4

    print("\nOK: endpoint, model, json mode and usage reporting are working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
