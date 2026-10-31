"""Verdict rendering tests: the GUI only renders the snapshot verdict.

The GUI must never aggregate route outcomes into a conclusion; M4's acceptance
engine is the only place that decides an outcome.
"""

from __future__ import annotations

from app.status import display_verdict


def _snapshot(**overrides):
    base = {
        "phase": "finished",
        "cancelled": False,
        "verdict": None,
        "routes": {
            "A": {"outcome": "success_within_scope"},
            "B": {"outcome": "success_within_scope"},
        },
    }
    base.update(overrides)
    return base


def test_no_verdict_means_no_computed_success():
    result = display_verdict(_snapshot())
    assert result["key"] == "none"
    assert "尚无 verdict" in result["label_zh"]
    # Even with both routes successful, the GUI must not invent a success.
    assert "成功" not in result["label_zh"]


def test_renders_controller_verdict_outcome():
    result = display_verdict(
        _snapshot(
            verdict={
                "outcome": "rejected",
                "reason": "regression_failed",
                "failing_obligation": "behaviour",
                "detail": "required obligation failed",
            }
        )
    )
    assert result["key"] == "rejected"
    assert result["label_zh"] == "被拒"


def test_cancelled_state_is_rendered_directly():
    result = display_verdict(_snapshot(phase="screening", cancelled=True))
    assert result["key"] == "cancelled"
    assert result["label_zh"] == "已取消"


def test_unfinished_job_has_no_verdict_label():
    assert display_verdict(_snapshot(phase="analyzing"))["label_zh"] == "—"
