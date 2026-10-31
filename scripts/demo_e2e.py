#!/usr/bin/env python3
"""End-to-end synthetic controller demo for BinaryRepair.

This script drives the REAL controller path in-process:

    app/controller_api.py   (HTTP routes, contract approval, run start)
    app/services/jobs.py    (SQLite job store, idempotent run start)
    app/services/worker.py  (result-bundle consumer)
    app/services/orchestration.py
    app/domain/acceptance.py (the only deterministic outcome engine)

It uses the public CWE-121 fixture ELF bytes
(``fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf``) and the
reviewed contract body shipped next to it (``contract.json``).

REAL in this demo
    * the fixture ELF bytes and their sha256 (computed, never executed);
    * job creation, contract approval, run start and idempotency checks;
    * SQLite persistence (jobs / artifacts / contracts / runs / attempts /
      obligations rows), read back through the API and directly from the file;
    * every outcome below is produced by ``app/domain/acceptance.py``.

SYNTHETIC in this demo
    * no candidate binary is built and no binary is executed anywhere;
    * the V1-V5 check results come from a clearly labelled synthetic result
      bundle written by this script (``synthetic: true`` in the bundle). The
      first scenario reports the unimplemented checks as ``not_run``; the second
      scenario reports one synthetic ``failed`` control so rejection precedence
      can be observed. Neither is evidence about a repaired binary.

The host never executes the uploaded or repaired binary. This script only reads
its bytes, hashes them, and hands them to the controller API.

Run:
    python scripts/demo_e2e.py
    python scripts/demo_e2e.py --data-dir _scratch/runtime_verification/e2e_manual

Exit code 0 only when every assertion in this file passes; 1 on any unexpected
error (the traceback is printed). The default data directory is a fresh,
timestamped directory under ``_scratch/runtime_verification/`` because the DSH
sandbox denies new directories directly under ``var/``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE = (
    REPO_ROOT
    / "fixtures"
    / "public"
    / "CWE121_src_char_declare_cpy_51b"
    / "original.elf"
)
FIXTURE_CONTRACT = FIXTURE.with_name("contract.json")

SYNTHETIC_LABEL = (
    "SYNTHETIC result bundle written by scripts/demo_e2e.py: no candidate "
    "binary was built and no uploaded or repaired binary was executed."
)

VALID_TESTS = [
    "valid case 1: 9-byte C string, expect exit 0",
    "valid case 2: 49-byte C string, expect exit 0",
    "invalid case 1: 60-byte C string, expect the exact stderr line and exit 1",
]

JOB_DATA = {
    "defect": (
        "CWE-121: strcpy into a 50-byte stack buffer in badSink does not "
        "check the input length (public fixture, not executed here)"
    ),
    "valid_tests": "\n".join(VALID_TESTS),
    "environment": "Ubuntu x86-64 guest (guest execution NOT available on this host)",
    "valid_domain": "C string inputs of length 0..49 bytes",
    "forbidden": "write past dest[50] for inputs of length 50 or more",
    "invalid_outcome": "exact stderr line and exit code 1",
    "assumptions": "Linux x86-64 non-PIE guest\nStack protector disabled",
}


class DemoCheckError(RuntimeError):
    """A measured expectation of this demo did not hold."""


def check(condition: Any, message: str) -> None:
    if not condition:
        raise DemoCheckError(message)


def expect(resp: Any, status: int, label: str) -> Any:
    check(
        resp.status_code == status,
        f"{label}: expected HTTP {status}, observed {resp.status_code}: {resp.text[:400]!r}",
    )
    return resp


def sha_json(payload: Any) -> str:
    """Reproduce app.controller_api._sha_json (manifest hash of the test list)."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def print_json_section(title: str, payload: Any) -> None:
    print(title)
    print(json.dumps(payload, indent=2, sort_keys=True))
    print()


def bundle_path(data_dir: Path, job_id: str, run_id: str, attempt_id: str) -> Path:
    return (
        Path(data_dir)
        / "jobs"
        / job_id
        / "runs"
        / run_id
        / "attempts"
        / f"{attempt_id}.json"
    )


def write_synthetic_bundle(
    *,
    data_dir: Path,
    job_id: str,
    run_id: str,
    attempt_id: str,
    route: str,
    candidate_sha256: str,
    results: dict[str, str],
    details: dict[str, str],
) -> Path:
    path = bundle_path(data_dir, job_id, run_id, attempt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "synthetic": True,
        "label": SYNTHETIC_LABEL,
        "route": route,
        "candidate_sha256": candidate_sha256,
        "results": results,
        "evidence": {},
        "details": details,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def drive_scenario(
    client: Any,
    *,
    scenario: str,
    fixture_bytes: bytes,
    fixture_sha256: str,
    contract_body: dict[str, Any],
    data_dir: Path,
    results: dict[str, str],
    gate_details: dict[str, str],
    expected_outcome: str,
    expected_reason: str,
    expected_failing_kind: str,
) -> dict[str, Any]:
    """Create job -> 409 before approval -> approve -> start -> bundles -> worker."""

    from app.db.connection import connect
    from app.domain.contracts import contract_sha256
    from app.services import worker

    print(f"---- scenario {scenario}: {expected_outcome} expected ----")

    # 1. Create the job from the real fixture bytes through the real HTTP API.
    files = {"binary": ("original.elf", fixture_bytes, "application/octet-stream")}
    created = expect(
        client.post("/api/jobs", data=JOB_DATA, files=files), 201, "POST /api/jobs"
    ).json()
    job_id = created["id"]
    check(
        created["original_sha256"] == fixture_sha256,
        f"stored original hash {created['original_sha256']} != fixture sha256 {fixture_sha256}",
    )
    print(f"POST /api/jobs -> 201 id={job_id} phase={created['phase']} sha256={created['original_sha256']}")

    snapshot = expect(client.get(f"/api/jobs/{job_id}"), 200, "GET /api/jobs/{id}").json()
    check(snapshot["contract"]["approved"] is False, "a fresh job must not have an approved contract")
    check(snapshot["run"] is None, "a fresh job must not have a run")

    manifest_sha256 = sha_json(VALID_TESTS)

    # 2. Starting a run before approval must be refused (real 409 guard).
    early = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 1,
            "manifest_sha256": manifest_sha256,
            "idempotency_key": f"runtime-verifier-{scenario}-early",
        },
    )
    expect(early, 409, "POST /api/jobs/{id}/runs before approval")
    print(f"POST /api/jobs/{{id}}/runs before approval -> 409 ({early.json()['detail']})")

    # 3. Approve the reviewed contract (real contract body of the fixture).
    approval = expect(
        client.post(
            f"/api/jobs/{job_id}/approve",
            json={
                "contract": contract_body,
                "contract_revision": 1,
                "manifest_sha256": manifest_sha256,
                "reviewer_label": "runtime-verifier synthetic demo",
            },
        ),
        200,
        "POST /api/jobs/{id}/approve",
    ).json()
    expected_scope_sha = contract_sha256(contract_body)
    check(
        approval["sha256"] == expected_scope_sha,
        f"approved scope hash {approval['sha256']} != canonical contract hash {expected_scope_sha}",
    )
    check(approval["approved"] is True, "approval response must set approved=true")
    print(
        f"POST /api/jobs/{{id}}/approve -> 200 revision={approval['contract_revision']} "
        f"scope_sha256={approval['sha256']}"
    )

    snapshot = expect(client.get(f"/api/jobs/{job_id}"), 200, "GET /api/jobs/{id}").json()
    check(snapshot["contract"]["approved"] is True, "approved contract not reflected in snapshot")
    check(snapshot["contract"]["sha256"] == expected_scope_sha, "snapshot contract hash mismatch")

    # 4. Start the run (canonical route) and verify idempotency.
    start_body = {
        "contract_revision": 1,
        "manifest_sha256": manifest_sha256,
        "idempotency_key": f"runtime-verifier-{scenario}-run-1",
    }
    start = expect(
        client.post(f"/api/jobs/{job_id}/runs", json=start_body),
        202,
        "POST /api/jobs/{id}/runs",
    ).json()
    run_id = start["id"]
    check(start["created"] is True, "first run start must create the run")
    check(start["manifest_sha256"] == manifest_sha256, "run manifest hash does not match approval")
    attempts = start["attempts"]
    check(len(attempts) == 2, f"expected 2 pre-created attempts, observed {len(attempts)}")
    check(
        [attempt["route"] for attempt in attempts] == ["route_a", "route_b"],
        f"unexpected routes: {[attempt['route'] for attempt in attempts]}",
    )
    print(f"POST /api/jobs/{{id}}/runs -> 202 run={run_id} attempts={[a['id'] for a in attempts]}")

    repeat = client.post(f"/api/jobs/{job_id}/runs", json=start_body)
    expect(repeat, 200, "POST /api/jobs/{id}/runs (same idempotency key)")
    check(repeat.json()["id"] == run_id, "idempotent start returned a different run id")
    check(repeat.json()["created"] is False, "repeat start must not create a run")
    print(f"POST /api/jobs/{{id}}/runs (same idempotency key) -> 200 same run, created=false")

    conflicting = client.post(
        f"/api/jobs/{job_id}/runs",
        json={
            "contract_revision": 1,
            "manifest_sha256": "1" * 64,
            "idempotency_key": start_body["idempotency_key"],
        },
    )
    expect(conflicting, 409, "same idempotency key with a different manifest")
    print(f"POST /api/jobs/{{id}}/runs (same key, different manifest) -> 409")

    # 5. Write clearly-labelled SYNTHETIC bundles for both routes.
    candidate_sha256 = hashlib.sha256(
        f"SYNTHETIC-PLACEHOLDER-CANDIDATE-NOT-BUILT:{job_id}".encode("utf-8")
    ).hexdigest()
    for attempt in attempts:
        path = write_synthetic_bundle(
            data_dir=data_dir,
            job_id=job_id,
            run_id=run_id,
            attempt_id=attempt["id"],
            route=attempt["route"],
            candidate_sha256=candidate_sha256,
            results=results,
            details=gate_details,
        )
        print(f"wrote synthetic bundle for {attempt['route']}: {path.name}")

    # 6. Run the real worker once; capture its console output as evidence.
    worker_buffer = io.StringIO()
    with contextlib.redirect_stdout(worker_buffer):
        processed = worker.run_once(data_dir=data_dir)
    worker_output = worker_buffer.getvalue().rstrip()
    print("app.services.worker.run_once output:")
    for line in worker_output.splitlines():
        print(f"    {line}")
    check(processed == 2, f"worker processed {processed} attempts, expected 2")

    # 7. Read the verdicts back through the API.
    final = expect(client.get(f"/api/jobs/{job_id}"), 200, "GET /api/jobs/{id}").json()
    check(final["phase"] == "finished", f"job phase after worker is {final['phase']!r}, not finished")
    check(final["verdict"] is not None, "no run verdict was persisted for the job")
    check(
        final["verdict"]["outcome"] == expected_outcome,
        f"run outcome {final['verdict']['outcome']!r} != expected {expected_outcome!r}",
    )
    check(
        final["verdict"]["reason"] == expected_reason,
        f"run reason {final['verdict']['reason']!r} != expected {expected_reason!r}",
    )

    scenario_record: dict[str, Any] = {
        "scenario": scenario,
        "job_id": job_id,
        "run_id": run_id,
        "manifest_sha256": manifest_sha256,
        "scope_sha256": expected_scope_sha,
        "synthetic_candidate_sha256": candidate_sha256,
        "job": final,
        "routes": {},
        "attempt_evidence": {},
    }

    route_keys = {"route_a": "A", "route_b": "B"}
    for attempt in attempts:
        route_key = route_keys[attempt["route"]]
        route = final["routes"][route_key]
        check(
            route["outcome"] == expected_outcome,
            f"{attempt['route']} outcome {route['outcome']!r} != expected {expected_outcome!r}",
        )
        check(
            route["verification"] != "passed",
            f"{attempt['route']} verification was reported as passed for synthetic data",
        )
        obligation_map = {
            item["kind"]: item["result"] for item in route.get("obligations") or []
        }
        check(obligation_map, f"{attempt['route']} has no persisted obligations in the snapshot")
        check(
            obligation_map.get("identity") == "passed",
            f"{attempt['route']} V0 identity result is {obligation_map.get('identity')!r}",
        )
        for kind, result in obligation_map.items():
            if kind == "identity":
                continue
            check(
                result != "passed",
                f"{attempt['route']} synthetic gate {kind} must not be reported as passed",
            )
            if expected_outcome == "rejected":
                check(
                    "failed" in obligation_map.values(),
                    f"{attempt['route']} rejection scenario has no failed obligation",
                )
        check(
            expected_failing_kind in obligation_map,
            f"{attempt['route']} missing failing obligation kind {expected_failing_kind!r}",
        )
        check(
            obligation_map[expected_failing_kind] == "failed"
            if expected_outcome == "rejected"
            else obligation_map[expected_failing_kind] == "not_run",
            f"{attempt['route']} expected failing obligation kind mismatch",
        )

        evidence = expect(
            client.get(f"/api/attempts/{attempt['id']}/evidence"),
            200,
            "GET /api/attempts/{id}/evidence",
        ).json()
        check(evidence["outcome"] == expected_outcome, f"{attempt['route']} evidence outcome mismatch")
        scenario_record["routes"][attempt["route"]] = {
            "attempt_id": attempt["id"],
            "phase": route["phase"],
            "outcome": route["outcome"],
            "reason": route["outcome_reason"],
            "verification": route["verification"],
            "obligations": obligation_map,
        }
        scenario_record["attempt_evidence"][attempt["route"]] = {
            "outcome": evidence["outcome"],
            "reason": evidence["reason"],
            "failing_obligation": evidence["failing_obligation"],
            "endpoint_obligations": {
                item["kind"]: item["result"]
                for group in evidence["obligations"].values()
                for item in group
            },
            "note": (
                "The canonical /api/attempts/{id}/evidence endpoint serves the persisted "
                "per-attempt obligation rows (evidence: persisted) and returns an empty "
                "group with evidence: not_persisted when the worker has not written any."
            ),
        }

    # 8. Verify persistence directly: SQLite rows plus the stored original bytes.
    connection = connect(data_dir)
    try:
        run_row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        attempt_rows = connection.execute(
            "SELECT id, route, phase, outcome FROM attempts WHERE run_id = ? ORDER BY route",
            (run_id,),
        ).fetchall()
        obligation_count = connection.execute(
            "SELECT COUNT(*) FROM obligations WHERE attempt_id IN "
            "(SELECT id FROM attempts WHERE run_id = ?)",
            (run_id,),
        ).fetchone()[0]
        contract_row = connection.execute(
            "SELECT * FROM contracts WHERE job_id = ? AND revision = 1", (job_id,)
        ).fetchone()
    finally:
        connection.close()
    check(run_row is not None, "run row missing from SQLite")
    check(run_row["outcome"] == expected_outcome, "persisted run outcome mismatch")
    check(run_row["phase"] == "finished", "persisted run phase is not finished")
    check(len(attempt_rows) == 2, "expected 2 persisted attempt rows")
    check(
        all(row["outcome"] == expected_outcome for row in attempt_rows),
        "a persisted attempt outcome differs from the scenario outcome",
    )
    check(obligation_count == 12, f"expected 12 obligation rows (2x6), observed {obligation_count}")
    check(contract_row is not None, "approved contract row missing from SQLite")
    check(
        contract_row["sha256"] == expected_scope_sha,
        "persisted contract scope hash does not match the canonical contract hash",
    )

    original_path = data_dir / "jobs" / job_id / "original.bin"
    check(original_path.exists(), "stored original bytes are missing on disk")
    check(
        hashlib.sha256(original_path.read_bytes()).hexdigest() == fixture_sha256,
        "stored original bytes on disk do not hash to the fixture sha256",
    )

    artifacts = expect(
        client.get(f"/api/jobs/{job_id}/artifacts"), 200, "GET /api/jobs/{id}/artifacts"
    ).json()
    check(
        artifacts["A"][0]["sha256"] == fixture_sha256,
        "artifact listing does not report the fixture sha256",
    )

    obligations_endpoint = expect(
        client.get(f"/api/jobs/{job_id}/obligations"), 200, "GET /api/jobs/{id}/obligations"
    ).json()
    check(set(obligations_endpoint) == {"A", "B"}, "obligations endpoint lost a route")

    scenario_record["persistence"] = {
        "run_phase": run_row["phase"],
        "run_outcome": run_row["outcome"],
        "attempts": [dict(row) for row in attempt_rows],
        "obligation_rows": obligation_count,
        "contract_scope_sha256": contract_row["sha256"],
        "stored_original_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
    }
    print(
        f"persisted: run phase={run_row['phase']} outcome={run_row['outcome']} "
        f"attempts={len(attempt_rows)} obligation_rows={obligation_count}"
    )
    print(
        f"stored original bytes: {original_path.relative_to(data_dir).as_posix()} "
        f"sha256={scenario_record['persistence']['stored_original_sha256']}"
    )
    print()
    return scenario_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the real BinaryRepair controller with labelled synthetic attempts."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=(
            "fresh data directory for the SQLite job store; must be empty or absent. "
            "Defaults to a timestamped directory under _scratch/runtime_verification/."
        ),
    )
    args = parser.parse_args(argv)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    data_dir = args.data_dir or (
        REPO_ROOT / "_scratch" / "runtime_verification" / f"e2e_{timestamp}"
    )
    if not data_dir.is_absolute():
        data_dir = (REPO_ROOT / data_dir).resolve()
    if data_dir.exists():
        check(
            not any(data_dir.iterdir()),
            f"--data-dir {data_dir} already contains files; use a fresh directory",
        )
    data_dir.mkdir(parents=True, exist_ok=True)

    # The controller API reads BINARYREPAIR_DATA_DIR per request; set it before
    # importing the app so only this fresh data directory is written.
    os.environ["BINARYREPAIR_DATA_DIR"] = str(data_dir)

    from fastapi.testclient import TestClient

    from app.controller_api import app as controller_app

    print("== environment (measured) ==")
    print(f"python: {sys.version.split()[0]}")
    print(f"platform: {sys.platform}")
    print(f"repo root: {REPO_ROOT}")
    print(f"fresh data dir: {data_dir}")
    print(f"fixture: {FIXTURE.relative_to(REPO_ROOT).as_posix()}")
    print()

    check(FIXTURE.exists(), f"fixture not found: {FIXTURE}")
    check(FIXTURE_CONTRACT.exists(), f"fixture contract not found: {FIXTURE_CONTRACT}")
    fixture_bytes = FIXTURE.read_bytes()
    check(
        fixture_bytes[:4] == b"\x7fELF",
        "fixture does not start with the ELF magic; refusing to continue",
    )
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    print("== real input ==")
    print(f"fixture ELF bytes: {len(fixture_bytes)}")
    print(f"fixture sha256: {fixture_sha256}")
    print("ELF magic checked; the binary is NOT executed on this host.")
    print()

    contract_body = json.loads(FIXTURE_CONTRACT.read_text(encoding="utf-8"))

    # Host check middleware only allows 127.0.0.1/localhost Host headers.
    client = TestClient(controller_app, base_url="http://127.0.0.1")
    health = expect(client.get("/healthz"), 200, "GET /healthz")
    print(f"GET /healthz -> 200 {health.json()}")
    print()

    scenario_one = drive_scenario(
        client,
        scenario="honest-wiring",
        fixture_bytes=fixture_bytes,
        fixture_sha256=fixture_sha256,
        contract_body=contract_body,
        data_dir=data_dir,
        results={
            "V0": "passed",
            "V1": "not_run",
            "V2": "not_run",
            "V3": "not_run",
            "V4": "not_run",
            "V5": "not_run",
        },
        gate_details={
            "V0": (
                "SYNTHETIC bundle: the harness reports V0 passed because the stored "
                "original bytes hash to the approved input hash; no candidate exists."
            ),
            "V1": "SYNTHETIC: not executed (no guest, no candidate). Reported not_run on purpose.",
            "V2": "SYNTHETIC: not executed (no guest, no candidate). Reported not_run on purpose.",
            "V3": "SYNTHETIC: not executed (no guest, no candidate). Reported not_run on purpose.",
            "V4": "SYNTHETIC: not executed (no symbolic engine, no candidate). Reported not_run on purpose.",
            "V5": "SYNTHETIC: not executed (no reproducibility replay). Reported not_run on purpose.",
        },
        expected_outcome="inconclusive",
        expected_reason="required_check_not_run",
        expected_failing_kind="structure",
    )

    scenario_two = drive_scenario(
        client,
        scenario="failed-check-control",
        fixture_bytes=fixture_bytes,
        fixture_sha256=fixture_sha256,
        contract_body=contract_body,
        data_dir=data_dir,
        results={
            "V0": "passed",
            "V1": "not_run",
            "V2": "not_run",
            "V3": "failed",
            "V4": "not_run",
            "V5": "not_run",
        },
        gate_details={
            "V0": (
                "SYNTHETIC bundle: the harness reports V0 passed because the stored "
                "original bytes hash to the approved input hash; no candidate exists."
            ),
            "V1": "SYNTHETIC: not executed. Reported not_run on purpose.",
            "V2": "SYNTHETIC: not executed. Reported not_run on purpose.",
            "V3": (
                "SYNTHETIC control: a required check is reported failed on purpose so the "
                "rejection precedence of the acceptance engine can be observed. No binary ran."
            ),
            "V4": "SYNTHETIC: not executed. Reported not_run on purpose.",
            "V5": "SYNTHETIC: not executed. Reported not_run on purpose.",
        },
        expected_outcome="rejected",
        expected_reason="regression_failed",
        expected_failing_kind="behaviour",
    )

    print("== final verdicts (produced by app/domain/acceptance.py) ==")
    for record in (scenario_one, scenario_two):
        verdict = record["job"]["verdict"]
        print(f"scenario {record['scenario']}:")
        print(f"  job phase: {record['job']['phase']}")
        print(f"  run outcome: {verdict['outcome']}")
        print(f"  reason: {verdict['reason']}")
        print(f"  failing obligation: {verdict['failing_obligation']}")
        print(f"  detail: {verdict['detail']}")
        for route_name, route in record["routes"].items():
            print(
                f"  {route_name}: outcome={route['outcome']} reason={route['reason']!r} "
                f"verification={route['verification']}"
            )
            print(f"    obligations={route['obligations']}")
        print()

    print("== what is real vs synthetic ==")
    print("REAL: fixture ELF bytes and sha256; HTTP controller routes; contract approval;")
    print("      idempotent run start; SQLite rows (jobs/contracts/runs/attempts/obligations);")
    print("      worker/orchestration wiring; acceptance verdicts.")
    print("SYNTHETIC: every V1-V5 result and the V0 result are supplied by this script's")
    print("      labelled bundle; no candidate binary exists and nothing was executed.")
    print("NOT RUN: guest execution, Route A/B candidate generation, V1-V5 real checks,")
    print("      symbolic (V4) verification, and V5 independent replay.")
    print()
    print(
        "DEMO OK: synthetic attempt data exercised the real controller, database, worker "
        "and deterministic acceptance path end to end."
    )
    print(f"Evidence directory for this run: {data_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DemoCheckError as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception:  # noqa: BLE001 - any unexpected error must exit non-zero
        print("DEMO FAILED with an unexpected error:", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
