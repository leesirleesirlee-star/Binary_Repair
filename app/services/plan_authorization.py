"""Transfer authorization for hosted-provider planning.

The assistant planner may run on an external model only when the job's original
binary is a committed public fixture. Everything else fails closed: client
binaries and confidential derived content must never reach a hosted provider
without an explicit, recorded authorization.

The allowlist is derived at runtime from ``fixtures/public/*/original.elf`` so a
new public fixture is authorized the moment it is committed, and an absent
fixture directory yields an empty allowlist (deny all).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_HEX = frozenset("0123456789abcdef")


def _fixture_root() -> Path:
    return _ROOT / "fixtures" / "public"


@lru_cache(maxsize=1)
def public_fixture_hashes() -> frozenset[str]:
    """SHA-256 of every committed public-fixture ``original.elf``.

    Cached per process. The value changes only when the checkout changes, which
    is acceptable for an authorization allowlist that must be stable within one
    run and must fail closed when the fixture directory is absent.
    """
    root = _fixture_root()
    if not root.is_dir():
        return frozenset()
    hashes: set[str] = set()
    for elf in root.glob("*/original.elf"):
        try:
            hashes.add(hashlib.sha256(elf.read_bytes()).hexdigest())
        except OSError:
            continue
    return frozenset(hashes)


def may_transfer_plan(original_sha256: str | None) -> bool:
    """True only when ``original_sha256`` names a committed public fixture.

    A missing, malformed, or unknown hash is denied, so a plan route fails
    closed for anything that is not demonstrably public.
    """
    if not isinstance(original_sha256, str):
        return False
    value = original_sha256.strip().lower()
    if len(value) != 64 or any(char not in _HEX for char in value):
        return False
    return value in public_fixture_hashes()


__all__ = ["may_transfer_plan", "public_fixture_hashes"]
