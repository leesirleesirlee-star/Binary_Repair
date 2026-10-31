"""Canonical manifest-set hash for the approval -> run binding.

A run binds three immutable things:

* the original input: ``jobs.original_sha256``
* the reviewed contract: ``contracts.sha256``
* the frozen test manifest set: ``runs.manifest_sha256``

The manifest files (``valid.json`` / ``invalid.json``) are stored byte-for-byte
as artifacts. ``manifest_sha256`` is the hash of a tiny canonical index over
their names and file hashes, not a re-serialization of the test cases. That
keeps the binding reproducible even when case order or formatting inside a file
differs from what a particular tool would emit.

Canonical form::

    {"case_id":"...",
     "manifests":[{"name":"invalid.json","sha256":"..."},
                  {"name":"valid.json","sha256":"..."}],
     "original_sha256":"...",
     "schema_version":1}

with sorted keys, compact separators, UTF-8, and ``manifests`` sorted by name.
File bytes are hashed as-is; reformatting a manifest file changes its hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MANIFEST_SET_SCHEMA_VERSION = 1

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9._:@+/-]{1,128}$")


def _clean_sha(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    text = value.strip().lower()
    if not _SHA_RE.match(text):
        raise ValueError(f"{field} must be 64 lowercase hex characters")
    return text


def _clean_name(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("manifest name must be a string")
    name = value.replace("\\", "/")
    if not name or name.startswith("/") or "\x00" in name:
        raise ValueError("manifest name must be a relative path")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("manifest name must not contain empty, '.' or '..' segments")
    if len(name) > 200:
        raise ValueError("manifest name is longer than 200 characters")
    return name


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_name(self.name))
        object.__setattr__(self, "sha256", _clean_sha(self.sha256, "manifest sha256"))


def manifest_set_payload(
    *,
    case_id: str,
    original_sha256: str,
    entries: Iterable[ManifestEntry],
) -> dict[str, object]:
    if not isinstance(case_id, str) or not _CASE_ID_RE.match(case_id):
        raise ValueError("case_id must be 1..128 printable ASCII characters")
    original = _clean_sha(original_sha256, "original_sha256")
    items = list(entries)
    if not items:
        raise ValueError("at least one manifest entry is required")
    names = [e.name for e in items]
    if len(set(names)) != len(names):
        raise ValueError("manifest names must be unique")
    return {
        "schema_version": MANIFEST_SET_SCHEMA_VERSION,
        "case_id": case_id,
        "original_sha256": original,
        "manifests": [
            {"name": e.name, "sha256": e.sha256}
            for e in sorted(items, key=lambda e: e.name)
        ],
    }


def manifest_set_json(
    *,
    case_id: str,
    original_sha256: str,
    entries: Iterable[ManifestEntry],
) -> str:
    return json.dumps(
        manifest_set_payload(
            case_id=case_id, original_sha256=original_sha256, entries=entries
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def manifest_set_sha256(
    *,
    case_id: str,
    original_sha256: str,
    entries: Iterable[ManifestEntry],
) -> str:
    text = manifest_set_json(
        case_id=case_id, original_sha256=original_sha256, entries=entries
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_entries_from_files(
    files: Iterable[Path], *, root: Path
) -> tuple[ManifestEntry, ...]:
    """Hash manifest files as bytes; names are relative POSIX paths under root."""
    root = Path(root).resolve()
    out: list[ManifestEntry] = []
    for raw in files:
        path = Path(raw).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"manifest file is outside root: {path}") from exc
        data = path.read_bytes()
        out.append(ManifestEntry(rel, hashlib.sha256(data).hexdigest()))
    return tuple(sorted(out, key=lambda e: e.name))
