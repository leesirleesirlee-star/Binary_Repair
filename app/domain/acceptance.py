"""Deterministic acceptance rules for BinaryRepair.

Pure functions: obligations in, verdict out. No I/O, no logging, no framework.
This is the only place that decides whether a repair attempt may be called a
success, so every caller - the API, the GUI, the export builder, the tests -
must use it rather than re-deriving an outcome.

The rules are a direct encoding of Tech Design section 9 ("Acceptance rules"):

* a counterexample or failed required behavioural/compatibility check rejects
  the candidate;
* an unsupported interface, placement, or profile returns ``unsupported`` and
  cannot satisfy the demonstration;
* a required ``not_run``, ``unknown``, timed-out, or incomplete check prevents
  success and produces ``inconclusive``;
* a tool or provider crash produces ``error``; cancellation stays distinct;
* ``success_within_scope`` requires every required obligation to pass, with
  matching final hashes and a nonempty reviewed scope;
* both routes must individually succeed for full MVP acceptance.

Precedence note: a definite failure outranks missing evidence, and missing
evidence outranks nothing. That ordering is why ``decide_attempt_outcome``
checks ``FAILED`` before ``NOT_RUN``/``UNKNOWN``/``TIMED_OUT``: if a candidate
was actually shown to be dangerous, the honest answer is ``rejected`` even if
some other check never ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.domain.outcomes import CheckResult, Outcome, ReasonCode


@dataclass(frozen=True)
class Obligation:
    """One required or optional piece of verification evidence.

    ``required`` describes whether the contract demands this evidence. Set
    ``result=CheckResult.NOT_RUN`` (not ``PASSED``) for a check that is not
    implemented or never executed.
    """

    kind: str
    required: bool
    result: CheckResult
    detail: str = ""


@dataclass(frozen=True)
class Verdict:
    """The acceptance decision plus the reason a human needs to read."""

    outcome: Outcome
    reason: ReasonCode | None = None
    failing_obligation: str | None = None
    detail: str = ""

    @property
    def is_success(self) -> bool:
        return self.outcome is Outcome.SUCCESS_WITHIN_SCOPE


def _non_success_verdict(obligation: Obligation) -> Verdict | None:
    """Map one required obligation to the verdict it forces, if any."""
    if obligation.result is CheckResult.FAILED:
        return Verdict(
            outcome=Outcome.REJECTED,
            reason=ReasonCode.REGRESSION_FAILED,
            failing_obligation=obligation.kind,
            detail=obligation.detail,
        )
    if obligation.result is CheckResult.NOT_RUN:
        return Verdict(
            outcome=Outcome.INCONCLUSIVE,
            reason=ReasonCode.REQUIRED_CHECK_NOT_RUN,
            failing_obligation=obligation.kind,
            detail=obligation.detail,
        )
    if obligation.result is CheckResult.UNKNOWN:
        return Verdict(
            outcome=Outcome.INCONCLUSIVE,
            reason=ReasonCode.SOLVER_UNKNOWN,
            failing_obligation=obligation.kind,
            detail=obligation.detail,
        )
    if obligation.result is CheckResult.TIMED_OUT:
        return Verdict(
            outcome=Outcome.INCONCLUSIVE,
            reason=ReasonCode.CHECK_TIMED_OUT,
            failing_obligation=obligation.kind,
            detail=obligation.detail,
        )
    return None


def decide_attempt_outcome(
    *,
    obligations: Sequence[Obligation],
    candidate_sha256: str | None = None,
    expected_sha256: str | None = None,
    reviewed_scope_sha256: str | None = None,
    cancelled: bool = False,
    error_reason: ReasonCode | None = None,
    error_detail: str = "",
    unsupported_reason: ReasonCode | None = None,
    unsupported_detail: str = "",
) -> Verdict:
    """Decide the outcome of one repair attempt (one route's candidate).

    Parameters
    ----------
    obligations:
        All verification obligations for this attempt.
    candidate_sha256:
        Hash of the final candidate ELF that was actually validated.
    expected_sha256:
        Approved candidate hash (not the original input hash). When the
        approval binds a candidate, ``candidate_sha256 != expected_sha256``
        means the evidence does not describe the approved artifact. When the
        approval does not bind a candidate, pass ``None``; never substitute
        ``job.original_sha256``.
    reviewed_scope_sha256:
        Hash of the reviewed verification scope. Empty/``None`` means no scope
        was reviewed, so success is not claimable.
    cancelled:
        The operator stopped the attempt. Checked before every other rule,
        because a cancelled attempt has no authoritative result.
    error_reason / unsupported_reason:
        Set by the caller when a tool/provider crashed or a capability is
        outside the supported profile. Both outrank per-check results.

    Returns
    -------
    Verdict
        Never raises for ordinary evidence states. An empty obligation list is
        not success: it is ``inconclusive`` because nothing was demonstrated.
    """

    # 1. Operator cancellation is authoritative: no partial evidence becomes a
    #    success, and a crash after cancellation does not become an error.
    if cancelled:
        return Verdict(
            outcome=Outcome.CANCELLED,
            reason=None,
            detail="operator cancelled the attempt",
        )

    # 2. A tool, provider, or system failure is not evidence about the
    #    candidate, so it must not be reported as rejection or success.
    if error_reason is not None:
        return Verdict(
            outcome=Outcome.ERROR,
            reason=error_reason,
            detail=error_detail,
        )

    # 3. A capability the supported profile cannot provide is unsupported.
    if unsupported_reason is not None:
        return Verdict(
            outcome=Outcome.UNSUPPORTED,
            reason=unsupported_reason,
            detail=unsupported_detail,
        )

    required = [obligation for obligation in obligations if obligation.required]

    # 4. Definite required failure rejects the candidate.
    for obligation in required:
        verdict = _non_success_verdict(obligation)
        if verdict is not None and verdict.outcome is Outcome.REJECTED:
            return verdict

    # 5. Missing or undecided required evidence is inconclusive, not success.
    for obligation in required:
        verdict = _non_success_verdict(obligation)
        if verdict is not None:
            return verdict

    # 6. Evidence must describe the approved artifact. A mismatch means the
    #    evidence is about something else, so the claim is unproven.
    if expected_sha256 is not None and candidate_sha256 != expected_sha256:
        return Verdict(
            outcome=Outcome.INCONCLUSIVE,
            reason=ReasonCode.HASH_MISMATCH,
            detail=(
                f"candidate hash {candidate_sha256!r} does not match "
                f"approved hash {expected_sha256!r}"
            ),
        )

    # 7. Success is only ever "within a scope". No reviewed scope, no claim.
    if not reviewed_scope_sha256:
        return Verdict(
            outcome=Outcome.INCONCLUSIVE,
            reason=ReasonCode.EMPTY_SCOPE,
            detail="no reviewed verification scope was recorded",
        )

    # 8. An empty required set proves nothing. This guard is what stops a job
    #    whose checks were all silently dropped from showing a green result.
    if not required:
        return Verdict(
            outcome=Outcome.INCONCLUSIVE,
            reason=ReasonCode.NO_REQUIRED_EVIDENCE,
            detail="no required obligations were evaluated",
        )

    # 9. Every required obligation passed, the hashes agree, the scope is
    #    reviewed: the candidate succeeds within that stated scope.
    return Verdict(
        outcome=Outcome.SUCCESS_WITHIN_SCOPE,
        detail=f"{len(required)} required obligations passed",
    )


def decide_run_outcome(*, route_a: Verdict, route_b: Verdict) -> Verdict:
    """Decide the outcome of a run that must execute both routes.

    The MVP requires both routes to succeed on the same original binary. A run
    is therefore successful only when both route verdicts are
    ``success_within_scope``. Otherwise the run reports the most serious
    observed condition so the analyst is not told "one of two worked".
    """

    for route_name, verdict in (("route_a", route_a), ("route_b", route_b)):
        if verdict.outcome is Outcome.CANCELLED:
            return Verdict(
                outcome=Outcome.CANCELLED,
                detail=f"{route_name} was cancelled",
            )

    for route_name, verdict in (("route_a", route_a), ("route_b", route_b)):
        if verdict.outcome is Outcome.ERROR:
            return Verdict(
                outcome=Outcome.ERROR,
                reason=verdict.reason,
                detail=f"{route_name}: {verdict.detail}",
            )

    for route_name, verdict in (("route_a", route_a), ("route_b", route_b)):
        if verdict.outcome is Outcome.UNSUPPORTED:
            return Verdict(
                outcome=Outcome.UNSUPPORTED,
                reason=verdict.reason,
                detail=f"{route_name}: {verdict.detail}",
            )

    for route_name, verdict in (("route_a", route_a), ("route_b", route_b)):
        if verdict.outcome is Outcome.REJECTED:
            return Verdict(
                outcome=Outcome.REJECTED,
                reason=verdict.reason,
                failing_obligation=verdict.failing_obligation,
                detail=f"{route_name}: {verdict.detail}",
            )

    for route_name, verdict in (("route_a", route_a), ("route_b", route_b)):
        if verdict.outcome is Outcome.INCONCLUSIVE:
            return Verdict(
                outcome=Outcome.INCONCLUSIVE,
                reason=verdict.reason,
                failing_obligation=verdict.failing_obligation,
                detail=f"{route_name}: {verdict.detail}",
            )

    # Both routes succeeded within their scopes.
    return Verdict(
        outcome=Outcome.SUCCESS_WITHIN_SCOPE,
        detail="both routes succeeded within their reviewed scopes",
    )
