"""Rendering tests for the controller's run verdict and per-route obligations."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main


SNAPSHOT = {
    "id": "job-verdict",
    "demo": False,
    "filename": "sample.bin",
    "original_sha256": "a" * 64,
    "defect": "strcpy in stack_buf does not check length",
    "valid_tests": [],
    "environment": "Ubuntu x86-64 VM (demo)",
    "created_at": "2026-10-22T00:00:00+00:00",
    "phase": "finished",
    "cancelled": False,
    "contract": {
        "revision": 1,
        "sha256": "b" * 64,
        "approved": True,
        "valid_domain": "Input length 0-64 bytes",
        "forbidden": "Out-of-bounds write",
        "invalid_outcome": "Return error code 1",
        "assumptions": ["Single process"],
    },
    "run": {
        "id": "run-verdict",
        "phase": "finished",
        "manifest_sha256": "c" * 64,
        "reviewed_scope_sha256": "d" * 64,
    },
    "verdict": {
        "outcome": "rejected",
        "reason": "valid_input_regression_failed",
        "failing_obligation": "V3",
        "detail": "valid input case 3 return value 7 != 3",
    },
    "routes": {
        "A": {
            "name": "Route A",
            "phase": "finished",
            "outcome": "rejected",
            "outcome_reason": "valid input regression failed",
            "stages": [{"name": "screening", "state": "done"}],
            "tests": {"passed": 6, "total": 8},
            "verification": "failed",
            "compatibility": "passed",
            "changed_regions": [],
            "manual_interventions": 1,
            "ai_usage": "unavailable",
            "elapsed_s": 1.0,
            "verdict": {
                "outcome": "rejected",
                "reason": "valid_input_regression_failed",
                "failing_obligation": "V3",
                "detail": "case 3 failed",
            },
            "obligations": [
                {
                    "kind": "identity",
                    "required": True,
                    "result": "passed",
                    "detail": "V0 identity: passed",
                    "evidence_artifact_id": None,
                },
                {
                    "kind": "behaviour",
                    "required": True,
                    "result": "failed",
                    "detail": "return value mismatch",
                    "evidence_artifact_id": "artifact-v3",
                },
            ],
        },
        "B": {
            "name": "Route B",
            "phase": "finished",
            "outcome": "inconclusive",
            "outcome_reason": "symbolic budget exhausted",
            "stages": [{"name": "screening", "state": "done"}],
            "tests": {"passed": 8, "total": 8},
            "verification": "inconclusive",
            "compatibility": "passed",
            "changed_regions": [],
            "manual_interventions": 0,
            "ai_usage": "unavailable",
            "elapsed_s": 2.0,
            "verdict": {
                "outcome": "inconclusive",
                "reason": "symbolic_budget_exhausted",
                "failing_obligation": "V4",
                "detail": "budget exhausted",
            },
            "obligations": [
                {
                    "kind": "symbolic",
                    "required": True,
                    "result": "inconclusive",
                    "detail": "budget exhausted",
                    "evidence_artifact_id": None,
                }
            ],
        },
    },
}


class _FakeBackend:
    mode = "mock"
    supports_start = True

    def get_snapshot(self, job_id: str):
        return SNAPSHOT

    def get_visuals(self, job_id: str):
        return {"available": False, "note": "test"}

    def get_obligations(self, job_id: str):
        return {"A": [], "B": []}

    def get_artifacts(self, job_id: str):
        return {"A": [], "B": []}


def _client() -> TestClient:
    client = TestClient(main.app, base_url="http://127.0.0.1")
    assert client.get("/?token=" + main.SESSION_TOKEN).status_code == 200
    return client


def test_comparison_renders_run_verdict_and_obligations(monkeypatch):
    monkeypatch.setattr(main, "BACKEND", _FakeBackend())
    page = _client().get("/jobs/job-verdict")
    assert page.status_code == 200
    assert "run-verdict" in page.text
    assert "失败义务 V3" in page.text
    assert "V3" in page.text and "failed" in page.text
    assert "verdict" in page.text.lower()


def test_evidence_prefers_snapshot_obligations(monkeypatch):
    monkeypatch.setattr(main, "BACKEND", _FakeBackend())
    page = _client().get("/jobs/job-verdict/evidence")
    assert page.status_code == 200
    assert "V3" in page.text
    assert "return value mismatch" in page.text
