"""Reviewed security contract and its canonical scope hash.

Ownership: member 5 (schema) + member 1 (job/contract persistence).

Why this module exists: the acceptance engine treats ``reviewed_scope_sha256`` as
opaque (verification-design §4.1), but the approval layer still has to compute it
exactly once and reproduce it later. This module defines the fields and the
canonical serialization so two callers cannot disagree about the same contract.

The hash is::

    sha256(utf-8(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False)))

where list-valued fields are sorted first, because their order has no meaning.
Anything that changes the reviewed scope changes the hash.

Text is restricted to printable ASCII (plus newline/tab) to match the team's
language rule for API/export data: UI may be Chinese, uploaded/exported contract
text is English ASCII.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

CONTRACT_SCHEMA_VERSION = 2
CONTRACT_SCHEMA_VERSION_NO_INPUT = 3
INPUT_MODES = ("argv", "stdin", "none")

# V0-V5 kinds accepted by the acceptance engine (verification-design §4.1.4).
OBLIGATION_KINDS = (
    "identity",
    "structure",
    "interface",
    "behaviour",
    "symbolic",
    "reproducibility",
)

_ENTRY_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_@.$]*|0x[0-9a-fA-F]{1,16})$")
_TEXT_RE = re.compile(r"^[\x20-\x7e\n\t]+$")
_MAX_TEXT = 2000
_MAX_ITEM = 300
_MAX_LIST = 32

_LIST_FIELDS = (
    "authorized_edits",
    "allowed_calls",
    "environment_assumptions",
    "required_obligations",
)
_TEXT_FIELDS = (
    "target_entry",
    "target_signature",
    "valid_domain",
    "valid_behavior",
    "forbidden_behavior",
)
_INVALID_TEXT_FIELDS = ("invalid_input_response",)
_NUMERIC_FIELDS = (
    "dest_capacity_bytes",
    "max_valid_input_bytes",
    "capacity_includes_terminator",
    "invalid_exit_code",
)
_INVALID_FIELDS = _INVALID_TEXT_FIELDS + (
    "max_valid_input_bytes",
    "invalid_exit_code",
    "invalid_stdout",
    "invalid_stderr",
)
_BASE_KEYS = (
    _TEXT_FIELDS + _LIST_FIELDS + ("dest_capacity_bytes",
    "capacity_includes_terminator", "required_obligations", "schema_version")
)
_REQUIRED_KEYS = (
    _BASE_KEYS + _INVALID_FIELDS
)
_REQUIRED_KEYS_V3_ARGV = _REQUIRED_KEYS + ("input_mode",)
_REQUIRED_KEYS_V3_NONE = _BASE_KEYS + ("input_mode",)


def _clean_text(
    value: Any, field: str, limit: int = _MAX_TEXT, *, allow_empty: bool = False,
    unicode_prose: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL")
    text = value.strip()
    if not text:
        if allow_empty:
            return ""
        raise ValueError(f"{field} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    if unicode_prose:
        if any(char not in "\n\t" and unicodedata.category(char).startswith("C") for char in text):
            raise ValueError(f"{field} must use printable text")
    elif not _TEXT_RE.match(text):
        raise ValueError(f"{field} must be printable ASCII")
    return text


def _clean_byte_text(value: Any, field: str, limit: int = _MAX_TEXT) -> str:
    """Exact bytes-as-text for expected stdout/stderr: no strip, no folding."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL")
    if len(value) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    if value and not _TEXT_RE.match(value):
        raise ValueError(f"{field} must be printable ASCII")
    return value


def _clean_list(
    values: Iterable[Any], field: str, *, pattern: re.Pattern | None = None,
    unicode_prose: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must be a sequence of strings, not a string")
    items = []
    for raw in values:
        item = _clean_text(raw, field, _MAX_ITEM, unicode_prose=unicode_prose)
        if pattern is not None and not pattern.match(item):
            raise ValueError(f"{field} contains an invalid identifier: {item!r}")
        items.append(item)
    if len(items) > _MAX_LIST:
        raise ValueError(f"{field} has more than {_MAX_LIST} items")
    if len(set(items)) != len(items):
        raise ValueError(f"{field} contains duplicates")
    return tuple(items)


@dataclass(frozen=True)
class SecurityContract:
    """A reviewed, versioned contract. Frozen: a change means a new revision."""

    target_entry: str
    target_signature: str
    valid_domain: str
    valid_behavior: str
    forbidden_behavior: str
    invalid_input_response: str | None = None
    dest_capacity_bytes: int = 1
    max_valid_input_bytes: int | None = None
    capacity_includes_terminator: bool = True
    invalid_exit_code: int | None = None
    invalid_stdout: str | None = None
    invalid_stderr: str | None = None
    authorized_edits: tuple[str, ...] = ()
    allowed_calls: tuple[str, ...] = ()
    environment_assumptions: tuple[str, ...] = ()
    required_obligations: tuple[str, ...] = OBLIGATION_KINDS
    schema_version: int = CONTRACT_SCHEMA_VERSION
    input_mode: str = "argv"

    def __post_init__(self) -> None:
        if self.schema_version not in (CONTRACT_SCHEMA_VERSION, CONTRACT_SCHEMA_VERSION_NO_INPUT):
            raise ValueError("unsupported schema_version")
        if self.schema_version == CONTRACT_SCHEMA_VERSION:
            if self.input_mode != "argv":
                raise ValueError("v2 contract must use input_mode argv")
            self._require_legacy_rejection_fields()
        else:
            if self.input_mode not in INPUT_MODES:
                raise ValueError(f"input_mode must be one of {INPUT_MODES}")
            if self.input_mode == "none":
                if any(getattr(self, field) is not None for field in _INVALID_FIELDS):
                    raise ValueError(
                        "input_mode none must omit invalid_* and max_valid_input_bytes; "
                        "sentinel values are not allowed"
                    )
            else:
                self._require_legacy_rejection_fields()
        entry = _clean_text(self.target_entry, "target_entry")
        if not _ENTRY_RE.match(entry):
            raise ValueError("target_entry must be a C identifier or 0x address")
        object.__setattr__(self, "target_entry", entry)
        object.__setattr__(
            self, "target_signature", _clean_text(self.target_signature, "target_signature", _MAX_ITEM)
        )
        unicode_prose = self.schema_version == CONTRACT_SCHEMA_VERSION_NO_INPUT
        for field in ("valid_domain", "valid_behavior", "forbidden_behavior"):
            object.__setattr__(
                self, field, _clean_text(
                    getattr(self, field), field, unicode_prose=unicode_prose and field != "valid_behavior",
                )
            )
        if self.input_mode != "none":
            object.__setattr__(
                self, "invalid_input_response",
                _clean_text(self.invalid_input_response, "invalid_input_response", unicode_prose=unicode_prose),
            )

        if not isinstance(self.dest_capacity_bytes, int) or isinstance(self.dest_capacity_bytes, bool):
            raise TypeError("dest_capacity_bytes must be an integer")
        if self.dest_capacity_bytes < 1:
            raise ValueError("dest_capacity_bytes must be >= 1")
        if not isinstance(self.capacity_includes_terminator, bool):
            raise TypeError("capacity_includes_terminator must be a bool")
        if self.input_mode != "none":
            if not isinstance(self.max_valid_input_bytes, int) or isinstance(self.max_valid_input_bytes, bool):
                raise TypeError("max_valid_input_bytes must be an integer")
            if self.max_valid_input_bytes < 0:
                raise ValueError("max_valid_input_bytes must be >= 0")
            if self.capacity_includes_terminator:
                if self.max_valid_input_bytes >= self.dest_capacity_bytes:
                    raise ValueError(
                        "max_valid_input_bytes must be < dest_capacity_bytes when the terminator is counted"
                    )
            elif self.max_valid_input_bytes > self.dest_capacity_bytes:
                raise ValueError(
                    "max_valid_input_bytes must be <= dest_capacity_bytes when the terminator is not counted"
                )
            if not isinstance(self.invalid_exit_code, int) or isinstance(self.invalid_exit_code, bool):
                raise TypeError("invalid_exit_code must be an integer")
            if not 0 <= self.invalid_exit_code <= 255:
                raise ValueError("invalid_exit_code must be in 0..255")
            object.__setattr__(
                self, "invalid_stdout", _clean_byte_text(self.invalid_stdout, "invalid_stdout")
            )
            object.__setattr__(
                self, "invalid_stderr", _clean_byte_text(self.invalid_stderr, "invalid_stderr")
            )

        object.__setattr__(
            self, "allowed_calls", _clean_list(self.allowed_calls, "allowed_calls", pattern=_ENTRY_RE)
        )
        for field in ("authorized_edits", "environment_assumptions", "required_obligations"):
            object.__setattr__(self, field, _clean_list(
                getattr(self, field), field, unicode_prose=unicode_prose and field == "environment_assumptions"
            ))
        unknown = [k for k in self.required_obligations if k not in OBLIGATION_KINDS]
        if unknown:
            raise ValueError(f"unknown obligation kinds: {unknown}")
        if not self.required_obligations:
            raise ValueError("required_obligations must not be empty")

    def _require_legacy_rejection_fields(self) -> None:
        missing = [field for field in _INVALID_FIELDS if getattr(self, field) is None]
        if missing:
            raise ValueError(f"missing contract fields: {sorted(missing)}")

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "target_entry": self.target_entry,
            "target_signature": self.target_signature,
            "valid_domain": self.valid_domain,
            "valid_behavior": self.valid_behavior,
            "forbidden_behavior": self.forbidden_behavior,
            "dest_capacity_bytes": self.dest_capacity_bytes,
            "capacity_includes_terminator": self.capacity_includes_terminator,
            "authorized_edits": list(self.authorized_edits),
            "allowed_calls": list(self.allowed_calls),
            "environment_assumptions": list(self.environment_assumptions),
            "required_obligations": list(self.required_obligations),
        }
        if self.schema_version == CONTRACT_SCHEMA_VERSION_NO_INPUT:
            payload["input_mode"] = self.input_mode
            if self.input_mode != "none":
                payload["invalid_input_response"] = self.invalid_input_response
                payload["max_valid_input_bytes"] = self.max_valid_input_bytes
                payload["invalid_exit_code"] = self.invalid_exit_code
                payload["invalid_stdout"] = self.invalid_stdout
                payload["invalid_stderr"] = self.invalid_stderr
        else:
            payload["invalid_input_response"] = self.invalid_input_response
            payload["max_valid_input_bytes"] = self.max_valid_input_bytes
            payload["invalid_exit_code"] = self.invalid_exit_code
            payload["invalid_stdout"] = self.invalid_stdout
            payload["invalid_stderr"] = self.invalid_stderr
        return payload

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        for field in _LIST_FIELDS:
            payload[field] = sorted(payload[field])
        return payload

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def sha256(self) -> str:
        """The value to persist as the contract hash / reviewed scope hash."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SecurityContract":
        if not isinstance(data, dict):
            raise TypeError("contract must be a dict")
        schema = data.get("schema_version")
        if schema == CONTRACT_SCHEMA_VERSION:
            required = set(_REQUIRED_KEYS)
        elif schema == CONTRACT_SCHEMA_VERSION_NO_INPUT:
            mode = data.get("input_mode")
            if mode not in INPUT_MODES:
                raise ValueError(f"input_mode must be one of {INPUT_MODES}")
            required = set(_REQUIRED_KEYS_V3_ARGV if mode != "none" else _REQUIRED_KEYS_V3_NONE)
        else:
            raise ValueError(f"unsupported schema_version: {schema!r}")
        unknown = set(data) - required
        if unknown:
            raise ValueError(f"unknown contract fields: {sorted(unknown)}")
        missing = required - set(data)
        if missing:
            raise ValueError(f"missing contract fields: {sorted(missing)}")
        if schema == CONTRACT_SCHEMA_VERSION_NO_INPUT and data["input_mode"] == "none":
            return cls(**{key: data[key] for key in required})
        return cls(**{key: data[key] for key in required})

# -- single canonicalization entry points for the controller -----------------
# The controller must call these; it must not re-implement sorting or JSON
# settings. Keeping the implementation here is what prevents two hashes for the
# same reviewed contract.

def canonical_contract_json(data: dict[str, Any]) -> str:
    """Return the canonical JSON text for a persisted contract dict."""
    return SecurityContract.from_dict(data).canonical_json()


def contract_sha256(data: dict[str, Any]) -> str:
    """Return the reviewed-scope hash for a persisted contract dict."""
    return SecurityContract.from_dict(data).sha256()
