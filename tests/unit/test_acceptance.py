"""Negative-first tests for the acceptance rules.

The value of the acceptance engine is not that a passing run shows green - it
is that every incomplete or ambiguous state refuses to. These tests therefore
concentrate on the ways a result must *not* be accepted.

Every test here is pure logic: no binary is read, written, or executed.
"""

from __future__ import annotations

import pytest

from app.domain.acceptance import (
    Obligation,
    Verdict,
    decide_attempt_outcome,
    decide_run_outcome,
)
from app.domain.outcomes import (
    NON_SUCCESS_OUTCOMES,
    CheckResult,
    Outcome,
    ReasonCode,
)

# A syntactically plausible reviewed scope, used when a test needs success to
# be otherwise possible.
SCOPE = "a" * 64
CANDIDATE = "b" * 64


def passed(kind: str = "structure", *, required: bool = True) -> Obligation:
    return Obligation(kind=kind, required=required, result=CheckResult.PASSED)


def failed(kind: str = "regression", *, required: bool = True) -> Obligation:
    return Obligation(kind=kind, required=required, result=CheckResult.FAILED)


def not_run(kind: str = "symbolic", *, required: bool = True) -> Obligation:
    return Obligation(kind=kind, required=required, result=CheckResult.NOT_RUN)


def success(**overrides) -> Verdict:
    """Decide with an otherwise-successful baseline, applying overrides."""
    kwargs = dict(
        obligations=[passed("structure"), passed("regression")],
        candidate_sha256=CANDIDATE,
        expected_sha256=CANDIDATE,
        reviewed_scope_sha256=SCOPE,
    )
    kwargs.update(overrides)
    return decide_attempt_outcome(**kwargs)


# ---------------------------------------------------------------------------
# Success is possible, and requires every guard to hold
# ---------------------------------------------------------------------------


def test_all_required_passed_with_matching_hashes_is_success() -> None:
    verdict = success()
    assert verdict.outcome is Outcome.SUCCESS_WITHIN_SCOPE
    assert verdict.is_success


def test_optional_failure_does_not_block_success() -> None:
    verdict = success(
        obligations=[passed("structure"), failed("optional_extra", required=False)]
    )
    assert verdict.outcome is Outcome.SUCCESS_WITHIN_SCOPE


# ---------------------------------------------------------------------------
# Missing evidence can never be success -- the central rule
# ---------------------------------------------------------------------------


def test_required_not_run_is_inconclusive_not_success() -> None:
    verdict = success(obligations=[passed("structure"), not_run("symbolic")])
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.REQUIRED_CHECK_NOT_RUN
    assert verdict.failing_obligation == "symbolic"


def test_required_unknown_is_inconclusive() -> None:
    verdict = success(
        obligations=[passed("structure"), Obligation("symbolic", True, CheckResult.UNKNOWN)]
    )
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.SOLVER_UNKNOWN


def test_required_timed_out_is_inconclusive() -> None:
    verdict = success(
        obligations=[
            passed("structure"),
            Obligation("symbolic", True, CheckResult.TIMED_OUT),
        ]
    )
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.CHECK_TIMED_OUT


def test_empty_obligation_list_is_inconclusive() -> None:
    verdict = success(obligations=[])
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.NO_REQUIRED_EVIDENCE


def test_only_optional_obligations_cannot_establish_success() -> None:
    verdict = success(obligations=[passed("structure", required=False)])
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.NO_REQUIRED_EVIDENCE


# ---------------------------------------------------------------------------
# Failure rejects, and outranks missing evidence
# ---------------------------------------------------------------------------


def test_required_failure_rejects_candidate() -> None:
    verdict = success(obligations=[passed("structure"), failed("regression")])
    assert verdict.outcome is Outcome.REJECTED
    assert verdict.failing_obligation == "regression"


def test_definite_failure_outranks_not_run() -> None:
    verdict = success(
        obligations=[failed("regression"), not_run("symbolic"), passed("structure")]
    )
    assert verdict.outcome is Outcome.REJECTED, (
        "a shown-dangerous candidate must be rejected, not softened to inconclusive "
        "because another check did not run"
    )


# ---------------------------------------------------------------------------
# Identity and scope guards
# ---------------------------------------------------------------------------


def test_candidate_vs_explicit_approved_candidate_mismatch_blocks_success() -> None:
    # expected_sha256 is an approved *candidate* hash, not original input.
    verdict = success(candidate_sha256="c" * 64)
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.HASH_MISMATCH


def test_missing_candidate_hash_blocks_success_when_approval_was_bound() -> None:
    verdict = success(candidate_sha256=None)
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.HASH_MISMATCH


def test_empty_reviewed_scope_blocks_success() -> None:
    verdict = success(reviewed_scope_sha256="")
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.EMPTY_SCOPE


def test_none_reviewed_scope_blocks_success() -> None:
    verdict = success(reviewed_scope_sha256=None)
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason is ReasonCode.EMPTY_SCOPE


# ---------------------------------------------------------------------------
# Cancellation and error stay distinct from rejection
# ---------------------------------------------------------------------------


def test_cancelled_dominates_everything() -> None:
    verdict = success(
        obligations=[failed("regression")],
        cancelled=True,
        error_reason=ReasonCode.TOOL_FAILED,
    )
    assert verdict.outcome is Outcome.CANCELLED


def test_error_dominates_check_results() -> None:
    verdict = success(
        obligations=[failed("regression")],
        error_reason=ReasonCode.TOOL_FAILED,
        error_detail="angr crashed",
    )
    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason is ReasonCode.TOOL_FAILED
    assert "angr crashed" in verdict.detail


def test_unsupported_dominates_check_results() -> None:
    verdict = success(
        obligations=[not_run("symbolic")],
        unsupported_reason=ReasonCode.UNSUPPORTED_PLACEMENT,
    )
    assert verdict.outcome is Outcome.UNSUPPORTED
    assert verdict.reason is ReasonCode.UNSUPPORTED_PLACEMENT


def test_cancelled_is_not_error() -> None:
    cancelled = success(cancelled=True)
    errored = success(error_reason=ReasonCode.TOOL_FAILED)
    assert cancelled.outcome is not errored.outcome


# ---------------------------------------------------------------------------
# Run-level: both routes are mandatory
# ---------------------------------------------------------------------------


def attempt_success() -> Verdict:
    return success()


def attempt_rejected() -> Verdict:
    return success(obligations=[failed("regression")])


def attempt_inconclusive() -> Verdict:
    return success(obligations=[not_run("symbolic")])


def test_both_routes_succeed_gives_run_success() -> None:
    verdict = decide_run_outcome(route_a=attempt_success(), route_b=attempt_success())
    assert verdict.outcome is Outcome.SUCCESS_WITHIN_SCOPE


def test_one_successful_route_is_not_enough() -> None:
    verdict = decide_run_outcome(route_a=attempt_success(), route_b=attempt_rejected())
    assert verdict.outcome is Outcome.REJECTED
    assert verdict.outcome is not Outcome.SUCCESS_WITHIN_SCOPE


def test_success_plus_unsupported_route_is_unsupported() -> None:
    unsupported = decide_attempt_outcome(
        obligations=[passed()],
        candidate_sha256=CANDIDATE,
        expected_sha256=CANDIDATE,
        reviewed_scope_sha256=SCOPE,
        unsupported_reason=ReasonCode.UNSUPPORTED_INTERFACE,
    )
    verdict = decide_run_outcome(route_a=attempt_success(), route_b=unsupported)
    assert verdict.outcome is Outcome.UNSUPPORTED


def test_success_plus_inconclusive_route_is_inconclusive() -> None:
    verdict = decide_run_outcome(
        route_a=attempt_success(), route_b=attempt_inconclusive()
    )
    assert verdict.outcome is Outcome.INCONCLUSIVE


def test_run_cancelled_if_either_route_cancelled() -> None:
    cancelled = decide_attempt_outcome(obligations=[passed()], cancelled=True)
    verdict = decide_run_outcome(route_a=attempt_success(), route_b=cancelled)
    assert verdict.outcome is Outcome.CANCELLED


def test_run_error_dominates_rejection() -> None:
    errored = decide_attempt_outcome(
        obligations=[passed()], error_reason=ReasonCode.PROVIDER_FAILED
    )
    verdict = decide_run_outcome(route_a=attempt_rejected(), route_b=errored)
    assert verdict.outcome is Outcome.ERROR


# ---------------------------------------------------------------------------
# Label invariants: the six outcomes are the whole vocabulary
# ---------------------------------------------------------------------------


def test_outcome_vocabulary_is_exactly_six_labels() -> None:
    assert {outcome.value for outcome in Outcome} == {
        "success_within_scope",
        "unsupported",
        "rejected",
        "inconclusive",
        "error",
        "cancelled",
    }


def test_non_success_outcomes_never_equal_success() -> None:
    assert Outcome.SUCCESS_WITHIN_SCOPE not in NON_SUCCESS_OUTCOMES
    assert len(NON_SUCCESS_OUTCOMES) == len(Outcome) - 1


@pytest.mark.parametrize(
    "obligation, expected",
    [
        (failed("regression"), Outcome.REJECTED),
        (not_run("symbolic"), Outcome.INCONCLUSIVE),
        (Obligation("symbolic", True, CheckResult.UNKNOWN), Outcome.INCONCLUSIVE),
        (Obligation("symbolic", True, CheckResult.TIMED_OUT), Outcome.INCONCLUSIVE),
    ],
)
def test_each_bad_required_result_blocks_success(
    obligation: Obligation, expected: Outcome
) -> None:
    verdict = success(obligations=[passed("structure"), obligation])
    assert verdict.outcome is expected
    assert verdict.outcome is not Outcome.SUCCESS_WITHIN_SCOPE
