"""Adapter test: M3 Route A result bundle -> worker per-attempt bundle.

The mapping follows M4's CheckResult vocabulary:
pass->passed, fail->failed, inconclusive->unknown, unsupported->not_run.
No outcome field is produced.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.routea_bundle_to_attempt import convert

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "interfaces"
    / "route-a"
    / "result-bundle-DRYRUN.json"
)


def test_dry_run_bundle_maps_to_worker_contract_without_outcome():
    bundle = json.loads(FIXTURE.read_text(encoding="utf-8"))
    attempt = convert(bundle)

    assert attempt["route"] == "route_a"
    assert attempt["candidate_sha256"] == bundle["identity"]["candidate_sha256"]
    assert attempt["candidate_sha256"] == "c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82"

    results = attempt["results"]
    assert results["V0"] == "passed"
    # M4: inconclusive is an Outcome, not a per-check result -> map to unknown.
    assert results["V1"] == "unknown"
    assert results["V3"] == "unknown"
    for gate in ("V2", "V4", "V5"):
        assert results[gate] == "not_run"

    # The adapter maps raw gate statuses only; it must not invent an outcome.
    assert "outcome" not in attempt
    assert "outcome" not in results
    assert "V0" in attempt["evidence"]
    assert "original.elf" in attempt["evidence"]["V0"]
    assert "formal reverted-snapshot run missing" in attempt["details"]["V3"]


def test_unsupported_maps_to_not_run_and_pass_spelling_is_explicit():
    bundle = {
        "route": "A",
        "identity": {"candidate_sha256": "a" * 64},
        "gates": {
            "V0": {"status": "pass"},
            "V1": {"status": "unsupported"},
            "V2": {"status": "inconclusive"},
            "V3": {"status": "fail"},
            "V4": {"status": "not_run"},
            "V5": {"status": "timed_out"},
        },
    }
    attempt = convert(bundle)
    assert attempt["results"] == {
        "V0": "passed",
        "V1": "not_run",
        "V2": "unknown",
        "V3": "failed",
        "V4": "not_run",
        "V5": "timed_out",
    }
