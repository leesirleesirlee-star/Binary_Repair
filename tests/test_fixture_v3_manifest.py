"""Module documentation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.contracts import SecurityContract
from app.domain.manifests import (
    ManifestEntry,
    manifest_entries_from_files,
    manifest_set_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/public/CWE121_src_char_declare_cpy_51b"
TESTS = FIXTURE / "tests"

CASE_ID = "CWE121_src_char_declare_cpy_51b"
ORIGINAL_SHA256 = "aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393"
VALID_SHA256 = "17b2ee399db5ef43ba14978c497d71dc45d28aaa685ff90a60baaabc6f221e76"
INVALID_SHA256 = "d493f370a37b25f910eb72cb6f9202856e46be0e42e76922db38a30769f8190d"
MANIFEST_SET_SHA256 = "a564f0397a741b1a4fc386a3149d54550c1bfe7c02958309e021fb6f03333a78"
STDERR = "ERROR: input exceeds destination capacity\n"

CONTRACT = SecurityContract(
    target_entry="CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_51b_badSink",
    target_signature="void badSink(char *data)",
    valid_domain="C string inputs of length 0..49 bytes; the terminating NUL is copied",
    valid_behavior="strcpy into dest[50] and printLine(data); exit 0",
    forbidden_behavior="write past dest[50] for inputs of length 50 or more",
    invalid_input_response="print the exact stderr line and exit 1 without printing stdout",
    dest_capacity_bytes=50,
    max_valid_input_bytes=49,
    capacity_includes_terminator=True,
    invalid_exit_code=1,
    invalid_stdout="",
    invalid_stderr=STDERR,
    allowed_calls=("strcpy", "printLine"),
    environment_assumptions=("Linux x86-64", "non-PIE", "CET enabled"),
)


def _load(name: str) -> dict:
    return json.loads((TESTS / name).read_text(encoding="utf-8"))


def _cases() -> list[tuple[str, dict]]:
    out = []
    for name in ("valid.json", "invalid.json"):
        for case in _load(name)["tests"]:
            out.append((name, case))
    return out


def test_manifest_identity() -> None:
    for name in ("valid.json", "invalid.json"):
        data = _load(name)
        assert data["schema_version"] == 1
        assert data["case_id"] == CASE_ID
        assert data["original_sha256"] == ORIGINAL_SHA256
    ids = [case["id"] for _, case in _cases()]
    assert len(ids) == len(set(ids)) == 6


def test_valid_cases_are_preserved() -> None:
    cases = _load("valid.json")["tests"]
    assert [len(c["argv"][1].encode()) for c in cases] == [0, 1, 10, 49]
    for case in cases:
        assert case["kind"] == "valid"
        assert case["stdin_literal"] is None
        assert case["stdin_artifact"] is None
        expected = case["expected"]
        assert expected["return_code"] == 0
        assert expected["stderr"] == ""
        assert expected["stdout"] == case["argv"][1] + "\n"
        assert expected["allowed_output_files"] == []


def test_invalid_cases_take_the_reviewed_error_path() -> None:
    cases = _load("invalid.json")["tests"]
    assert [len(c["argv"][1].encode()) for c in cases] == [50, 100]
    for case in cases:
        assert case["kind"] == "invalid"
        expected = case["expected"]
        assert expected["return_code"] == CONTRACT.invalid_exit_code
        assert expected["stdout"] == CONTRACT.invalid_stdout
        assert expected["stderr"] == CONTRACT.invalid_stderr
        assert len(case["argv"][1].encode()) > CONTRACT.max_valid_input_bytes


def test_contract_matches_the_manifest_boundary() -> None:
    assert CONTRACT.dest_capacity_bytes == 50
    assert CONTRACT.max_valid_input_bytes == 49
    assert CONTRACT.capacity_includes_terminator is True
    assert CONTRACT.invalid_exit_code == 1
    assert CONTRACT.invalid_stdout == ""
    assert CONTRACT.invalid_stderr == STDERR
    for _, case in _cases():
        length = len(case["argv"][1].encode())
        if case["kind"] == "valid":
            assert length <= CONTRACT.max_valid_input_bytes
        else:
            assert length > CONTRACT.max_valid_input_bytes


def test_manifest_file_hashes_are_frozen() -> None:
    for name, expected in (("valid.json", VALID_SHA256), ("invalid.json", INVALID_SHA256)):
        got = hashlib.sha256((TESTS / name).read_bytes()).hexdigest()
        assert got == expected


def test_manifest_set_hash_is_frozen_and_order_independent() -> None:
    entries = manifest_entries_from_files(
        [TESTS / "valid.json", TESTS / "invalid.json"], root=TESTS
    )
    assert manifest_set_sha256(
        case_id=CASE_ID, original_sha256=ORIGINAL_SHA256, entries=entries
    ) == MANIFEST_SET_SHA256
    assert manifest_set_sha256(
        case_id=CASE_ID,
        original_sha256=ORIGINAL_SHA256,
        entries=[ManifestEntry(e.name, e.sha256) for e in reversed(entries)],
    ) == MANIFEST_SET_SHA256


def _run(elf: Path, arg: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(elf), arg], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
    )


def test_original_is_v0_on_invalid_inputs_if_binary_present() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF execution requires Linux")
    elf = FIXTURE / "original.elf"
    if not elf.exists():
        pytest.skip("original.elf is not in the shared tree")
    for name in ("valid.json", "invalid.json"):
        for case in _load(name)["tests"]:
            got = _run(elf, case["argv"][1])
            if case["kind"] == "valid":
                assert got.returncode == 0
                assert got.stdout == case["expected"]["stdout"].encode()
            else:
                expected = case["expected"]
                # V0: the original does NOT take the repaired error path.
                assert not (
                    got.returncode == expected["return_code"]
                    and got.stdout.decode() == expected["stdout"]
                    and got.stderr.decode() == expected["stderr"]
                )


def test_repaired_candidate_matches_v3_if_provided() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF execution requires Linux")
    raw = os.environ.get("BINARYREPAIR_CANDIDATE_ELF")
    if not raw:
        pytest.skip("set BINARYREPAIR_CANDIDATE_ELF to run V3 on a repaired candidate")
    elf = Path(raw)
    if not elf.exists():
        pytest.fail(f"candidate ELF not found: {elf}")
    for _, case in _cases():
        got = _run(elf, case["argv"][1])
        expected = case["expected"]
        assert got.returncode == expected["return_code"], case["id"]
        assert got.stdout.decode() == expected["stdout"], case["id"]
        assert got.stderr.decode() == expected["stderr"], case["id"]
