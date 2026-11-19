"""Transfer-authorization gate for hosted-provider planning."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services.plan_authorization import may_transfer_plan, public_fixture_hashes

ROOT = Path(__file__).resolve().parents[1]
CWE121 = ROOT / "fixtures" / "public" / "CWE121_src_char_declare_cpy_51b" / "original.elf"


def test_public_fixture_is_in_the_allowlist() -> None:
    if not CWE121.is_file():
        pytest.skip("public fixture original.elf not present")
    digest = hashlib.sha256(CWE121.read_bytes()).hexdigest()
    assert digest in public_fixture_hashes()


def test_may_transfer_plan_accepts_only_a_committed_fixture() -> None:
    if not CWE121.is_file():
        pytest.skip("public fixture original.elf not present")
    digest = hashlib.sha256(CWE121.read_bytes()).hexdigest()
    assert may_transfer_plan(digest) is True
    assert may_transfer_plan(digest.upper()) is True


def test_may_transfer_plan_denies_missing_malformed_and_unknown() -> None:
    assert may_transfer_plan(None) is False
    assert may_transfer_plan("") is False
    assert may_transfer_plan("not-a-hash") is False
    assert may_transfer_plan("z" * 64) is False  # non-hex
    assert may_transfer_plan("0" * 64) is False  # well-formed but not a fixture
