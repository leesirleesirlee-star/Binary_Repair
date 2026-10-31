"""DeepSeek price arithmetic.

Prices are per 1M tokens and were copied from the official table on 2026-10-18:
https://api-docs.deepseek.com/quick_start/pricing/

Peak hours are 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday (Chinese public
holidays excluded). Off-peak is half price. Prices can change: re-check the page
before relying on a budget, and treat every number here as an estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ModelPrice:
    # USD per 1M tokens
    input_cache_hit_offpeak: float
    input_cache_hit_peak: float
    input_cache_miss_offpeak: float
    input_cache_miss_peak: float
    output_offpeak: float
    output_peak: float


PRICES: dict[str, ModelPrice] = {
    "deepseek-flash": ModelPrice(0.003, 0.006, 0.15, 0.30, 0.60, 1.20),
    "deepseek-v4-pro": ModelPrice(0.022, 0.044, 0.66, 1.32, 1.98, 3.96),
}

# Used only to bound spending when an unknown model name is configured.
FALLBACK_PRICE = PRICES["deepseek-v4-pro"]


def is_peak(at: datetime | None = None) -> bool:
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    at = at.astimezone(timezone.utc)
    if at.weekday() >= 5:  # Saturday/Sunday
        return False
    return 1 <= at.hour < 4 or 6 <= at.hour < 10


def price_for(model: str) -> ModelPrice:
    return PRICES.get(model, FALLBACK_PRICE)


def estimate_cost_usd(
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int | None = None,
    at: datetime | None = None,
) -> float:
    """Return an estimated USD cost from observed (or estimated) usage."""
    p = price_for(model)
    peak = is_peak(at)
    hit_price = p.input_cache_hit_peak if peak else p.input_cache_hit_offpeak
    miss_price = p.input_cache_miss_peak if peak else p.input_cache_miss_offpeak
    out_price = p.output_peak if peak else p.output_offpeak

    if cache_miss_tokens is None:
        cache_miss_tokens = max(prompt_tokens - cache_hit_tokens, 0)

    return (
        cache_hit_tokens * hit_price
        + cache_miss_tokens * miss_price
        + completion_tokens * out_price
    ) / 1_000_000


def worst_case_cost_usd(
    model: str,
    *,
    prompt_chars: int,
    max_output_tokens: int,
    at: datetime | None = None,
) -> float:
    """Conservative pre-flight bound used to reserve budget *before* sending.

    3 characters/token is deliberately pessimistic for English + code, and the
    input is priced as a cache miss. Overestimating is the safe direction.
    """
    prompt_tokens = max(1, prompt_chars // 3)
    return estimate_cost_usd(
        model,
        prompt_tokens=prompt_tokens,
        completion_tokens=max_output_tokens,
        cache_hit_tokens=0,
        at=at,
    )
