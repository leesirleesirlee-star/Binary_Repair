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
import datetime as _dt
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.db.connection import connect
from app.domain.acceptance import Verdict
from app.domain.evidence import verify_candidate_binding
from app.domain.outcomes import Outcome, ReasonCode
from app.services import jobs, obligations, orchestration
from app.services.attempt_driver import routeb_bundle_dir

TERMINAL = {"finished", "cancelled"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _stamp_attempt(
    attempt_id: str,
    *,
    data_dir: str | Path,
    started: bool = False,
    finished: bool = False,
    candidate_sha256: str | None = None,
) -> None:
    assignments = []
    values: list[Any] = []
    if started:
        assignments.append("started_at = COALESCE(started_at, ?)")
        values.append(_now())
    if finished:
        assignments.append("finished_at = ?")
        values.append(_now())
    if candidate_sha256 is not None:
        assignments.append("candidate_sha256 = ?")
        values.append(candidate_sha256)
    if not assignments:
        return
    values.append(attempt_id)
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute(
                f"UPDATE attempts SET {', '.join(assignments)} WHERE id = ? "
                "AND EXISTS (SELECT 1 FROM runs WHERE runs.id = attempts.run_id "
                "AND runs.phase NOT IN ('finished', 'cancelled', 'cancelling'))", values,
            )
    finally:
        connection.close()


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
            owner_guard = (
                " AND EXISTS (SELECT 1 FROM runs WHERE runs.id = attempts.run_id "
                "AND runs.phase NOT IN ('finished', 'cancelled', 'cancelling'))"
                if table == "attempts" else ""
            )
            connection.execute(
                f"UPDATE {table} SET phase = ? WHERE id = ? "
                "AND phase NOT IN ('finished', 'cancelled', 'cancelling')" + owner_guard,
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _executor_inputs(data_dir: str | Path, job_id: str, run_id: str) -> tuple[Path, Path]:
    """Return verified original and contract paths for one executor spawn.

    Fail closed if the original bytes or the approved contract cannot be
    verified. The contract file is written atomically and idempotently under a
    contract-id path, so it cannot overwrite another revision.
    """

    root = Path(data_dir)
    connection = connect(data_dir)
    try:
        run = connection.execute(
            "SELECT * FROM runs WHERE id = ? AND job_id = ?",
            (run_id, job_id),
        ).fetchone()
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if run is None:
            raise ValueError(f"unknown run id for job {job_id}: {run_id}")
        if job is None:
            raise ValueError(f"unknown job id: {job_id}")
        contract = connection.execute(
            "SELECT * FROM contracts WHERE id = ? AND job_id = ?",
            (run["contract_id"], job_id),
        ).fetchone()
    finally:
        connection.close()

    if contract is None:
        raise ValueError("run has no approved contract")
    if not contract["approved_at"]:
        raise ValueError("run contract is not approved")

    original = root / "jobs" / job_id / "original.bin"
    if not original.is_file() or not os.access(original, os.R_OK):
        raise ValueError(f"original input is not readable: {original}")
    if _sha256_file(original) != str(job["original_sha256"]):
        raise ValueError("original input hash does not match the job record")

    body = contract["body_json"]
    if not isinstance(body, str):
        raise ValueError("contract body is not text")
    body_bytes = body.encode("utf-8")
    contract_hash = hashlib.sha256(body_bytes).hexdigest()
    bound_hash = str(run["reviewed_scope_sha256"] or "")
    if contract_hash != str(contract["sha256"]) or contract_hash != bound_hash:
        raise ValueError("contract bytes do not match the run-bound contract hash")

    contracts_dir = root / "jobs" / job_id / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    contract_path = contracts_dir / f"{run['contract_id']}.json"
    if contract_path.exists():
        if _sha256_file(contract_path) != contract_hash:
            raise ValueError(f"contract file exists with different bytes: {contract_path}")
    else:
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{run['contract_id']}-", suffix=".tmp", dir=contracts_dir
        )
        try:
            with os.fdopen(handle, "wb") as temp:
                temp.write(body_bytes)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, contract_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    if not os.access(contract_path, os.R_OK):
        raise ValueError(f"contract file is not readable: {contract_path}")
    return original, contract_path


def _executor_argv(command: str) -> list[str]:
    """Turn ``BINARYREPAIR_ATTEMPT_CMD`` into an argv list.

    A value that names an existing file is executed as-is, which keeps the
    original single-executable contract for wrappers with spaces in their path.
    Otherwise the value is a command line and is split, so ``python -m
    app.services.attempt_driver`` works without a shell wrapper.
    """
    if Path(command).exists():
        return [command]
    argv = shlex.split(command)
    if not argv:
        raise ValueError("BINARYREPAIR_ATTEMPT_CMD is empty")
    return argv


_PROBE_CACHE: dict[str, tuple[float, bool]] = {}


def _probe_executor(command: str, *, ttl: float = 10.0) -> bool:
    """Return True when the configured executor reports readiness.

    The probe is a cheap, side-effect-free check (the guest bridge implements it
    as ``--probe``). A false result records a worker observation and leaves the
    run queued, so an unavailable guest produces ``waiting_for_executor``
    instead of a fabricated execution or a premature tool failure.
    """

    now = time.monotonic()
    cached = _PROBE_CACHE.get(command)
    if cached is not None and now - cached[0] < ttl:
        return cached[1]
    try:
        completed = subprocess.run(
            _executor_argv(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        ready = False
    else:
        ready = completed.returncode == 0
    _PROBE_CACHE[command] = (now, ready)
    return ready


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
    original, contract = _executor_inputs(data_dir, job_id, run_id)
    env = os.environ.copy()
    env.update(
        {
            "BINARYREPAIR_DATA_DIR": str(data_dir),
            "BINARYREPAIR_JOB_ID": job_id,
            "BINARYREPAIR_RUN_ID": run_id,
            "BINARYREPAIR_ATTEMPT_ID": attempt_id,
            "BINARYREPAIR_ROUTE": route,
            "BINARYREPAIR_RESULT_PATH": str(bundle),
            "BINARYREPAIR_ORIGINAL": str(original),
            "BINARYREPAIR_CONTRACT": str(contract),
            # Host-side driver contract: where the guest leaves the Route B
            # static bundle. Always set so a wrapper needs no extra flags; the
            # driver fails closed when the directory is absent.
            "BINARYREPAIR_ROUTEB_BUNDLE": str(
                routeb_bundle_dir(data_dir, job_id, run_id, attempt_id)
            ),
        }
    )
    completed = subprocess.run(
        _executor_argv(command),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().splitlines()
        tail = stderr[-1] if stderr else ""
        raise RuntimeError(
            f"executor exited with status {completed.returncode}"
            + (f": {tail}" if tail else "")
        )
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

    current = jobs.get_run(run.id, data_dir=data_dir)
    if current is None or current.phase in TERMINAL | {"cancelling"}:
        return None

    payload = _load_bundle(bundle)
    if payload.get("route") not in (None, attempt.route):
        raise ValueError(f"result bundle route does not match attempt {attempt.id}")

    if any(value == "passed" for value in payload["results"].values()):
        verify_candidate_binding(
            data_dir=data_dir,
            job_id=job_id,
            attempt_id=attempt.id,
            candidate_sha256=payload["candidate_sha256"],
            evidence=payload.get("evidence") or {},
        )
    _stamp_attempt(
        attempt.id,
        data_dir=data_dir,
        finished=True,
        candidate_sha256=payload["candidate_sha256"],
    )
    obligations.persist_obligations(
        data_dir=data_dir,
        job_id=job_id,
        attempt_id=attempt.id,
        results=payload["results"],
        evidence=payload.get("evidence") or {},
        details=payload.get("details") or {},
        candidate_sha256=payload["candidate_sha256"],
    )
    # persist_obligations may downgrade a claimed pass (V2/V4) after validating
    # its evidence. Evaluate the persisted rows, not the unvalidated payload,
    # so a downgraded gate can never produce success.
    persisted = {
        row["kind"]: row["result"]
        for row in jobs.list_obligations(attempt.id, data_dir=data_dir)
    }
    effective = {
        gate: persisted.get(kind, "not_run")
        for gate, kind, _required in orchestration.GATES
    }
    return orchestration.evaluate_attempt(
        job_id,
        run.id,
        attempt_id=attempt.id,
        results=effective,
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
        scope_ok, scope_detail = jobs.reviewed_scope_state(
            run.job_id, run.id, data_dir=data_dir,
        )
        if not scope_ok:
            jobs.fail_unbound_run(
                run.job_id, run.id, detail=scope_detail, data_dir=data_dir,
            )
            print(f"worker: run {run.id} refused before the executor: {scope_detail}")
            continue
        pending = [
            attempt for attempt in jobs.list_attempts(run.id, data_dir=data_dir)
            if attempt.phase not in TERMINAL
        ]
        waiting_for_evidence = bool(pending) and not any(
            _bundle_path(data_dir, job.id, run.id, attempt.id).is_file() for attempt in pending
        )
        attempt_command = os.environ.get("BINARYREPAIR_ATTEMPT_CMD")
        probe_command = os.environ.get("BINARYREPAIR_EXECUTOR_PROBE_CMD")
        if waiting_for_evidence and not attempt_command:
            jobs.record_executor_unavailable(run, data_dir=data_dir)
            continue
        if waiting_for_evidence and probe_command and not _probe_executor(probe_command):
            jobs.record_executor_unavailable(run, data_dir=data_dir)
            continue
        if not jobs.claim_run(run, data_dir=data_dir):
            continue

        verdicts: dict[str, Verdict] = {}
        for attempt in jobs.list_attempts(run.id, data_dir=data_dir):
            current = jobs.get_run(run.id, data_dir=data_dir)
            if current is None or current.phase in TERMINAL | {"cancelling"}:
                break
            if attempt.phase in TERMINAL and attempt.outcome:
                verdict = _reconstruct(attempt)
                if verdict is not None:
                    verdicts[attempt.route] = verdict
                continue
            if attempt.phase == "cancelled":
                continue

            _stamp_attempt(attempt.id, data_dir=data_dir, started=True)
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
                current = jobs.get_run(run.id, data_dir=data_dir)
                if current is None or current.phase in TERMINAL | {"cancelling"}:
                    break
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
                                "reason = 'tool_failed', detail = ? WHERE id = ? "
                                "AND EXISTS (SELECT 1 FROM runs WHERE runs.id = attempts.run_id "
                                "AND runs.phase NOT IN ('finished', 'cancelled', 'cancelling'))",
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
            _stamp_attempt(attempt.id, data_dir=data_dir, finished=True)
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
        default=os.environ.get("BINARYREPAIR_DATA_DIR"),
        help="controller data directory; defaults to BINARYREPAIR_DATA_DIR",
    )
    parser.add_argument("--once", action="store_true", help="process queued runs once")
    parser.add_argument("--poll", type=float, default=0.0, help="poll interval in seconds")
    args = parser.parse_args(argv)
    if not args.data_dir:
        parser.error("--data-dir is required (or set BINARYREPAIR_DATA_DIR)")

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
