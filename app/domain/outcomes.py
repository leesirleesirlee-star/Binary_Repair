"""Outcome and check-result semantics for BinaryRepair.

This module is the single source of truth for the labels the product uses to
describe a repair attempt. It performs no I/O and imports no framework.

Rules encoded here, from the PRD "Result Semantics" and Tech Design sections 6
and 9:

* phase and outcome are separate axes; a phase is where work is, an outcome is
  what the evidence supports;
* a check that was not run is ``not_run``, never ``passed``;
* unknown, timed out, or not-run required evidence cannot produce success;
* cancellation stays distinct from error;
* both routes must individually succeed for the MVP - one is not enough.

The API and the GUI only *render* the verdicts produced here. They must never
compute their own success label.
"""

from __future__ import annotations

from enum import Enum


class CheckResult(str, Enum):
    """Result of one verification obligation.

    ``PASSED`` is the only value that can contribute to success. Every other
    value prevents an overall successful outcome, even when the check merely
    could not be completed.
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"
    UNKNOWN = "unknown"
    TIMED_OUT = "timed_out"


class Phase(str, Enum):
    """Lifecycle phase of a job, run, or attempt.

    Kept separate from :class:`Outcome` so that "still working" and "finished
    successfully" cannot be confused. Route B adds ``ROUNDTRIP``.
    """

    DRAFT = "draft"
    SCREENING = "screening"
    AWAITING_REVIEW = "awaiting_review"
    QUEUED = "queued"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    APPLYING = "applying"
    VALIDATING = "validating"
    ROUNDTRIP = "roundtrip"
    FINISHED = "finished"


class Outcome(str, Enum):
    """Terminal label for a job, run, or attempt.

    Only these six values exist. ``SUCCESS_WITHIN_SCOPE`` is deliberately the
    longest name: it is a claim about a stated scope, not about the program.
    """

    SUCCESS_WITHIN_SCOPE = "success_within_scope"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    CANCELLED = "cancelled"


class ReasonCode(str, Enum):
    """Stable machine-readable reason attached to a non-success outcome.

    Reason codes are persisted and exported, so they are part of the evidence
    contract: add new values, do not rename existing ones.
    """

    # --- rejected: a candidate was produced and a required check failed it ---
    COUNTEREXAMPLE = "counterexample"
    REGRESSION_FAILED = "regression_failed"
    COMPATIBILITY_FAILED = "compatibility_failed"
    SECURITY_CONTRACT_VIOLATED = "security_contract_violated"

    # --- inconclusive: the evidence cannot establish a required property ---
    REQUIRED_CHECK_NOT_RUN = "required_check_not_run"
    SOLVER_UNKNOWN = "solver_unknown"
    CHECK_TIMED_OUT = "check_timed_out"
    INCOMPLETE_EXPLORATION = "incomplete_exploration"
    HASH_MISMATCH = "hash_mismatch"
    EMPTY_SCOPE = "empty_scope"
    NO_REQUIRED_EVIDENCE = "no_required_evidence"

    # --- unsupported: the required capability is outside the supported profile ---
    UNSUPPORTED_PLACEMENT = "unsupported_placement"
    UNSUPPORTED_INTERFACE = "unsupported_interface"
    UNSUPPORTED_PROFILE = "unsupported_profile"

    # --- error: a tool, provider, or system failed ---
    TOOL_FAILED = "tool_failed"
    PROVIDER_FAILED = "provider_failed"
    TERMINATION_UNCONFIRMED = "termination_unconfirmed"


#: Outcomes that can never be displayed as success. Exposed so the UI tests can
#: assert that no such label is ever present on a successful-only screen.
NON_SUCCESS_OUTCOMES: frozenset[Outcome] = frozenset(
    {
        Outcome.UNSUPPORTED,
        Outcome.REJECTED,
        Outcome.INCONCLUSIVE,
        Outcome.ERROR,
        Outcome.CANCELLED,
    }
)
