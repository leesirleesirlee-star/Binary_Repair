"""Configuration loaded from the environment (and an optional local .env file).

Only the trusted provider process should import this. The key never leaves this
process: it is not written to logs, reports, the browser, or the execution VM.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> None:
    """Minimal .env loader (no dependency). Existing environment values win."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    thinking: bool = False
    timeout_s: float = 120.0
    max_retries: int = 1
    max_output_tokens: int = 4000
    budget_usd: float = 2.0
    store_raw_responses: bool = False

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> "Settings":
        if env_file:
            load_env_file(env_file)

        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and fill it in, "
                "or export the variable in this shell only."
            )

        thinking = os.environ.get("DEEPSEEK_THINKING", "disabled").lower() in {
            "1",
            "true",
            "yes",
            "enabled",
            "on",
        }
        return cls(
            api_key=key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip(),
            thinking=thinking,
            timeout_s=float(os.environ.get("DEEPSEEK_TIMEOUT_S", "120")),
            max_retries=int(os.environ.get("DEEPSEEK_MAX_RETRIES", "1")),
            max_output_tokens=int(os.environ.get("DEEPSEEK_MAX_OUTPUT_TOKENS", "4000")),
            budget_usd=float(os.environ.get("BINARYREPAIR_AI_BUDGET_USD", "2.0")),
            store_raw_responses=os.environ.get("BINARYREPAIR_STORE_RAW", "0") == "1",
        )

    def redacted(self) -> dict:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "thinking": self.thinking,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
            "max_output_tokens": self.max_output_tokens,
            "budget_usd": self.budget_usd,
        }
