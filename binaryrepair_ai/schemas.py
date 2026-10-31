"""Candidate schema v1 + context the server is allowed to reveal to the model.

The model never decides policy. The server sends a closed list of templates (Route A)
or one recovered function plus an allowlist of callees (Route B); the returned object
is validated against that context before anything downstream sees it.

Schema mirrors TechDesign section 10:

    schema_version, original_sha256, contract_sha256, analysis_sha256, route,
    target_entry, evidence_ids, template_id + parameters (A),
    recovered_function_sha256 + replacement_c (B),
    expected_effects, assumptions, uncertainties

Wire values are ``route_a`` / ``route_b`` (agreed with the verification skeleton,
2026-10-18). Prose may still say "Route A"; JSON never does.

Two entry points exist on purpose:

* ``CandidateV1`` + ``check_route_a/b`` -- pure, usable without a job context;
* ``check_candidate(cand, ctx)`` -- adds identity, evidence and recovered-hash binding.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
EVIDENCE_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
ENTRY_PATTERN = r"^(?:[A-Za-z_][A-Za-z0-9_@.$]*|0x[0-9a-fA-F]{1,16})$"

SHA256_RE = re.compile(SHA256_PATTERN)
EVIDENCE_RE = re.compile(EVIDENCE_PATTERN)
ENTRY_RE = re.compile(ENTRY_PATTERN)

# Expressed as annotated types so the exported JSON Schema carries the constraints;
# the field validators below keep the friendlier error messages.
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
Entry = Annotated[str, StringConstraints(pattern=ENTRY_PATTERN)]
EvidenceId = Annotated[str, StringConstraints(pattern=EVIDENCE_PATTERN)]
TextItem = Annotated[str, StringConstraints(max_length=512)]

Route = Literal["route_a", "route_b"]
ROUTE_A: Route = "route_a"
ROUTE_B: Route = "route_b"

# Route B: a pure function for the MVP. These constructs are outside the reviewed
# interface and are rejected before compilation, not "fixed later".
_FORBIDDEN_C = re.compile(
    r"(#\s*include|#\s*pragma|\basm\b|__asm|__attribute__|\bsystem\s*\(|\bpopen\s*\("
    r"|\bexec[lv]p?e?\s*\(|\bfork\s*\(|\bdlopen\s*\(|\bsocket\s*\(|\bopen\s*\()",
    re.IGNORECASE,
)
_CALL_RE = re.compile(r"(?<![\w.>])([A-Za-z_]\w*)\s*\(")
_C_KEYWORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "case", "do", "else",
    "typeof", "alignof", "defined", "_static_assert", "static_assert",
}

# Free-text fields are reviewed by a human and displayed in the GUI. They must not
# read like instructions to a tool, or like a way to move data off the host. This is
# a backstop, not the security boundary: the real guarantee is that a candidate can
# never change permissions, scope, or tool behaviour.
_INSTRUCTION_RE = re.compile(
    r"("
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?"
    r"|disregard\s+.{0,30}instructions?"
    r"|system\s+prompt"
    r"|you\s+are\s+now"
    r"|\brun\s*:"
    r"|\brm\s+-rf\b"
    r"|\bcurl\s+"
    r"|\bsudo\b"
    r"|https?://"
    r"|</?untrusted"
    r"|\btool_call\b"
    r")",
    re.IGNORECASE,
)

_MAX_TEXT_ITEM = 512
_MAX_LIST_ITEMS = 32


class CandidateRejected(Exception):
    """Returned as a typed Failure; never escapes as a 500."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class TemplateParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_value: int
    max_value: int

    @model_validator(mode="after")
    def _ordered(self) -> "TemplateParam":
        if self.min_value > self.max_value:
            raise ValueError("min_value > max_value")
        return self


class TemplateSpec(BaseModel):
    """A reviewed, deterministic repair template. The model may only parameterize it."""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=300)
    parameters: dict[str, TemplateParam] = Field(default_factory=dict)


class RepairContext(BaseModel):
    """Everything the trusted host is willing to send for one candidate request."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    route: Route
    original_sha256: Sha256
    contract_sha256: Sha256
    analysis_sha256: Sha256
    target_entry: Entry
    target_signature: str | None = Field(default=None, max_length=300)
    contract_summary: str = Field(min_length=1, max_length=4000)
    allowed_evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=32)

    # Route A
    templates: list[TemplateSpec] = Field(default_factory=list, max_length=8)

    # Route B
    recovered_function_sha256: Sha256 | None = None
    recovered_c: str | None = None
    allowed_calls: list[str] = Field(default_factory=list, max_length=32)
    max_replacement_chars: int = Field(default=8000, ge=200, le=20000)

    @field_validator("original_sha256", "contract_sha256", "analysis_sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not SHA256_RE.match(v):
            raise ValueError("must be a lowercase 64-hex sha256")
        return v

    @field_validator("recovered_function_sha256")
    @classmethod
    def _sha_opt(cls, v: str | None) -> str | None:
        if v is not None and not SHA256_RE.match(v):
            raise ValueError("must be a lowercase 64-hex sha256")
        return v

    @field_validator("target_entry")
    @classmethod
    def _entry(cls, v: str) -> str:
        if not ENTRY_RE.match(v):
            raise ValueError("target_entry must be a C identifier or 0x address")
        return v

    @field_validator("allowed_evidence_ids")
    @classmethod
    def _evidence(cls, v: list[str]) -> list[str]:
        for item in v:
            if not EVIDENCE_RE.match(item):
                raise ValueError(f"bad evidence id: {item!r}")
        return v

    @field_validator("allowed_calls")
    @classmethod
    def _calls(cls, v: list[str]) -> list[str]:
        for item in v:
            if not ENTRY_RE.match(item) or item.startswith("0x"):
                raise ValueError(f"bad callee name: {item!r}")
        return v

    @model_validator(mode="after")
    def _route_shape(self) -> "RepairContext":
        if self.route == ROUTE_A:
            if not self.templates:
                raise ValueError("Route A requires at least one reviewed template")
            if self.recovered_c is not None:
                raise ValueError("Route A context must not carry recovered C")
        else:
            if not self.recovered_c:
                raise ValueError("Route B requires recovered_c")
            if self.recovered_function_sha256 is None:
                raise ValueError("Route B requires recovered_function_sha256")
            if len(self.recovered_c) > 60_000:
                raise ValueError("recovered_c too large for one request")
        return self

    # Convenience for prompt building / budgeting.
    def template(self, template_id: str) -> TemplateSpec | None:
        return next((t for t in self.templates if t.template_id == template_id), None)


class CandidateV1(BaseModel):
    """Strict model output. Unknown fields are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    original_sha256: Sha256
    contract_sha256: Sha256
    analysis_sha256: Sha256
    route: Route
    target_entry: Entry
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=16)

    template_id: str | None = Field(default=None, max_length=64)
    parameters: dict[str, int] | None = None

    recovered_function_sha256: Sha256 | None = None
    replacement_c: str | None = Field(default=None, max_length=20_000)

    expected_effects: list[TextItem] = Field(min_length=1, max_length=_MAX_LIST_ITEMS)
    assumptions: list[TextItem] = Field(default_factory=list, max_length=_MAX_LIST_ITEMS)
    uncertainties: list[TextItem] = Field(default_factory=list, max_length=_MAX_LIST_ITEMS)

    @field_validator("original_sha256", "contract_sha256", "analysis_sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not SHA256_RE.match(v):
            raise ValueError("must be a lowercase 64-hex sha256")
        return v

    @field_validator("recovered_function_sha256")
    @classmethod
    def _sha_opt(cls, v: str | None) -> str | None:
        if v is not None and not SHA256_RE.match(v):
            raise ValueError("must be a lowercase 64-hex sha256")
        return v

    @field_validator("target_entry")
    @classmethod
    def _entry(cls, v: str) -> str:
        if not ENTRY_RE.match(v):
            raise ValueError("target_entry must be a C identifier or 0x address")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def _evidence(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate evidence_ids")
        for item in v:
            if not EVIDENCE_RE.match(item):
                raise ValueError(f"bad evidence id: {item!r}")
        return v

    @field_validator("expected_effects", "assumptions", "uncertainties")
    @classmethod
    def _text_items(cls, v: list[str]) -> list[str]:
        out = []
        for item in v:
            if "\x00" in item:
                raise ValueError("NUL byte in text")
            item = item.strip()
            if not item:
                raise ValueError("empty text item")
            if len(item) > _MAX_TEXT_ITEM:
                raise ValueError(f"text item longer than {_MAX_TEXT_ITEM} chars")
            if _INSTRUCTION_RE.search(item):
                # Reject the whole candidate; never "clean it up and accept".
                raise ValueError("text contains instruction-like or URL content")
            out.append(item)
        return out

    @model_validator(mode="after")
    def _route_shape(self) -> "CandidateV1":
        if self.route == ROUTE_A:
            if not self.template_id or self.parameters is None:
                raise ValueError("Route A candidate requires template_id and parameters")
            if self.replacement_c is not None or self.recovered_function_sha256 is not None:
                raise ValueError("Route A candidate must not carry Route B fields")
            if len(self.parameters) > 8:
                raise ValueError("too many parameters")
        else:
            if not self.replacement_c or self.recovered_function_sha256 is None:
                raise ValueError("Route B candidate requires replacement_c and recovered hash")
            if self.template_id is not None or self.parameters is not None:
                raise ValueError("Route B candidate must not carry template fields")
            if "\x00" in self.replacement_c:
                raise ValueError("NUL byte in replacement_c")
        return self


def check_route_a(cand: CandidateV1, templates: Sequence[TemplateSpec]) -> None:
    """Template membership and parameter ranges. Context-free; raises CandidateRejected."""
    if cand.route != ROUTE_A:
        raise CandidateRejected("schema_invalid", "candidate is not a route_a candidate")
    assert cand.template_id is not None and cand.parameters is not None

    template = next((t for t in templates if t.template_id == cand.template_id), None)
    if template is None:
        raise CandidateRejected(
            "scope_violation", f"template_id {cand.template_id!r} was not offered"
        )

    expected = set(template.parameters)
    got = set(cand.parameters)
    if got != expected:
        raise CandidateRejected(
            "schema_invalid",
            f"parameters must be exactly {sorted(expected)}, got {sorted(got)}",
        )

    for name, value in cand.parameters.items():
        spec = template.parameters[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise CandidateRejected("schema_invalid", f"parameter {name} must be an integer")
        if not (spec.min_value <= value <= spec.max_value):
            raise CandidateRejected(
                "schema_invalid",
                f"parameter {name}={value} outside [{spec.min_value}, {spec.max_value}]",
            )


def check_route_b(
    cand: CandidateV1,
    *,
    target_entry: str,
    allowed_calls: Sequence[str],
    max_replacement_chars: int,
) -> None:
    """Content and callee checks for a replacement function. Raises CandidateRejected."""
    if cand.route != ROUTE_B:
        raise CandidateRejected("schema_invalid", "candidate is not a route_b candidate")
    assert cand.replacement_c is not None
    src = cand.replacement_c

    if len(src) > max_replacement_chars:
        raise CandidateRejected(
            "schema_invalid", f"replacement_c exceeds {max_replacement_chars} chars"
        )

    if not re.search(rf"\b{re.escape(target_entry)}\s*\(", src):
        raise CandidateRejected(
            "schema_invalid", f"replacement_c does not define {target_entry}()"
        )

    if _FORBIDDEN_C.search(src):
        raise CandidateRejected(
            "scope_violation", "replacement_c contains a forbidden construct"
        )

    # Every called identifier must be on the allowlist (or the function itself).
    callees = {m.group(1) for m in _CALL_RE.finditer(src)} - _C_KEYWORDS
    callees.discard(target_entry)
    unknown = sorted(c for c in callees if c not in set(allowed_calls))
    if unknown:
        raise CandidateRejected(
            "scope_violation", f"replacement_c calls identifiers outside the allowed set: {unknown}"
        )


def check_candidate(cand: CandidateV1, ctx: RepairContext) -> None:
    """Job-bound checks: identity, evidence issuance, recovered hash, then route rules."""
    if cand.route != ctx.route:
        raise CandidateRejected("schema_invalid", "candidate route does not match request")

    for name in ("original_sha256", "contract_sha256", "analysis_sha256"):
        if getattr(cand, name) != getattr(ctx, name):
            raise CandidateRejected(
                "identity_mismatch", f"{name} does not match the reviewed job identity"
            )

    if cand.target_entry != ctx.target_entry:
        raise CandidateRejected("scope_violation", "target_entry is outside the approved target")

    allowed = set(ctx.allowed_evidence_ids)
    unknown = [e for e in cand.evidence_ids if e not in allowed]
    if unknown:
        raise CandidateRejected("scope_violation", f"unknown evidence ids: {unknown[:3]}")

    if cand.route == ROUTE_A:
        check_route_a(cand, ctx.templates)
        return

    if cand.recovered_function_sha256 != ctx.recovered_function_sha256:
        raise CandidateRejected(
            "identity_mismatch", "recovered_function_sha256 does not match the supplied function"
        )
    check_route_b(
        cand,
        target_entry=ctx.target_entry,
        allowed_calls=ctx.allowed_calls,
        max_replacement_chars=ctx.max_replacement_chars,
    )
