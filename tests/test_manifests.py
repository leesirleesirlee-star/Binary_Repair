from __future__ import annotations

import hashlib

import pytest

from app.domain.manifests import (
    ManifestEntry,
    manifest_entries_from_files,
    manifest_set_json,
    manifest_set_sha256,
)

CASE = "CWE121_src_char_declare_cpy_51b"
ORIG = "aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393"
A = "a" * 64
B = "b" * 64


def entries():
    return [ManifestEntry("valid.json", A), ManifestEntry("invalid.json", B)]


def test_order_does_not_change_hash() -> None:
    h1 = manifest_set_sha256(case_id=CASE, original_sha256=ORIG, entries=entries())
    h2 = manifest_set_sha256(
        case_id=CASE, original_sha256=ORIG, entries=list(reversed(entries()))
    )
    assert h1 == h2


def test_json_sorts_manifests_by_name() -> None:
    text = manifest_set_json(case_id=CASE, original_sha256=ORIG, entries=entries())
    assert text.index("invalid.json") < text.index("valid.json")
    assert " " not in text


def test_duplicate_names_rejected() -> None:
    with pytest.raises(ValueError):
        manifest_set_sha256(
            case_id=CASE,
            original_sha256=ORIG,
            entries=[ManifestEntry("valid.json", A), ManifestEntry("valid.json", B)],
        )


def test_bad_hash_rejected() -> None:
    with pytest.raises(ValueError):
        ManifestEntry("valid.json", "not-a-hash")


def test_path_traversal_rejected() -> None:
    with pytest.raises(ValueError):
        ManifestEntry("../valid.json", A)
    with pytest.raises(ValueError):
        ManifestEntry("/valid.json", A)


def test_file_helper_hashes_bytes(tmp_path) -> None:
    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    valid.write_text("{\"tests\":[]}\n")
    invalid.write_text("{\"tests\":[1]}\n")
    got = manifest_entries_from_files([valid, invalid], root=tmp_path)
    assert [e.name for e in got] == ["invalid.json", "valid.json"]
    assert got[0].sha256 == hashlib.sha256(invalid.read_bytes()).hexdigest()


def test_file_change_changes_hash(tmp_path) -> None:
    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    valid.write_text("{}\n")
    invalid.write_text("{}\n")
    before = manifest_set_sha256(
        case_id=CASE,
        original_sha256=ORIG,
        entries=manifest_entries_from_files([valid, invalid], root=tmp_path),
    )
    valid.write_text("{}\n\n")
    after = manifest_set_sha256(
        case_id=CASE,
        original_sha256=ORIG,
        entries=manifest_entries_from_files([valid, invalid], root=tmp_path),
    )
    assert before != after
