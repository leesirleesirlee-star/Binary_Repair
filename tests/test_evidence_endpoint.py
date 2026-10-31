"""Honesty tests for the controller evidence endpoints.

``GET /api/attempts/{attempt_id}/evidence`` and
``GET /api/jobs/{job_id}/obligations`` must serve the V0-V5 obligation rows that
the worker actually persisted. They must never serve a static placeholder or
invent a ``passed`` result for a check that did not run (AGENTS.md: "missing is
not passing").

These tests drive the real FastAPI app over ``TestClient`` and seed state
through the same service layer the production worker uses
(``app.services.worker.run_once`` and ``app.services.jobs``). The result bundles
are clearly labelled SYNTHETIC: no candidate binary is built and no binary is
executed anywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.controller_api import app
from app.services import jobs, worker

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    REPO_ROOT
    / "fixtures"
    / "public"
    / "CWE121_src_char_declare_cpy_51b"
    / "original.elf"
)
FIXTURE_CONTRACT = FIXTURE.with_name("contract.json")

MANIFEST_SHA256 = "1" * 64
SYNTHETIC_LABEL = (
    "SYNTHETIC result bundle written by tests/test_evidence_endpoint.py: no "
    "candidate binary was built and no uploaded or repaired binary was executed."
)
MIXED_RESULTS = {
    "V0": "passed",
    "V1": "not_run",
    "V2": "not_run",
    "V3": "not_run",
    "V4": "not_run",
    "V5": "not_run",
}
ALL_NOT_RUN_RESULTS = {gate: "not_run" for gate in MIXED_RESULTS}
FAILED_CONTROL_RESULTS = {**MIXED_RESULTS, "V3": "failed"}

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (test: execution disabled)",
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Point the controller at a fresh data directory for this test."""

    monkeypatch.setenv("BINARYREPAIR_DATA_DIR", str(tmp_path / "var"))
    return TestClient(app, base_url="http://127.0.0.1")


def _data_dir() -> Path:
    return Path(os.environ["BINARYREPAIR_DATA_DIR"])


def _fixture_bytes() -> bytes:
    assert FIXTURE.exists(), f"public fixture is missing: {FIXTURE}"
    payload = FIXTURE.read_bytes()
    assert payload[:4] == b"\x7fELF", "fixture does not start with the ELF magic"
    return payload


def _create_approved_job(client: TestClient) -> str:
    created = client.post(
        "/api/jobs",
        data=FORM,
        files={
            "binary": ("original.elf", _fixture_bytes(), "application/octet-stream")
        },
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    contract = json.loads(FIXTURE_CONTRACT.read_text(encoding="utf-8"))
    approved = client.post(
        f"/api/jobs/{job_id}/approve",
        json={
            "contract": contract,
            "contract_revision": 1,
            "manifest_sha256": MANIFEST_SHA256,
            "reviewer_label": "evidence-endpoint test",
        },
    )
    assert approved.status_code == 200, approved.text
    return job_id


def _start_run(client: TestClient, job_id: str, key: str) -> dict[str, Any]:
    started = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 1,
            "manifest_sha256": MANIFEST_SHA256,
            "idempotency_key": key,
        },
    )
    assert started.status_code == 202, started.text
    payload = started.json()
    assert len(payload["attempts"]) == 2
    return payload


def _bundle_path(data_dir: Path, job_id: str, run_id: str, attempt_id: str) -> Path:
    return (
        data_dir
        / "jobs"
        / job_id
        / "runs"
        / run_id
        / "attempts"
        / f"{attempt_id}.json"
    )


def _write_bundle(
    *,
    data_dir: Path,
    job_id: str,
    run_id: str,
    attempt: dict[str, Any],
    results: dict[str, str],
) -> None:
    path = _bundle_path(data_dir, job_id, run_id, attempt["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "synthetic": True,
        "label": SYNTHETIC_LABEL,
        "route": attempt["route"],
        "candidate_sha256": hashlib.sha256(
            f"SYNTHETIC-TEST-CANDIDATE:{job_id}:{attempt['id']}".encode("utf-8")
        ).hexdigest(),
        "results": results,
        "evidence": {},
        "details": {
            gate: f"SYNTHETIC test bundle: {gate} reported {result}; no binary ran."
            for gate, result in results.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _seed_finished_run(
    client: TestClient, results: dict[str, str], *, key: str
) -> tuple[str, dict[str, Any]]:
    """Create a job, start a run, seed synthetic bundles, run the real worker."""

    job_id = _create_approved_job(client)
    run = _start_run(client, job_id, key)
    data_dir = _data_dir()
    for attempt in run["attempts"]:
        _write_bundle(
            data_dir=data_dir,
            job_id=job_id,
            run_id=run["id"],
            attempt=attempt,
            results=results,
        )
    processed = worker.run_once(data_dir=data_dir)
    assert processed == 2, f"worker processed {processed} attempts, expected 2"
    return job_id, run


def _route_attempt(run: dict[str, Any], route: str) -> dict[str, Any]:
    return next(item for item in run["attempts"] if item["route"] == route)


def test_evidence_serves_persisted_rows_verbatim(client: TestClient) -> None:
    job_id, run = _seed_finished_run(client, MIXED_RESULTS, key="evidence-verbatim")
    data_dir = _data_dir()

    for attempt in run["attempts"]:
        response = client.get(f"/api/attempts/{attempt['id']}/evidence")
        assert response.status_code == 200, response.text
        payload = response.json()

        expected_rows = jobs.list_obligations(attempt["id"], data_dir=data_dir)
        assert len(expected_rows) == 6
        assert payload["evidence"] == "persisted"
        assert payload["attempt_id"] == attempt["id"]
        assert payload["run_id"] == run["id"]
        assert payload["route"] == attempt["route"]

        own_key = "A" if attempt["route"] == "route_a" else "B"
        other_key = "B" if own_key == "A" else "A"
        # The persisted rows are returned as stored, not re-derived.
        assert payload["obligations"][own_key] == expected_rows
        assert payload["obligations"][other_key] == []
        assert {row["kind"] for row in payload["obligations"][own_key]} == {
            "identity",
            "structure",
            "interface",
            "behaviour",
            "symbolic",
            "reproducibility",
        }


def test_evidence_is_empty_when_nothing_was_persisted(client: TestClient) -> None:
    job_id = _create_approved_job(client)
    run = _start_run(client, job_id, "evidence-not-persisted")
    attempt = run["attempts"][0]

    response = client.get(f"/api/attempts/{attempt['id']}/evidence")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["evidence"] == "not_persisted"
    assert payload["obligations"] == {"A": [], "B": []}
    assert payload["outcome"] is None
    # Nothing is reported as passed when no evidence row exists.
    assert "passed" not in json.dumps(payload["obligations"])

    job_obligations = client.get(f"/api/jobs/{job_id}/obligations")
    assert job_obligations.status_code == 200, job_obligations.text
    groups = job_obligations.json()
    assert set(groups) == {"A", "B"}
    assert groups == {"A": [], "B": []}

    snapshot = client.get(f"/api/jobs/{job_id}").json()
    assert snapshot["routes"]["A"]["evidence"] == "not_persisted"
    assert snapshot["routes"]["A"]["obligations"] == []
    assert snapshot["routes"]["A"]["verification"] == "not_run"


def test_persisted_not_run_identity_is_not_reported_as_passed(
    client: TestClient,
) -> None:
    job_id, run = _seed_finished_run(client, ALL_NOT_RUN_RESULTS, key="all-not-run")
    attempt = _route_attempt(run, "route_a")

    payload = client.get(f"/api/attempts/{attempt['id']}/evidence").json()
    assert payload["evidence"] == "persisted"
    rows = payload["obligations"]["A"]
    assert [row["kind"] for row in rows]
    assert all(row["result"] == "not_run" for row in rows)
    assert "passed" not in json.dumps(rows)

    snapshot = client.get(f"/api/jobs/{job_id}").json()
    assert snapshot["routes"]["A"]["verification"] == "not_run"
    assert snapshot["routes"]["A"]["outcome"] == "inconclusive"


def test_mixed_results_use_inconclusive_not_unknown(client: TestClient) -> None:
    job_id, _run = _seed_finished_run(client, MIXED_RESULTS, key="mixed-vocabulary")

    snapshot = client.get(f"/api/jobs/{job_id}").json()
    # The acceptance engine decides the outcome; the API only renders it.
    assert snapshot["verdict"]["outcome"] == "inconclusive"
    for key in ("A", "B"):
        route = snapshot["routes"][key]
        assert route["outcome"] == "inconclusive"
        results = {row["result"] for row in route["obligations"]}
        assert results == {"passed", "not_run"}
        assert route["verification"] == "inconclusive"
        assert route["verification"] != "unknown"
        assert route["verification"] != "passed"
        assert route["evidence"] == "persisted"


def test_definite_failure_outranks_missing_evidence(client: TestClient) -> None:
    job_id, _run = _seed_finished_run(
        client, FAILED_CONTROL_RESULTS, key="failed-control"
    )

    snapshot = client.get(f"/api/jobs/{job_id}").json()
    for key in ("A", "B"):
        route = snapshot["routes"][key]
        assert route["outcome"] == "rejected"
        assert route["verification"] == "failed"


def test_job_obligations_reflect_latest_attempt_rows_per_route(
    client: TestClient,
) -> None:
    job_id, first_run = _seed_finished_run(client, MIXED_RESULTS, key="latest-rows-1")
    data_dir = _data_dir()

    # A second run for the same job: its attempt rows become the latest evidence.
    second_run = _start_run(client, job_id, "latest-rows-2")
    for attempt in second_run["attempts"]:
        _write_bundle(
            data_dir=data_dir,
            job_id=job_id,
            run_id=second_run["id"],
            attempt=attempt,
            results=ALL_NOT_RUN_RESULTS,
        )
    processed = worker.run_once(data_dir=data_dir)
    assert processed == 2

    groups = client.get(f"/api/jobs/{job_id}/obligations").json()
    assert set(groups) == {"A", "B"}
    for route, key in (("route_a", "A"), ("route_b", "B")):
        latest_attempt = _route_attempt(second_run, route)
        expected = jobs.list_obligations(latest_attempt["id"], data_dir=data_dir)
        assert expected, f"no rows persisted for the latest {route} attempt"
        assert groups[key] == expected
        assert all(row["result"] == "not_run" for row in groups[key])
        # The older run's rows (identity passed) must not leak into the answer.
        old_attempt = _route_attempt(first_run, route)
        old_rows = jobs.list_obligations(old_attempt["id"], data_dir=data_dir)
        assert old_rows != expected
        assert groups[key] != old_rows


def test_unknown_attempt_evidence_returns_404(client: TestClient) -> None:
    response = client.get("/api/attempts/does-not-exist/evidence")
    assert response.status_code == 404
    assert response.json()["detail"] == "Attempt not found."


def test_unknown_job_obligations_returns_404(client: TestClient) -> None:
    response = client.get("/api/jobs/does-not-exist/obligations")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found."
