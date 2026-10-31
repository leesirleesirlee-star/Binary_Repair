"""Public candidate boundary (verification-design §4.3).

This is the import path the verification skeleton binds:
``pytest.importorskip("app.domain.candidates")``.

The implementation lives in ``binaryrepair_ai/schemas.py`` so the provider and the
boundary share one source of truth. Keep this file thin: it exists to give the rest
of the app a stable name, not to become a second validator.

Two levels, deliberately:

* ``validate_candidate(payload)`` -- stateless. Structure, identity shape, route,
  target whitelist, template/parameters, Route B content, text limits. It cannot
  prove that a hash or an evidence id was issued by this job; that needs a context.
* ``validate_candidate_for_context(payload, ctx)`` -- adds the job binding
  (hashes, evidence issuance, recovered-function hash) used by the provider.
"""

from __future__ import annotations

from typing import Iterable

from pydantic import ValidationError

from binaryrepair_ai.schemas import (
    ROUTE_A,
    CandidateRejected,
    CandidateV1,
    RepairContext,
    TemplateParam,
    TemplateSpec,
    check_candidate,
    check_route_a,
    check_route_b,
)

__all__ = [
    "CandidateRejected",
    "CandidateV1",
    "DEFAULT_ALLOWED_TARGETS",
    "DEFAULT_TEMPLATES",
    "RepairContext",
    "TemplateParam",
    "TemplateSpec",
    "may_leave_host",
    "validate",
    "validate_candidate",
    "validate_candidate_for_context",
]

# Targets approved for the public fixture. Route A/B candidates may not extend this.
DEFAULT_ALLOWED_TARGETS = frozenset({"copy_input", "copy_name"})

# Templates reviewed for the public fixture. ``bounded-copy-guard`` is the name used
# by the verification skeleton; ``guard_capacity`` is the one used by the kit demos.
DEFAULT_TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(
        template_id="bounded-copy-guard",
        description="Reject input at or above the destination capacity before any copy.",
        parameters={"capacity": TemplateParam(min_value=1, max_value=4096)},
    ),
    TemplateSpec(
        template_id="guard_capacity",
        description="Reject input at or above capacity and return a fixed error code.",
        parameters={
            "capacity": TemplateParam(min_value=1, max_value=4096),
            "error_return": TemplateParam(min_value=-4096, max_value=0),
        },
    ),
)

# The MVP repairs pure functions. A candidate may call nothing that the server did
# not explicitly allow.
DEFAULT_ALLOWED_CALLS: tuple[str, ...] = ()

# Classifications that may be sent to a hosted provider without further paperwork.
PUBLIC_CLASSIFICATIONS = frozenset({"public", "synthetic"})


def validate_candidate(
    payload: dict,
    *,
    allowed_targets: Iterable[str] | None = None,
    templates: Iterable[TemplateSpec] | None = None,
    allowed_calls: Iterable[str] | None = None,
    max_replacement_chars: int = 20_000,
) -> CandidateV1:
    """Validate one model-proposed candidate. Returns a non-None ``CandidateV1``.

    Raises ``CandidateRejected`` (structural or semantic) for any invalid payload.
    Never repairs, defaults, or "cleans up" a candidate.
    """
    try:
        cand = CandidateV1.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        raise CandidateRejected(
            "schema_invalid",
            f"schema validation failed at {loc}: {first.get('msg', 'invalid')}",
        ) from exc

    targets = DEFAULT_ALLOWED_TARGETS if allowed_targets is None else frozenset(allowed_targets)
    if cand.target_entry not in targets:
        raise CandidateRejected(
            "scope_violation",
            f"target_entry {cand.target_entry!r} is outside the allowed targets",
        )

    if cand.route == ROUTE_A:
        check_route_a(cand, tuple(templates) if templates is not None else DEFAULT_TEMPLATES)
    else:
        check_route_b(
            cand,
            target_entry=cand.target_entry,
            allowed_calls=(
                tuple(allowed_calls) if allowed_calls is not None else DEFAULT_ALLOWED_CALLS
            ),
            max_replacement_chars=max_replacement_chars,
        )
    return cand


# Alias for the shorthand used in the verification prose (``validate(payload)``).
validate = validate_candidate


def validate_candidate_for_context(payload: dict, ctx: RepairContext) -> CandidateV1:
    """Job-bound validation used before a candidate is persisted or verified."""
    cand = validate_candidate(
        payload,
        allowed_targets=[ctx.target_entry],
        templates=ctx.templates,
        allowed_calls=ctx.allowed_calls,
        max_replacement_chars=ctx.max_replacement_chars,
    )
    check_candidate(cand, ctx)
    return cand


def may_leave_host(
    classification: str,
    derived: bool = False,
    *,
    authorized: bool = False,
) -> bool:
    """Can this content be sent to a hosted provider?

    Default deny. Public/synthetic may leave. Client material stays local unless the
    classification is ``client_authorized`` *and* the caller explicitly passes
    ``authorized=True`` (the caller must have recorded the authorization). ``derived``
    is accepted for signature compatibility and never loosens a client rule.
    """
    kind = (classification or "").strip().lower()
    if kind in PUBLIC_CLASSIFICATIONS:
        return True
    if kind == "client_authorized" and authorized:
        return True
    return False
