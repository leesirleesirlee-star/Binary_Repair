"""End-to-end smoke: controller API -> worker -> snapshot.

This is deliberately an inconclusive job (all V0-V5 are ``not_run``). It proves
the wiring, not a successful repair; a real MVP job needs real Route A/B bundles.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.controller_api import app
from app.services import worker

FORM = {
    "defect": "smoke: wiring only",
    "valid_tests": "valid: hello",
    "environment": "test",
    "valid_domain": "any",
    "forbidden": "none",
    "invalid_outcome": "none",
    "assumptions": "test",
}


def test_controller_worker_snapshot_smoke(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "var"
    monkeypatch.setenv("BINARYREPAIR_DATA_DIR", str(data_dir))
    client = TestClient(app, base_url="http://127.0.0.1")

    created = client.post(
        "/api/jobs",
        data=FORM,
        files={"binary": ("sample.elf", b"\x7fELF smoke", "application/octet-stream")},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    contract = client.get(f"/api/jobs/{job_id}").json()["contract"]
    approved = client.post(
        f"/api/jobs/{job_id}/approve",
        data={"expected_sha256": contract["sha256"], "reviewer_label": "smoke"},
    )
    assert approved.status_code == 200, approved.text

    started = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 1,
            "manifest_sha256": "1" * 64,
            "idempotency_key": "smoke-1",
        },
    )
    assert started.status_code == 202, started.text
    run = started.json()

    for attempt in run["attempts"]:
        path = (
            data_dir
            / "jobs"
            / job_id
            / "runs"
            / run["id"]
            / "attempts"
            / f"{attempt['id']}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "route": attempt["route"],
                    "candidate_sha256": created.json()["original_sha256"],
                    "results": {f"V{i}": "not_run" for i in range(6)},
                    "details": {"V0": "smoke wiring only"},
                }
            ),
            encoding="utf-8",
        )

    processed = worker.run_once(data_dir=data_dir)
    assert processed == 2

    snapshot = client.get(f"/api/jobs/{job_id}")
    assert snapshot.status_code == 200, snapshot.text
    payload = snapshot.json()
    assert payload["phase"] == "finished"
    assert payload["verdict"]["outcome"] == "inconclusive"
    assert payload["run"]["id"] == run["id"]
    for key in ("A", "B"):
        route = payload["routes"][key]
        assert route["outcome"] == "inconclusive"
        assert len(route["obligations"]) == 6
        assert route["verdict"]["outcome"] == "inconclusive"
