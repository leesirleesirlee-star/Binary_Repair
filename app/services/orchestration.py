"""Orchestration: V0-V5 -> acceptance engine -> persisted Verdict.

The acceptance engine lives in ``app/domain`` and is supplied by step ② of the
integration. This module imports it lazily, so the controller-only branch can
exist before that step lands; calling the orchestration without the domain is an
ImportError, not a silent fallback.

Sequence:

1. after start, the run executor persists V0-V5 results;
2. ``evaluate_attempt`` is called once per route;
3. after both routes, ``evaluate_run`` is called once.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.db.connection import connect

GATES: tuple[tuple[str, str, bool], ...] = (
    ("V0", "identity", True),
    ("V1", "structure", True),
    ("V2", "interface", True),
    ("V3", "behaviour", True),
    ("V4", "symbolic", True),
    ("V5", "reproducibility", True),
)


def _gate_result(value: str | Any) -> Any:
    from app.domain.outcomes import CheckResult

    if isinstance(value, CheckResult):
        return value
    try:
        return CheckResult(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown check result: {value!r}") from exc


def build_obligations(
    results: Mapping[str, str | Any],
    *,
    required: Mapping[str, bool] | None = None,
) -> list[Any]:
    from app.domain.acceptance import Obligation

    required = required or {}
    obligations = []
    for gate, kind, default_required in GATES:
        result = _gate_result(results.get(gate, "not_run"))
        obligations.append(
            Obligation(
                kind=kind,
                required=required.get(gate, default_required),
                result=result,
                detail=f"{gate} {kind}: {result.value}",
            )
        )
    return obligations


def _verdict_payload(verdict: Any) -> dict[str, Any]:
    return {
        "outcome": verdict.outcome.value,
        "reason": verdict.reason.value if verdict.reason is not None else None,
        "failing_obligation": verdict.failing_obligation,
        "detail": verdict.detail,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _verdict_values(verdict: Any) -> tuple[str, str | None, str | None, str | None]:
    return (
        verdict.outcome.value,
        verdict.reason.value if verdict.reason is not None else None,
        verdict.failing_obligation,
        verdict.detail,
    )


def _select_attempt(
    connection: Any,
    *,
    run_id: str,
    attempt_id: str | None,
    route: str | None,
) -> Any:
    if attempt_id is not None:
        row = connection.execute(
            "SELECT * FROM attempts WHERE id = ? AND run_id = ?",
            (attempt_id, run_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown attempt id for run {run_id}: {attempt_id}")
        return row
    if route is not None:
        row = connection.execute(
            "SELECT * FROM attempts WHERE run_id = ? AND route = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (run_id, route),
        ).fetchone()
        if row is None:
            raise ValueError(f"no attempt for route {route!r} in run {run_id}")
        return row
    return None


def _run_context(job_id: str, run_id: str, data_dir: str | Path) -> tuple[Any, dict[str, Any]]:
    connection = connect(data_dir)
    try:
        run = connection.execute(
            "SELECT * FROM runs WHERE id = ? AND job_id = ?",
            (run_id, job_id),
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown run id for job {job_id}: {run_id}")
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"unknown job id: {job_id}")
    finally:
        connection.close()
    return run, {
        # Original input is identity/reporting data only. Approval currently
        # binds the reviewed scope, not a candidate hash, so expected_sha256
        # is None; never substitute the original input hash for it.
        "input_sha256": job["original_sha256"],
        "scope_sha256": run["reviewed_scope_sha256"],
    }


def evaluate_attempt(
    job_id: str,
    run_id: str,
    *,
    results: Mapping[str, str | Any],
    data_dir: str | Path,
    candidate_sha256: str,
    attempt_id: str | None = None,
    route: str | None = None,
    required: Mapping[str, bool] | None = None,
    cancelled: bool = False,
    error_reason: Any = None,
    error_detail: str = "",
    unsupported_reason: Any = None,
    unsupported_detail: str = "",
) -> Any:
    from app.domain.acceptance import decide_attempt_outcome

    run, body = _run_context(job_id, run_id, data_dir)
    obligations = build_obligations(results, required=required)
    verdict = decide_attempt_outcome(
        obligations=obligations,
        candidate_sha256=candidate_sha256,
        expected_sha256=None,
        reviewed_scope_sha256=body.get("scope_sha256"),
        cancelled=cancelled,
        error_reason=error_reason,
        error_detail=error_detail,
        unsupported_reason=unsupported_reason,
        unsupported_detail=unsupported_detail,
    )
    payload = {
        "job_id": job_id,
        "run_id": run_id,
        "candidate_sha256": candidate_sha256,
        "original_sha256": body.get("input_sha256"),
        "expected_sha256": None,
        "reviewed_scope_sha256": body.get("scope_sha256"),
        "obligations": [
            {
                "kind": item.kind,
                "required": item.required,
                "result": item.result.value,
                "detail": item.detail,
            }
            for item in obligations
        ],
        "verdict": _verdict_payload(verdict),
    }
    if attempt_id is None and route is None:
        _write_json(
            Path(data_dir) / "jobs" / job_id / "runs" / run_id / "verdict.json",
            payload,
        )

    outcome, reason, failing, detail = _verdict_values(verdict)
    connection = connect(data_dir)
    try:
        with connection:
            attempt = _select_attempt(
                connection,
                run_id=run_id,
                attempt_id=attempt_id,
                route=route,
            )
            if attempt is not None:
                connection.execute(
                    "UPDATE attempts SET phase = 'finished', outcome = ?, reason = ?, "
                    "failing_obligation = ?, detail = ? WHERE id = ?",
                    (outcome, reason, failing, detail, attempt["id"]),
                )
                _write_json(
                    Path(data_dir)
                    / "jobs"
                    / job_id
                    / "runs"
                    / run_id
                    / "attempts"
                    / str(attempt["id"])
                    / "verdict.json",
                    payload,
                )
    finally:
        connection.close()
    return verdict


def evaluate_run(
    job_id: str,
    *,
    route_a: Any,
    route_b: Any,
    data_dir: str | Path,
    run_id: str | None = None,
) -> Any:
    from app.domain.acceptance import decide_run_outcome

    verdict = decide_run_outcome(route_a=route_a, route_b=route_b)
    _write_json(
        Path(data_dir) / "jobs" / job_id / "run-verdict.json",
        {
            "job_id": job_id,
            "route_a": _verdict_payload(route_a),
            "route_b": _verdict_payload(route_b),
            "verdict": _verdict_payload(verdict),
        },
    )
    outcome, reason, failing, detail = _verdict_values(verdict)
    connection = connect(data_dir)
    try:
        with connection:
            if run_id is not None:
                connection.execute(
                    "UPDATE runs SET phase = 'finished', outcome = ?, reason = ?, "
                    "failing_obligation = ?, detail = ?, finished_at = ? "
                    "WHERE id = ? AND job_id = ?",
                    (outcome, reason, failing, detail, _now(), run_id, job_id),
                )
            else:
                connection.execute(
                    "UPDATE runs SET phase = 'finished', outcome = ?, reason = ?, "
                    "failing_obligation = ?, detail = ?, finished_at = ? "
                    "WHERE id = (SELECT id FROM runs WHERE job_id = ? "
                    "ORDER BY started_at DESC, rowid DESC LIMIT 1) AND job_id = ?",
                    (outcome, reason, failing, detail, _now(), job_id, job_id),
                )
            connection.execute("UPDATE jobs SET phase = 'finished' WHERE id = ?", (job_id,))
    finally:
        connection.close()
    return verdict


__all__ = ["GATES", "build_obligations", "evaluate_attempt", "evaluate_run"]
