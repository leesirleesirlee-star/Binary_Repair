"""Run consumer: turn queued runs into persisted attempt/run verdicts.

The worker does not execute binaries itself. It consumes one result bundle per
attempt, either already written by an external executor or produced by the
command in ``BINARYREPAIR_ATTEMPT_CMD``. It then:

1. writes the V0-V5 obligation rows;
2. calls ``orchestration.evaluate_attempt`` for each route;
3. calls ``orchestration.evaluate_run`` once both routes are terminal.

Result bundle path::

    <data-dir>/jobs/<job>/runs/<run>/attempts/<attempt_id>.json

Bundle schema::

    {"route": "route_a" | "route_b",
     "candidate_sha256": "<64 hex>",
     "results": {"V0": "passed", "V1": "not_run", ...},
     "evidence": {"V1": "<artifact id>", ...},
     "details": {"V1": "why", ...}}

A missing bundle is not an error: the run stays queued and the worker reports it.
No path in this module invents a ``passed`` result.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.db.connection import connect
from app.domain.acceptance import Verdict
from app.domain.outcomes import Outcome, ReasonCode
from app.services import jobs, obligations, orchestration

TERMINAL = {"finished", "cancelled"}


def _bundle_path(data_dir: str | Path, job_id: str, run_id: str, attempt_id: str) -> Path:
    return (
        Path(data_dir)
        / "jobs"
        / job_id
        / "runs"
        / run_id
        / "attempts"
        / f"{attempt_id}.json"
    )


def _set_phase(table: str, row_id: str, phase: str, *, data_dir: str | Path) -> None:
    if table not in {"jobs", "runs", "attempts"}:
        raise ValueError("invalid phase table")
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute(
                f"UPDATE {table} SET phase = ? WHERE id = ?",
                (phase, row_id),
            )
    finally:
        connection.close()


def _load_bundle(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read result bundle {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("result bundle must be an object")
    candidate = payload.get("candidate_sha256")
    if not isinstance(candidate, str) or len(candidate) != 64:
        raise ValueError("result bundle candidate_sha256 must be a 64-character hex digest")
    try:
        int(candidate, 16)
    except ValueError as exc:
        raise ValueError("result bundle candidate_sha256 must be hex") from exc
    results = payload.get("results")
    if not isinstance(results, dict):
        raise ValueError("result bundle results must be an object")
    return payload


def _run_executor(
    *,
    data_dir: str | Path,
    job_id: str,
    run_id: str,
    attempt_id: str,
    route: str,
    bundle: Path,
) -> bool:
    command = os.environ.get("BINARYREPAIR_ATTEMPT_CMD")
    if not command:
        return False
    env = os.environ.copy()
    env.update(
        {
            "BINARYREPAIR_DATA_DIR": str(data_dir),
            "BINARYREPAIR_JOB_ID": job_id,
            "BINARYREPAIR_RUN_ID": run_id,
            "BINARYREPAIR_ATTEMPT_ID": attempt_id,
            "BINARYREPAIR_ROUTE": route,
            "BINARYREPAIR_RESULT_PATH": str(bundle),
        }
    )
    subprocess.run([command], env=env, check=True)
    return bundle.exists()


def _reconstruct(attempt: jobs.AttemptRecord) -> Verdict | None:
    if not attempt.outcome:
        return None
    try:
        outcome = Outcome(attempt.outcome)
    except ValueError:
        return None
    reason = None
    if attempt.reason:
        try:
            reason = ReasonCode(attempt.reason)
        except ValueError:
            reason = None
    return Verdict(
        outcome=outcome,
        reason=reason,
        failing_obligation=attempt.failing_obligation,
        detail=attempt.detail or "",
    )


def _attempt_verdict(
    *,
    job_id: str,
    run: jobs.RunRecord,
    attempt: jobs.AttemptRecord,
    data_dir: str | Path,
) -> Verdict | None:
    bundle = _bundle_path(data_dir, job_id, run.id, attempt.id)
    if not bundle.exists():
        _run_executor(
            data_dir=data_dir,
            job_id=job_id,
            run_id=run.id,
            attempt_id=attempt.id,
            route=attempt.route,
            bundle=bundle,
        )
    if not bundle.exists():
        return None

    payload = _load_bundle(bundle)
    if payload.get("route") not in (None, attempt.route):
        raise ValueError(f"result bundle route does not match attempt {attempt.id}")

    obligations.persist_obligations(
        data_dir=data_dir,
        job_id=job_id,
        attempt_id=attempt.id,
        results=payload["results"],
        evidence=payload.get("evidence") or {},
        details=payload.get("details") or {},
    )
    return orchestration.evaluate_attempt(
        job_id,
        run.id,
        attempt_id=attempt.id,
        results=payload["results"],
        data_dir=data_dir,
        candidate_sha256=payload["candidate_sha256"],
    )


def run_once(*, data_dir: str | Path) -> int:
    runs = jobs.list_queued_runs(data_dir=data_dir)
    if not runs:
        print("worker: no queued runs")
        return 0

    processed = 0
    for run in runs:
        job = jobs.get_job(run.job_id, data_dir=data_dir)
        if job is None:
            print(f"worker: run {run.id} has no job")
            continue
        if job.cancel_requested:
            jobs.cancel_job(job.id, data_dir=data_dir)
            print(f"worker: job {job.id} cancelled")
            continue

        _set_phase("runs", run.id, "screening", data_dir=data_dir)
        _set_phase("jobs", job.id, "screening", data_dir=data_dir)

        verdicts: dict[str, Verdict] = {}
        for attempt in jobs.list_attempts(run.id, data_dir=data_dir):
            if attempt.phase in TERMINAL and attempt.outcome:
                verdict = _reconstruct(attempt)
                if verdict is not None:
                    verdicts[attempt.route] = verdict
                continue
            if attempt.phase == "cancelled":
                continue

            _set_phase("attempts", attempt.id, "analyzing", data_dir=data_dir)
            _set_phase("runs", run.id, "analyzing", data_dir=data_dir)
            _set_phase("jobs", job.id, "analyzing", data_dir=data_dir)
            try:
                verdict = _attempt_verdict(
                    job_id=job.id,
                    run=run,
                    attempt=attempt,
                    data_dir=data_dir,
                )
            except Exception as exc:  # noqa: BLE001 - worker must persist a failure
                try:
                    verdict = orchestration.evaluate_attempt(
                        job.id,
                        run.id,
                        attempt_id=attempt.id,
                        results={},
                        data_dir=data_dir,
                        candidate_sha256=job.original_sha256,
                        error_reason=ReasonCode.TOOL_FAILED,
                        error_detail=str(exc),
                    )
                except Exception as inner:  # noqa: BLE001
                    connection = connect(data_dir)
                    try:
                        with connection:
                            connection.execute(
                                "UPDATE attempts SET phase = 'finished', outcome = 'error', "
                                "reason = 'tool_failed', detail = ? WHERE id = ?",
                                (f"{exc}; fallback: {inner}", attempt.id),
                            )
                    finally:
                        connection.close()
                    refreshed = jobs.get_attempt(attempt.id, data_dir=data_dir)
                    verdict = _reconstruct(refreshed) if refreshed else None
                if verdict is not None:
                    verdicts[attempt.route] = verdict
                print(f"worker: attempt {attempt.id} failed: {exc}")
                continue

            if verdict is None:
                _set_phase("attempts", attempt.id, "queued", data_dir=data_dir)
                print(f"worker: attempt {attempt.id} has no result bundle yet")
                continue
            verdicts[attempt.route] = verdict
            processed += 1

        if "route_a" in verdicts and "route_b" in verdicts:
            orchestration.evaluate_run(
                job.id,
                run_id=run.id,
                route_a=verdicts["route_a"],
                route_b=verdicts["route_b"],
                data_dir=data_dir,
            )
            print(f"worker: run {run.id} finished")
        else:
            _set_phase("runs", run.id, "screening", data_dir=data_dir)
            _set_phase("jobs", job.id, "screening", data_dir=data_dir)
            print(f"worker: run {run.id} waiting for both routes")

    return processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.worker")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("BINARYREPAIR_DATA_DIR", "var"),
        help="controller data directory",
    )
    parser.add_argument("--once", action="store_true", help="process queued runs once")
    parser.add_argument("--poll", type=float, default=0.0, help="poll interval in seconds")
    args = parser.parse_args(argv)

    if args.poll > 0:
        while True:
            try:
                run_once(data_dir=args.data_dir)
            except Exception as exc:  # noqa: BLE001 - keep supervisor alive
                print(f"worker: error: {exc}", file=sys.stderr)
            time.sleep(args.poll)
    else:
        run_once(data_dir=args.data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
