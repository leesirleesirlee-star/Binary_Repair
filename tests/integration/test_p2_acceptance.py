from __future__ import annotations

from tools import p2_acceptance


def _snapshot(*, verdict: str = "inconclusive", outcome: str = "inconclusive",
              result: str = "passed", demo: bool = False) -> dict:
    kinds = ("identity", "structure", "interface", "behaviour", "symbolic", "reproducibility")
    return {
        "phase": "finished",
        "demo": demo,
        "verdict": verdict,
        "routes": {
            key: {
                "outcome": outcome,
                "verdict": outcome,
                "obligations": [{"kind": kind, "result": result} for kind in kinds],
            }
            for key in ("A", "B")
        },
    }


def test_rejects_success_with_all_not_run() -> None:
    errors = p2_acceptance.validate_snapshot(
        _snapshot(verdict="success_within_scope", outcome="success_within_scope", result="not_run")
    )
    assert any("all obligations not_run" in error for error in errors)


def test_rejects_invalid_outcome_enum() -> None:
    errors = p2_acceptance.validate_snapshot(
        _snapshot(verdict="inconclusive", outcome="looks_good")
    )
    assert any("not a valid outcome" in error for error in errors)


def test_synthetic_mode_requires_explicit_label() -> None:
    assert p2_acceptance.main([
        "--job-id", "j", "--data-dir", "/tmp/does-not-matter"
    ]) == 2


def test_synthetic_flag_cannot_use_live_controller() -> None:
    assert p2_acceptance.main(["--job-id", "j", "--synthetic"]) == 2


def test_synthetic_offline_finished_snapshot_still_passes(monkeypatch) -> None:
    monkeypatch.setattr(p2_acceptance, "_snapshot_from_data",
                        lambda data_dir, job_id: _snapshot())
    assert p2_acceptance.main([
        "--job-id", "j", "--data-dir", "/tmp/does-not-matter", "--synthetic"
    ]) == 0
