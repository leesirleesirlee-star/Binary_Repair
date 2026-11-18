"""HTTP API for the member-1 job controller.

This is a separate FastAPI service from the GUI. The GUI's ``http`` backend
adapter calls these endpoints:

    POST /api/jobs                         multipart create
    GET  /api/jobs/{id}                    snapshot
    POST /api/jobs/{id}/start              approve + idempotent run start
    POST /api/jobs/{id}/cancel             cancel
    GET  /api/jobs/{id}/obligations        persisted V0-V5 rows of the latest attempt per route
    GET  /api/attempts/{id}/evidence       persisted per-attempt evidence
    GET  /api/jobs/{id}/artifacts          artifact list
    GET  /api/jobs/{id}/artifacts/{aid}    artifact metadata, job scoped
    GET  /api/jobs/{id}/artifacts/{aid}/download
                                           verified artifact bytes, job scoped
    GET  /api/jobs/{id}/analysis           screening state and analysis artifacts
    POST /api/jobs/{id}/analysis           request a screening (no executor yet)
    GET  /api/jobs/{id}/comparison         behavior-comparison artifact, when ingested
    GET  /api/jobs/{id}/v3                 V3 round-trip vs repaired-behaviour
    GET  /api/artifacts/{id}               artifact metadata by id (legacy)
    GET  /api/artifacts/{id}/download      registered artifact bytes (legacy)

Evidence honesty rule: this module only *renders* what the worker persisted. It
never invents a ``passed`` obligation, and an attempt whose checks never ran has
no evidence rows at all.

Run:

    .\\.venv\\Scripts\\python.exe -m uvicorn app.controller_api:app --host 127.0.0.1 --port 8000

Then start the GUI with ``BINARYREPAIR_BACKEND=http`` and
``BINARYREPAIR_API_BASE=http://127.0.0.1:8000``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.db.connection import connect
from app.services import jobs

APP_ENV = "BINARYREPAIR_DATA_DIR"
DEFAULT_DATA_DIR = "var"

ROUTE_STAGES: dict[str, list[str]] = {
    "A": ["screening", "analyzing", "generating", "applying", "validating"],
    "B": ["screening", "analyzing", "roundtrip", "generating", "applying", "validating"],
}
ROUTE_NAMES: dict[str, str] = {
    "A": "Route A - Direct instruction repair",
    "B": "Route B - Recovered C reintegration",
}

# Roles that carry displayable analysis content, from
# docs/reference/interfaces/artifact-roles-v1.md. They are display only and never become
# an acceptance obligation.
ANALYSIS_ROLES = frozenset(
    {
        "analysis_expected_effects",
        "analysis_assumptions",
        "analysis_uncertainties",
        "analysis_v3",
        "behavior_comparison",
    }
)

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_CJK = re.compile(r"[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


def _data_dir() -> Path:
    return Path(os.environ.get(APP_ENV, DEFAULT_DATA_DIR))


def _sha_json(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ascii_filename(name: str) -> str:
    base = Path(name or "").name
    if base and base.isascii() and _UNSAFE_FILENAME.search(base) is None:
        return base
    stem, dot, suffix = base.rpartition(".")
    stem_clean = _UNSAFE_FILENAME.sub("_", stem).strip("_-") if stem else ""
    suffix_clean = _UNSAFE_FILENAME.sub("", suffix) if dot else ""
    if not stem_clean:
        stem_clean = "uploaded-sample"
    return f"{stem_clean}.{suffix_clean}" if suffix_clean else stem_clean


def _require_english(fields: dict[str, str]) -> None:
    for name, value in fields.items():
        if value and any(ord(char) > 127 for char in value):
            raise HTTPException(
                status_code=422,
                detail=f"Field '{name}' must use English/ASCII characters only.",
            )


def _contract_body(
    *,
    valid_domain: str = "",
    forbidden: str = "",
    invalid_outcome: str = "",
    assumptions: str = "",
) -> dict[str, Any]:
    body = {
        "valid_domain": valid_domain.strip()
        or "Input length 0-64 bytes; out-of-range lengths rejected",
        "forbidden": forbidden.strip()
        or "Out-of-bounds write to the target buffer",
        "invalid_outcome": invalid_outcome.strip()
        or "Return error code 1 without out-of-bounds access",
        "assumptions": [
            line.strip()
            for line in (
                assumptions
                or "Single process\nLinux x86-64 ELF\nStack protector disabled"
            ).splitlines()
            if line.strip()
        ],
    }
    return {
        "revision": 1,
        "sha256": _sha_json(body),
        "approved": False,
        "approved_at": None,
        "reviewer_label": None,
        **body,
    }


def _meta_path(job_id: str) -> Path:
    return _data_dir() / "jobs" / job_id / "meta.json"


def _read_meta(job_id: str) -> dict[str, Any]:
    path = _meta_path(job_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_meta(job_id: str, payload: dict[str, Any]) -> None:
    path = _meta_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _job_or_404(job_id: str) -> jobs.JobRecord:
    record = jobs.get_job(job_id, data_dir=_data_dir())
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return record


def _artifact_file(row: dict[str, Any]) -> Path | None:
    data_dir = _data_dir().resolve()
    relative = Path(str(row.get("relative_path") or ""))
    candidates = [
        data_dir / relative,
        data_dir / "jobs" / str(row.get("job_id") or "") / "artifacts" / relative,
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved.is_relative_to(data_dir):
            return resolved
    return None


def _artifact_problem(
    row: dict[str, Any], path: Path | None, *, verify_hash: bool
) -> str:
    """Return an empty string when the bytes on disk match the registration.

    ``verify_hash=False`` only compares the file size, which is cheap enough for
    a listing. Detail and download pass ``True`` and rehash the bytes.
    """

    if path is None:
        return "artifact bytes are not on disk"
    try:
        size = path.stat().st_size
    except OSError:
        return "artifact bytes are not readable"
    registered_size = row.get("size_bytes")
    if registered_size is not None and size != int(registered_size):
        return "artifact size does not match the registered size"
    if not verify_hash:
        return ""
    try:
        payload = path.read_bytes()
    except OSError:
        return "artifact bytes are not readable"
    registered = str(row.get("sha256") or "").lower()
    if registered and hashlib.sha256(payload).hexdigest() != registered:
        return "artifact sha256 does not match the registered hash"
    return ""


def _artifact_metadata(row: dict[str, Any], *, verify_hash: bool = False) -> dict[str, Any]:
    path = _artifact_file(row)
    problem = _artifact_problem(row, path, verify_hash=verify_hash)
    artifact_id = row["id"]
    job_id = str(row.get("job_id") or "")
    run_id = row.get("run_id")
    return {
        "id": artifact_id,
        "job_id": job_id,
        "run_id": run_id,
        "kind": (
            "run_evidence" if row.get("attempt_id")
            else "original_input" if row["role"] in {"original", "original_input"}
            else "manual_deliverable"
        ),
        "name": path.name if path else Path(str(row["relative_path"])).name,
        "role": row["role"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "media_type": row["media_type"],
        "available": problem == "",
        "unavailable_reason": problem,
        "integrity": "sha256" if (verify_hash and problem == "") else "size",
        "content_url": f"/api/artifacts/{artifact_id}/download",
        "job_content_url": f"/api/jobs/{job_id}/artifacts/{artifact_id}/download",
    }


def _job_artifact_or_404(job_id: str, artifact_id: str) -> dict[str, Any]:
    _job_or_404(job_id)
    row = jobs.get_artifact(artifact_id, data_dir=_data_dir())
    if row is None or str(row.get("job_id") or "") != job_id:
        # An artifact of another job must look exactly like a missing one.
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return row


def _verified_artifact_response(job_id: str, artifact_id: str) -> FileResponse:
    row = _job_artifact_or_404(job_id, artifact_id)
    path = _artifact_file(row)
    problem = _artifact_problem(row, path, verify_hash=True)
    if problem or path is None:
        raise HTTPException(status_code=409, detail=problem or "artifact bytes are not on disk")
    return FileResponse(
        path,
        media_type=row.get("media_type") or "application/octet-stream",
        filename=path.name,
        content_disposition_type="attachment",
    )


def _ai_usage_for_job(job_id: str) -> dict[str, Any] | None:
    """Provider token usage recorded for this job.

    ``None`` means nothing recorded real usage. A missing number is never
    rendered as a made-up one, and a partial record is not padded with zeros.

    ``partial`` is passed through from :func:`aggregate_job_usage` unchanged:
    ``True`` means at least one recorded call has a NULL token count, so the sum
    covers only the calls that did report real tokens; ``False`` means every
    recorded call contributed. The plan fallback always reports ``False``
    because it reads one stored plan record that either has both counts or is
    not used at all -- there are no siblings that could be missing.
    """
    from app.db.connection import connect
    from app.services.assistant import get_plan
    from app.services.model_calls import aggregate_job_usage

    connection = connect(_data_dir())
    try:
        recorded = connection.execute(
            "SELECT 1 FROM model_calls WHERE job_id = ? OR attempt_id IN ("
            "  SELECT a.id FROM attempts a JOIN runs r ON r.id = a.run_id "
            "  WHERE r.job_id = ?"
            ") LIMIT 1",
            (job_id, job_id),
        ).fetchone()
    finally:
        connection.close()
    if recorded is not None:
        # Calls exist, so the aggregate is authoritative. It is None only when
        # no row carries real tokens; when some rows do, it returns their sum
        # with ``partial: true``. Falling back to the plan would mix two
        # sources under one label. Return it untouched so the flag cannot be
        # dropped or re-derived here.
        return aggregate_job_usage(data_dir=_data_dir(), job_id=job_id)

    # Older jobs have no model_calls rows; their plan is the only real record.
    record = get_plan(job_id, data_dir=_data_dir())
    usage = (record or {}).get("usage")
    if not isinstance(usage, dict):
        return None
    values = [usage.get("input_tokens"), usage.get("output_tokens")]
    if not all(isinstance(value, int) and value >= 0 for value in values):
        return None
    return {
        "input_tokens": values[0],
        "output_tokens": values[1],
        "partial": False,
    }


def _placeholder_routes(phase: str) -> dict[str, dict[str, Any]]:
    routes: dict[str, dict[str, Any]] = {}
    for key, stages in ROUTE_STAGES.items():
        routes[key] = {
            "name": ROUTE_NAMES[key],
            "phase": phase,
            "outcome": None,
            "outcome_reason": "",
            "stages": [{"name": name, "state": "pending"} for name in stages],
            "tests": {"passed": 0, "total": 0},
            "verification": "not_run",
            "evidence": "not_persisted",
            "compatibility": "not_run",
            "attempt_id": None,
            "changed_regions": [],
            "manual_interventions": 0,
            "ai_usage": None,
            "elapsed_s": 0.0,
        }
    return routes


def _stage_index(phase: str, stages: list[str]) -> int | None:
    order = {
        "screening": 0,
        "analyzing": 1,
        "generating": 2,
        "applying": 3,
        "roundtrip": stages.index("roundtrip") if "roundtrip" in stages else None,
        "validating": 4,
        "finished": len(stages),
    }
    return order.get(phase)


def _stages_for(phase: str, stages: list[str]) -> list[dict[str, str]]:
    index = _stage_index(phase, stages)
    result = []
    for position, name in enumerate(stages):
        if index is None:
            state = "pending"
        elif position < index:
            state = "done"
        elif position == index:
            state = "active"
        else:
            state = "pending"
        result.append({"name": name, "state": state})
    return result


def _verdict_for_attempt(attempt: jobs.AttemptRecord) -> dict[str, Any]:
    return {
        "outcome": attempt.outcome,
        "reason": attempt.reason,
        "failing_obligation": attempt.failing_obligation,
        "detail": attempt.detail,
    }


def _route_key(route: str) -> str:
    """Map a stored route name (``route_a``/``route_b``) to the GUI key."""
    return "A" if route == "route_a" else "B"


def _evidence_state(rows: list[dict[str, Any]]) -> str:
    """Machine-readable marker for whether this payload is backed by stored rows.

    ``not_persisted`` means the worker never wrote obligation rows for this
    attempt. Consumers must treat that as "no evidence", not as "checks passed".
    """
    return "persisted" if rows else "not_persisted"


def _obligation_groups(
    rows: list[dict[str, Any]], route_key: str
) -> dict[str, list[dict[str, Any]]]:
    """Keep the historical route-keyed payload shape while serving real rows.

    Older consumers (``scripts/demo_e2e.py``) read ``obligations`` as a mapping
    keyed by route. For a single attempt only its own route group carries the
    persisted rows; the other group is empty rather than a placeholder.
    """
    return {"A": rows if route_key == "A" else [], "B": rows if route_key == "B" else []}


def _verification_label(results: set[str]) -> str:
    """Display label for one route's persisted obligation results.

    Display vocabulary only: the acceptance decision stays in
    ``app/domain/acceptance.py``. A definite failure outranks missing evidence,
    so any ``failed`` row is shown as ``failed``. Mixed or incomplete evidence
    (for example V0 passed with V1-V5 not_run) is ``inconclusive`` - the engine's
    honest word for "not every required check was demonstrated" - never
    ``unknown`` and never ``passed``.
    """
    if "failed" in results:
        return "failed"
    if results == {"passed"}:
        return "passed"
    if results == {"not_run"}:
        return "not_run"
    return "inconclusive"


def _job_obligation_groups(job_id: str) -> dict[str, list[dict[str, Any]]]:
    """Persisted V0-V5 rows of the latest attempt per route.

    No run or no persisted rows means empty groups: the payload never falls back
    to a placeholder that claims an identity check passed.
    """
    groups: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    run = jobs.latest_run(job_id, data_dir=_data_dir())
    if run is None:
        return groups
    latest: dict[str, jobs.AttemptRecord] = {}
    for attempt in jobs.list_attempts(run.id, data_dir=_data_dir()):
        previous = latest.get(attempt.route)
        if previous is None or attempt.sequence > previous.sequence:
            latest[attempt.route] = attempt
    for route, attempt in latest.items():
        groups[_route_key(route)] = jobs.list_obligations(
            attempt.id, data_dir=_data_dir()
        )
    return groups


STEP_FLOW: tuple[tuple[str, str], ...] = (
    ("intake", "Describe the defect"),
    ("contract", "Review the repair contract"),
    ("screening", "Screen the original"),
    ("repair", "Repair and verify"),
    ("verification", "Collect evidence"),
    ("done", "Finished"),
)
STEP_IDS: tuple[str, ...] = tuple(step for step, _ in STEP_FLOW)
RUN_ACTIVE_PHASES = frozenset(
    {"screening", "analyzing", "roundtrip", "generating", "applying", "validating"}
)
RUN_FAILED_OUTCOMES = frozenset({"rejected", "error", "failed"})
COMPARISON_ROLE = "behavior_comparison"
COMPARISON_MAX_BYTES = 2 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _analysis_rows(job_id: str) -> list[dict[str, Any]]:
    """Registered screening artifacts (``analysis_*`` roles), newest last."""
    return [
        row
        for row in jobs.list_artifacts(job_id, data_dir=_data_dir())
        if str(row.get("role") or "").startswith("analysis_")
    ]


def _analysis_state(job_id: str) -> dict[str, Any]:
    """Screening state without pretending a producer exists.

    ``available`` means artifacts are registered; ``requested`` means the host
    recorded a request but nothing has been ingested; ``not_provided`` is the
    honest answer for a job nobody has screened. The screening executor (M3) and
    the ingest adapter (M5) are the only writers of the first two states.
    """
    rows = _analysis_rows(job_id)
    request = _read_meta(job_id).get("analysis") or {}
    if rows:
        status = "available"
        detail = f"{len(rows)} analysis artifact(s) are registered for this job"
    elif request.get("status") == "requested":
        status = "requested"
        detail = (
            "a screening was requested but no analysis artifact has been ingested; "
            "this build has no screening executor"
        )
    else:
        status = "not_provided"
        detail = "no screening has been requested for this job"
    return {
        "status": status,
        "requested_at": request.get("requested_at"),
        "detail": detail,
        "rows": rows,
    }


def _analysis_payload(job_id: str, record: jobs.JobRecord) -> dict[str, Any]:
    state = _analysis_state(job_id)
    return {
        "job_id": job_id,
        "purpose": "screening",
        "status": state["status"],
        "requested_at": state["requested_at"],
        "detail": state["detail"],
        "phase": record.phase,
        "screening": None,
        "artifacts": [_artifact_metadata(row, verify_hash=True) for row in state["rows"]],
    }


def _comparison_payload(job_id: str) -> dict[str, Any]:
    """Behavior comparison is rendered only from an ingested artifact."""
    rows = [
        row
        for row in jobs.list_artifacts(job_id, data_dir=_data_dir())
        if row.get("role") == COMPARISON_ROLE
    ]
    base: dict[str, Any] = {
        "job_id": job_id,
        "status": "not_provided",
        "detail": "no behavior-comparison artifact is registered for this job",
        "comparison": None,
        "artifacts": [],
    }
    if not rows:
        return base

    row = rows[0]
    base["status"] = "available"
    base["artifacts"] = [_artifact_metadata(row, verify_hash=True)]
    path = _artifact_file(row)
    if path is None:
        base["detail"] = "the comparison artifact is registered but its bytes are not on disk"
        return base
    # Content is only rendered from bytes that still match the registration.
    problem = _artifact_problem(row, path, verify_hash=True)
    if problem:
        base["detail"] = f"refusing to render the comparison: {problem}"
        return base
    if path.stat().st_size > COMPARISON_MAX_BYTES:
        base["detail"] = (
            f"the comparison artifact is larger than {COMPARISON_MAX_BYTES} bytes; "
            "download it directly"
        )
        return base
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        base["detail"] = f"the comparison artifact is not readable JSON: {exc}"
        return base
    base["comparison"] = parsed
    base["detail"] = ""
    return base


V3_KIND_ROLES: dict[str, str] = {
    "round_trip": "routeb_manifest_compare",
    "repaired_behaviour": "routeb_manifest_repaired",
}


def _assistant_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Render one stored plan.  ``plan`` is the AssistantPlan body; the wrapper
    keeps the mode/model/usage metadata that explains how it was produced."""
    return {
        "id": record.get("id"),
        "job_id": record.get("job_id"),
        "mode": record.get("mode"),
        "plan": record.get("plan"),
        "model": record.get("model"),
        "usage": record.get("usage"),
        "created_at": record.get("created_at"),
    }


def _v3_payload(job_id: str) -> dict[str, Any]:
    """The two V3 evidence kinds, kept separate.

    ``round_trip`` records an unchanged round trip; it is evidence, not repair
    credit. Only ``repaired_behaviour`` can earn V3, and the API says so in
    ``v3_credit`` instead of letting a reader guess from two similar rows.
    """
    rows = jobs.list_artifacts(job_id, data_dir=_data_dir())
    kinds: dict[str, Any] = {}
    for kind, role in V3_KIND_ROLES.items():
        matches = [row for row in rows if row.get("role") == role]
        kinds[kind] = _artifact_metadata(matches[-1], verify_hash=True) if matches else None
    repaired = kinds["repaired_behaviour"] is not None
    round_trip = kinds["round_trip"] is not None
    if repaired:
        status, detail = "available", "repaired behaviour evidence is registered"
    elif round_trip:
        status = "available"
        detail = (
            "only round-trip evidence is registered; an unchanged round trip is "
            "not repair V3 and earns no behaviour credit"
        )
    else:
        status, detail = "not_provided", "no V3 evidence artifact is registered"
    return {
        "job_id": job_id,
        "status": status,
        "v3_credit": repaired,
        "detail": detail,
        "kinds": kinds,
    }


def _execution_status(record: jobs.JobRecord, run_row: Any) -> dict[str, Any]:
    """Derive execution state from persisted rows.

    Record 87 asks for an ``execution.status``. The execution-bridge sidecar it
    once referred to (record 85) does not exist in this tree, so this value is
    derived from ``runs.phase`` / ``runs.outcome`` and says so: ``sidecar`` stays
    ``null`` and ``source`` is explicit. Nothing here upgrades an unknown phase
    into a success.
    """
    base: dict[str, Any] = {
        "source": "controller-derived",
        "sidecar": None,
        "note": (
            "Derived from persisted run rows; no producer sidecar exists in this build."
        ),
        "run_id": None,
        "phase": record.phase,
        "outcome": None,
        "started_at": None,
        "finished_at": None,
    }
    if run_row is None:
        if record.phase == "cancelled":
            status = "cancelled"
        elif record.phase in {"draft", "awaiting_review"}:
            status = "not_started"
        elif record.phase in {"queued", "running"}:
            status = "queued"
        else:
            status = "unknown"
        return {**base, "status": status}

    keys = set(run_row.keys())
    phase = str(run_row["phase"])
    outcome = run_row["outcome"] if "outcome" in keys else None
    if phase == "queued":
        status = "queued"
    elif phase in RUN_ACTIVE_PHASES:
        status = "running"
    elif phase == "finished":
        status = "failed" if str(outcome or "").lower() in RUN_FAILED_OUTCOMES else "completed"
    elif phase == "cancelled":
        status = "cancelled"
    else:
        status = "unknown"
    return {
        **base,
        "status": status,
        "run_id": run_row["id"],
        "phase": phase,
        "outcome": outcome,
        "started_at": run_row["started_at"] if "started_at" in keys else None,
        "finished_at": run_row["finished_at"] if "finished_at" in keys else None,
    }


def _current_step(
    record: jobs.JobRecord, run_row: Any, analysis_status: str
) -> str:
    phase = record.phase
    if phase == "cancelled":
        return "done"
    if run_row is not None:
        run_phase = str(run_row["phase"])
        if run_phase in {"finished", "cancelled"}:
            return "done"
        return "repair"
    if phase == "finished":
        return "done"
    if analysis_status == "requested":
        return "screening"
    if phase == "draft":
        return "intake"
    if phase == "awaiting_review":
        return "contract"
    if phase in {"queued", "running"}:
        return "repair"
    return "unknown"


def _agent_state(
    record: jobs.JobRecord, run_row: Any, analysis_status: str
) -> dict[str, Any]:
    """The step the agent console should highlight, with the whole flow."""
    current = _current_step(record, run_row, analysis_status)
    index = STEP_IDS.index(current) if current in STEP_IDS else None
    steps = []
    for position, (step_id, label) in enumerate(STEP_FLOW):
        if index is None:
            state = "pending"
        elif position < index:
            state = "done"
        elif position == index:
            state = "active"
        else:
            state = "pending"
        steps.append({"id": step_id, "label": label, "state": state})
    return {
        "current_step": current,
        "label": dict(STEP_FLOW).get(current, ""),
        "steps": steps,
        "source": "controller-derived",
    }


def _snapshot(record: jobs.JobRecord, meta: dict[str, Any]) -> dict[str, Any]:
    contract = dict(meta.get("contract") or _contract_body())
    contract.setdefault("revision", 1)
    contract.setdefault("sha256", _sha_json({k: v for k, v in contract.items() if k not in {"revision", "sha256", "approved", "approved_at", "reviewer_label"}}))
    contract.setdefault("approved", False)

    routes = _placeholder_routes(record.phase)
    ai_usage = _ai_usage_for_job(record.id)
    for route in routes.values():
        route["ai_usage"] = ai_usage
    run = jobs.latest_run(record.id, data_dir=_data_dir())
    run_row: Any = None
    run_verdict: dict[str, Any] | None = None
    if run is not None:
        connection = connect(_data_dir())
        try:
            run_row = connection.execute("SELECT * FROM runs WHERE id = ?", (run.id,)).fetchone()
        finally:
            connection.close()
        run_verdict = {
            "outcome": run_row["outcome"] if run_row else None,
            "reason": run_row["reason"] if run_row else None,
            "failing_obligation": run_row["failing_obligation"] if run_row else None,
            "detail": run_row["detail"] if run_row else None,
        }
        for attempt in jobs.list_attempts(run.id, data_dir=_data_dir()):
            key = _route_key(attempt.route)
            route = routes[key]
            route["attempt_id"] = attempt.id
            route["phase"] = attempt.phase
            route["outcome"] = attempt.outcome
            route["outcome_reason"] = attempt.reason or ""
            route["verdict"] = _verdict_for_attempt(attempt)
            group = jobs.list_obligations(attempt.id, data_dir=_data_dir())
            route["obligations"] = group
            route["evidence"] = _evidence_state(group)
            route["stages"] = _stages_for(attempt.phase, ROUTE_STAGES[key])
            if group:
                results = {item["result"] for item in group}
                route["verification"] = _verification_label(results)
                interface = next(
                    (item for item in group if item["kind"] == "interface"), None
                )
                if interface is not None:
                    route["compatibility"] = interface["result"]

    analysis = _analysis_state(record.id)
    return {
        "id": record.id,
        "demo": False,
        "data_source": "controller-http",
        "execution": _execution_status(record, run_row),
        "execution_phase": "not_started" if record.phase == "draft" else record.phase,
        "agent": _agent_state(record, run_row, analysis["status"]),
        "filename": record.filename,
        "original_sha256": record.original_sha256,
        "defect": record.defect_description,
        "valid_tests": meta.get("valid_tests", []),
        "environment": meta.get("environment", ""),
        "created_at": record.created_at,
        "manifest_sha256": meta.get("manifest_sha256"),
        "phase": record.phase,
        "cancelled": record.phase == "cancelled",
        "contract": contract,
        "routes": routes,
        "run": {
            "id": run.id,
            "phase": run.phase,
            "manifest_sha256": run.manifest_sha256,
            "reviewed_scope_sha256": run.reviewed_scope_sha256,
        } if run is not None else None,
        "verdict": run_verdict,
    }


app = FastAPI(title="BinaryRepair Controller API", version="0.1.0")


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0].strip().lower()
    if host not in {"127.0.0.1", "localhost"}:
        return JSONResponse(
            {"detail": "Local access only (host check failed)."},
            status_code=403,
        )
    return await call_next(request)


ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost", "::1"}
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _state_change_origin_allowed(origin: str) -> bool:
    """Accept only loopback origins for browser-initiated state changes."""
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return (parsed.hostname or "").lower() in ALLOWED_ORIGIN_HOSTS


def _state_change_authorized(request: Request) -> tuple[bool, int, str]:
    origin = request.headers.get("origin")
    if origin and not _state_change_origin_allowed(origin):
        return False, 403, "cross-origin state change rejected"
    if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return False, 403, "cross-site state change rejected"
    token = os.environ.get("BINARYREPAIR_CONTROLLER_TOKEN")
    if token:
        supplied = request.headers.get("x-binaryrepair-token") or ""
        authorization = request.headers.get("authorization") or ""
        if not supplied and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not hmac.compare_digest(token, supplied):
            return False, 401, "missing or invalid controller token"
    return True, 200, ""


@app.middleware("http")
async def _guard_state_changes(request: Request, call_next: Any) -> Any:
    """Reject cross-site or unauthenticated state changes.

    Reads are unaffected: this service is loopback-only and every read is job
    scoped. Uploads, approvals, runs, cancels, and analysis requests all pass
    through here, so there is no unguarded write path.
    """
    if request.method in STATE_CHANGING_METHODS:
        allowed, status, detail = _state_change_authorized(request)
        if not allowed:
            return JSONResponse({"detail": detail}, status_code=status)
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "binaryrepair-controller", "backend": "http", "demo": None}


@app.post("/api/jobs", status_code=201)
async def create_job(
    binary: UploadFile = File(...),
    defect: str = Form(""),
    valid_tests: str = Form(""),
    environment: str = Form(""),
    valid_domain: str = Form(""),
    forbidden: str = Form(""),
    invalid_outcome: str = Form(""),
    assumptions: str = Form(""),
) -> dict[str, Any]:
    _require_english(
        {
            "defect": defect,
            "valid_tests": valid_tests,
            "environment": environment,
            "valid_domain": valid_domain,
            "forbidden": forbidden,
            "invalid_outcome": invalid_outcome,
            "assumptions": assumptions,
        }
    )
    payload = await binary.read()
    if not payload:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    # A non-ELF upload cannot be repaired, screened, or executed; rejecting it
    # here keeps junk out of jobs/ and out of every later hash comparison.
    if not payload.startswith(b"\x7fELF"):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not an ELF binary (missing \\x7fELF magic).",
        )

    raw_filename = binary.filename or "sample.bin"
    parts = re.split(r"[\\/]+", raw_filename)
    if (
        raw_filename.startswith(("/", "\\"))
        or ".." in parts
        or (parts and parts[0] in {"", "."})
    ):
        raise HTTPException(
            status_code=400,
            detail="Uploaded filename must be a plain basename without path traversal.",
        )

    filename = _ascii_filename(raw_filename)
    record = jobs.create_job(
        original_bytes=payload,
        filename=filename,
        defect_description=defect or "Defect description not provided.",
        data_dir=_data_dir(),
    )
    tests = [line.strip() for line in valid_tests.splitlines() if line.strip()]
    contract = _contract_body(
        valid_domain=valid_domain,
        forbidden=forbidden,
        invalid_outcome=invalid_outcome,
        assumptions=assumptions,
    )
    _write_meta(
        record.id,
        {
            "valid_tests": tests,
            "environment": environment.strip()
            or "Ubuntu x86-64 VM (execution not implemented)",
            "contract": contract,
            "manifest_sha256": _sha_json(tests),
            "scope_sha256": contract["sha256"],
        },
    )
    return {"id": record.id, "original_sha256": record.original_sha256, "phase": record.phase}


@app.get("/api/jobs")
def list_job_summaries(limit: int = Query(50, ge=1, le=50)) -> dict[str, Any]:
    """List local jobs newest first, bounded to 50 records."""
    return {"jobs": jobs.list_jobs(data_dir=_data_dir(), limit=limit)}


@app.get("/api/jobs/{job_id}")
def get_snapshot(job_id: str) -> dict[str, Any]:
    record = _job_or_404(job_id)
    return _snapshot(record, _read_meta(job_id))


@app.post("/api/jobs/{job_id}/start", status_code=202)
def start(job_id: str, request: Request) -> dict[str, Any]:
    record = _job_or_404(job_id)
    if record.phase == "cancelled":
        raise HTTPException(status_code=409, detail="Job is cancelled; a new job is required.")
    meta = _read_meta(job_id)
    contract = meta.get("contract")
    approval_meta = meta.get("approval")
    if not isinstance(contract, dict):
        raise HTTPException(status_code=409, detail="Job has no contract.")
    if not contract.get("approved") or not isinstance(approval_meta, dict):
        raise HTTPException(status_code=409, detail="Contract is not approved.")
    revision = int(contract.get("revision", 1))
    if (
        int(approval_meta.get("revision", -1)) != revision
        or str(approval_meta.get("sha256", "")) != str(contract.get("sha256", ""))
    ):
        raise HTTPException(status_code=409, detail="Approval is stale.")

    row = jobs.get_contract(job_id, revision, data_dir=_data_dir())
    if row is None:
        raise HTTPException(status_code=409, detail="No persisted approval for this revision.")
    manifest = str(meta.get("manifest_sha256") or "0" * 64)
    try:
        approval = jobs.approval_from_contract(row, manifest_sha256=manifest)
        approval = jobs.Approval(
            job_id=approval.job_id,
            contract_id=approval.contract_id,
            contract_revision=approval.contract_revision,
            input_sha256=record.original_sha256,
            manifest_sha256=approval.manifest_sha256,
            scope_sha256=approval.scope_sha256,
            reviewer_label=approval.reviewer_label,
            approved_at=approval.approved_at,
        )
        key = (request.headers.get("idempotency-key") or "http-start-v1").strip()
        if not key or len(key) > 128:
            raise HTTPException(status_code=422, detail="Idempotency-Key must be 1-128 characters.")
        run = jobs.start_run(
            job_id,
            approval=approval,
            idempotency_key=key,
            data_dir=_data_dir(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "id": run.id,
        "run_id": run.id,
        "phase": run.phase,
        "created": run.created,
    }


@app.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: str) -> dict[str, Any]:
    try:
        record = jobs.cancel_job(job_id, data_dir=_data_dir())
    except ValueError as exc:
        missing = str(exc).startswith("unknown job id")
        raise HTTPException(
            status_code=404 if missing else 409,
            detail="Job not found." if missing else str(exc),
        ) from exc
    return {"id": record.id, "phase": record.phase}


class ApproveRequest(BaseModel):
    contract: dict[str, Any]
    contract_revision: int = Field(default=1, ge=1)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    reviewer_label: str = Field(min_length=1, max_length=128)


class RunRequest(BaseModel):
    contract_revision: int = Field(default=1, ge=1)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class RetryRequest(BaseModel):
    route: str = Field(pattern="^route_[ab]$")


@app.post("/api/jobs/{job_id}/contract")
def update_contract(
    job_id: str,
    expected_revision: int = Form(...),
    valid_domain: str = Form(""),
    forbidden: str = Form(""),
    invalid_outcome: str = Form(""),
    assumptions: str = Form(""),
) -> dict[str, Any]:
    _job_or_404(job_id)
    meta = _read_meta(job_id)
    contract = dict(meta.get("contract") or _contract_body())
    current = int(contract.get("revision", 1))
    if expected_revision != current:
        raise HTTPException(status_code=409, detail="Stale contract revision.")

    updated = _contract_body(
        valid_domain=valid_domain,
        forbidden=forbidden,
        invalid_outcome=invalid_outcome,
        assumptions=assumptions,
    )
    updated["revision"] = current + 1
    updated["approved"] = False
    updated["approved_at"] = None
    updated["reviewer_label"] = None
    meta["contract"] = updated
    meta.pop("approval", None)
    _write_meta(job_id, meta)
    return {"id": job_id, "contract": updated}


@app.post("/api/jobs/{job_id}/approve")
async def approve(job_id: str, request: Request) -> dict[str, Any]:
    record = _job_or_404(job_id)
    meta = _read_meta(job_id)
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        raw = await request.json()
        body = ApproveRequest.model_validate(raw)
        try:
            approval = jobs.approve_contract(
                job_id,
                contract_revision=body.contract_revision,
                reviewer_label=body.reviewer_label,
                data_dir=_data_dir(),
                contract_body=body.contract,
                input_sha256=record.original_sha256,
                manifest_sha256=body.manifest_sha256,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        contract = dict(body.contract)
        contract.update(
            {
                "revision": body.contract_revision,
                "sha256": approval.scope_sha256,
                "approved": True,
                "approved_at": approval.approved_at,
                "reviewer_label": body.reviewer_label,
            }
        )
        meta["contract"] = contract
        meta["manifest_sha256"] = body.manifest_sha256
    else:
        form = await request.form()
        expected = str(form.get("expected_sha256", ""))
        reviewer = str(form.get("reviewer_label", ""))
        contract = dict(meta.get("contract") or _contract_body())
        if not expected or expected != contract.get("sha256"):
            raise HTTPException(status_code=409, detail="Stale contract hash.")
        if not reviewer:
            raise HTTPException(status_code=422, detail="reviewer_label is required.")
        revision = int(contract.get("revision", 1))
        manifest = str(meta.get("manifest_sha256") or "0" * 64)
        try:
            approval = jobs.approve_contract(
                job_id,
                contract_revision=revision,
                reviewer_label=reviewer,
                data_dir=_data_dir(),
                input_sha256=record.original_sha256,
                manifest_sha256=manifest,
                scope_sha256=str(contract.get("sha256") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        contract.update(
            {
                "approved": True,
                "approved_at": approval.approved_at,
                "reviewer_label": reviewer,
            }
        )
        meta["contract"] = contract

    meta["approval"] = {
        "revision": int(meta["contract"]["revision"]),
        "sha256": str(meta["contract"]["sha256"]),
        "reviewer_label": str(meta["contract"].get("reviewer_label") or ""),
        "approved_at": str(meta["contract"].get("approved_at") or ""),
    }
    _write_meta(job_id, meta)
    return {
        "contract_id": approval.contract_id,
        "contract_revision": approval.contract_revision,
        "revision": int(meta["contract"]["revision"]),
        "sha256": str(meta["contract"]["sha256"]),
        "manifest_sha256": meta["manifest_sha256"] if "manifest_sha256" in meta else approval.manifest_sha256,
        "reviewer_label": str(meta["contract"].get("reviewer_label") or ""),
        "approved": True,
        "approved_at": approval.approved_at,
    }


@app.post("/api/jobs/{job_id}/runs")
def start_canonical(job_id: str, body: RunRequest) -> JSONResponse:
    record = _job_or_404(job_id)
    if record.phase == "cancelled":
        raise HTTPException(status_code=409, detail="Job is cancelled; a new job is required.")
    contract = jobs.get_contract(job_id, body.contract_revision, data_dir=_data_dir())
    if contract is None:
        raise HTTPException(status_code=409, detail="No approval for this contract revision.")
    try:
        approval = jobs.approval_from_contract(
            contract, manifest_sha256=body.manifest_sha256
        )
        approval = jobs.Approval(
            job_id=approval.job_id,
            contract_id=approval.contract_id,
            contract_revision=approval.contract_revision,
            input_sha256=record.original_sha256,
            manifest_sha256=approval.manifest_sha256,
            scope_sha256=approval.scope_sha256,
            reviewer_label=approval.reviewer_label,
            approved_at=approval.approved_at,
        )
        run = jobs.start_run(
            job_id,
            approval=approval,
            idempotency_key=body.idempotency_key,
            data_dir=_data_dir(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(
        {
            "id": run.id,
            "phase": run.phase,
            "created": run.created,
            "manifest_sha256": run.manifest_sha256,
            "reviewed_scope_sha256": run.reviewed_scope_sha256,
            "attempts": [
                {
                    "id": attempt.id,
                    "route": attempt.route,
                    "sequence": attempt.sequence,
                    "phase": attempt.phase,
                }
                for attempt in jobs.list_attempts(run.id, data_dir=_data_dir())
            ],
        },
        status_code=202 if run.created else 200,
    )


@app.post("/api/runs/{run_id}/attempts/retry", status_code=202)
def retry(run_id: str, body: RetryRequest) -> dict[str, Any]:
    try:
        attempt = jobs.retry_attempt(run_id, body.route, data_dir=_data_dir())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": attempt.id,
        "run_id": attempt.run_id,
        "route": attempt.route,
        "sequence": attempt.sequence,
        "phase": attempt.phase,
    }


_GATE_BY_KIND = {
    "identity": "V0",
    "structure": "V1",
    "interface": "V2",
    "behaviour": "V3",
    "symbolic": "V4",
    "reproducibility": "V5",
}
_GATE_ORDER = {gate: index for index, gate in enumerate(("V0", "V1", "V2", "V3", "V4", "V5"))}


def _gate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render the persisted rows as V0-V5 rows for API consumers."""
    return [
        {
            "gate": _GATE_BY_KIND.get(row["kind"], row["kind"]),
            "kind": row["kind"],
            "scope": row["scope"],
            "required": row["required"],
            "result": row["result"],
            "detail": row["detail"],
            "evidence_artifact_id": row["evidence_artifact_id"],
        }
        for row in rows
    ]


def _sorted_gate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: _GATE_ORDER.get(row["gate"], len(_GATE_ORDER)))


def _v3_counts_from_artifact(
    row: dict[str, Any], *, data_dir: str | Path
) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    """Return valid/invalid passed/total from the V3 artifact.

    Missing artifact or an unrecognised manifest yields ``None`` for each
    count; the endpoint never invents zero from an absent file.
    """
    artifact_id = row.get("evidence_artifact_id")
    if not artifact_id:
        return None, None, None, None, None
    artifact = jobs.get_artifact(artifact_id, data_dir=data_dir)
    if artifact is None:
        return None, None, None, None, None
    root = Path(data_dir).resolve()
    path = (root / artifact["relative_path"]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None, None, None, None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, None, None, None
    cases = payload.get("cases")
    if not isinstance(cases, list):
        cases = payload.get("tests")
    if not isinstance(cases, list):
        return None, None, None, None, None
    valid = [case for case in cases if isinstance(case, dict) and case.get("kind") == "valid"]
    invalid = [case for case in cases if isinstance(case, dict) and case.get("kind") == "invalid"]
    if not valid and not invalid:
        return None, None, None, None, None
    valid_passed = sum(1 for case in valid if case.get("ok") is True or case.get("passed") is True)
    invalid_passed = sum(1 for case in invalid if case.get("ok") is True or case.get("passed") is True)
    detail = row.get("detail") or ""
    mode_match = re.search(r"input_mode\s*=\s*(none|argv|stdin)", detail)
    return (
        valid_passed,
        len(valid),
        invalid_passed,
        len(invalid),
        mode_match.group(1) if mode_match else None,
    )


def _v3_summary(rows: list[dict[str, Any]], *, data_dir: str | Path) -> dict[str, Any] | None:
    row = next((item for item in rows if item["kind"] == "behaviour"), None)
    if row is None:
        return None
    valid_passed, valid_total, invalid_passed, invalid_total, mode = _v3_counts_from_artifact(
        row, data_dir=data_dir
    )
    if mode is None:
        mode_match = re.search(r"input_mode\s*=\s*(none|argv|stdin)", row.get("detail") or "")
        mode = mode_match.group(1) if mode_match else None
    # A no-input run has no invalid cases by construction and may have no V3
    # manifest at all. Report zero counts and "none" rather than unknown: the
    # gate result still comes from the row, so this never turns not_run into a
    # pass, and a client no longer has to parse a null pair.
    if valid_total is None:
        valid_passed, valid_total = 0, 0
    if invalid_total is None:
        invalid_passed, invalid_total = 0, 0
    if mode is None:
        mode = "none"
    invalid_applicable = None if invalid_total is None else invalid_total > 0
    return {
        "gate": "V3",
        "kind": "behaviour",
        "result": row["result"],
        "input_mode": mode,
        "valid": {"passed": valid_passed, "total": valid_total},
        "invalid": {"passed": invalid_passed, "total": invalid_total},
        "invalid_applicable": invalid_applicable,
    }


@app.get("/api/attempts/{attempt_id}/evidence")
def attempt_evidence(attempt_id: str) -> dict[str, Any]:
    attempt = jobs.get_attempt(attempt_id, data_dir=_data_dir())
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    rows = jobs.list_obligations(attempt.id, data_dir=_data_dir())
    return {
        "attempt_id": attempt.id,
        "run_id": attempt.run_id,
        "route": attempt.route,
        "sequence": attempt.sequence,
        "outcome": attempt.outcome,
        "reason": attempt.reason,
        "failing_obligation": attempt.failing_obligation,
        "detail": attempt.detail,
        "evidence": _evidence_state(rows),
        "gates": _sorted_gate_rows(_gate_rows(rows)),
        "v3": _v3_summary(rows, data_dir=_data_dir()),
        "obligations": _obligation_groups(rows, _route_key(attempt.route)),
    }


def _duration_ms(started_at: Any, finished_at: Any) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = finish - start
    return max(0, int(delta.total_seconds() * 1000))


def _timeline_status(row: dict[str, Any]) -> str:
    """Normalise an attempt phase/outcome for the timeline, without inventing ok."""
    phase = str(row.get("status") or "")
    outcome = str(row.get("outcome") or "")
    reason = str(row.get("reason") or "").lower()
    if "timeout" in reason or "timed_out" in reason:
        return "timeout"
    if phase == "cancelled":
        return "cancelled"
    if phase != "finished":
        return "unknown"
    if outcome in {"success", "success_within_scope"}:
        return "ok"
    if outcome in {"failed", "rejected", "error"}:
        return "failed"
    return "unknown"


@app.get("/api/jobs/{job_id}/attempts")
def job_attempts(job_id: str) -> dict[str, Any]:
    """Every attempt id for this job, oldest first.

    ``available`` distinguishes "the job was read and has no attempts" from a
    missing job (404). The legacy fields are kept; timeline-specific fields are
    additive. ``exit_code`` is null when the store does not record one — never
    invented as 0.
    """
    _job_or_404(job_id)
    rows = jobs.list_job_attempts(job_id, data_dir=_data_dir())
    attempts = []
    for row in rows:
        item = dict(row)
        item["route_label"] = "repaired" if row.get("route") == "route_b" else "direct"
        item["timeline_status"] = _timeline_status(row)
        item["duration_ms"] = _duration_ms(row.get("started_at"), row.get("finished_at"))
        item["exit_code"] = None
        attempts.append(item)
    return {"available": True, "job_id": job_id, "attempts": attempts}


@app.get("/api/jobs/{job_id}/runs/{run_id}/evaluation")
def run_evaluation(job_id: str, run_id: str) -> dict[str, Any]:
    """Evaluation for one authorized historical run.

    The normal snapshot is latest-run only. This route lets the browser read a
    run the job still owns without pretending it is the current one.
    """
    _job_or_404(job_id)
    connection = connect(_data_dir())
    try:
        run = connection.execute(
            "SELECT * FROM runs WHERE id = ? AND job_id = ?", (run_id, job_id)
        ).fetchone()
    finally:
        connection.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found for this job.")

    attempts = jobs.list_attempts(run_id, data_dir=_data_dir())
    obligations: list[dict[str, Any]] = []
    for attempt in attempts:
        for row in jobs.list_obligations(attempt.id, data_dir=_data_dir()):
            obligations.append(
                {
                    "route": attempt.route,
                    "level": _GATE_BY_KIND.get(str(row["kind"]), str(row["kind"])),
                    "status": row["result"],
                    "reason_code": row["detail"] or None,
                }
            )
    finalized = str(run["phase"]) in {"finished", "cancelled"} and bool(run["outcome"])
    limitations: list[dict[str, str]] = []
    if not finalized:
        limitations.append(
            {
                "code": "evaluation_pending",
                "detail": "This run has not committed a finalized evaluation.",
            }
        )
    if any(row["status"] != "passed" for row in obligations):
        limitations.append(
            {
                "code": "checks_incomplete",
                "detail": "One or more recorded checks did not pass and are not evidence.",
            }
        )
    return {
        "job_id": job_id,
        "run_id": run_id,
        "available": bool(attempts or run["outcome"]),
        "finalized": finalized,
        "demo": False,
        "data_source": "controller-http",
        "verdict": {
            "outcome": run["outcome"] if finalized else None,
            "reason": run["reason"] if finalized else None,
        },
        "obligations": obligations,
        "limitations": limitations,
    }


@app.get("/api/jobs/{job_id}/obligations")
def obligations(job_id: str) -> dict[str, list[dict[str, Any]]]:
    _job_or_404(job_id)
    return _job_obligation_groups(job_id)


@app.get("/api/jobs/{job_id}/visuals")
def visuals(job_id: str) -> dict[str, Any]:
    _job_or_404(job_id)
    rows = [
        row
        for row in jobs.list_artifacts(job_id, data_dir=_data_dir())
        if row.get("role") in ANALYSIS_ROLES
    ]
    if not rows:
        reason = "no analysis artifact is registered for this job"
        return {
            "available": False,
            "synthetic": False,
            "reason": reason,
            "note": reason,
            "sources": [],
            "routes": {},
        }
    return {
        "available": True,
        "synthetic": False,
        "reason": "",
        "note": "",
        "routes": {},
        "sources": [
            {
                "id": row["id"],
                "role": row["role"],
                "media_type": row["media_type"],
                "sha256": row["sha256"],
                "content_url": f"/api/jobs/{job_id}/artifacts/{row['id']}/download",
            }
            for row in rows
        ],
    }


@app.get("/api/jobs/{job_id}/artifacts")
def artifacts(job_id: str) -> dict[str, list[dict[str, Any]]]:
    _job_or_404(job_id)
    rows = jobs.list_artifacts(job_id, data_dir=_data_dir())
    groups: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    for row in rows:
        meta = _artifact_metadata(row)
        key = "B" if row.get("route") == "route_b" else "A"
        groups[key].append(meta)
    return groups


DELIVERABLE_MAX_BYTES = 64 * 1024 * 1024


@app.post("/api/jobs/{job_id}/deliverables", status_code=201)
async def upload_deliverable(job_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Store an operator-supplied repaired ELF for download.

    This is deliberately not acceptance evidence: it creates an
    ``operator_deliverable`` artifact only. It never writes an obligation, never
    changes an attempt/run outcome, and never executes the bytes.
    """
    _job_or_404(job_id)
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    if len(payload) > DELIVERABLE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file is too large.")
    if not payload.startswith(b"\x7fELF"):
        raise HTTPException(
            status_code=400,
            detail="Uploaded deliverable is not an ELF binary (missing \\x7fELF magic).",
        )
    raw_filename = file.filename or "repaired.elf"
    parts = re.split(r"[\\/]+", raw_filename)
    if raw_filename.startswith(("/", "\\")) or ".." in parts:
        raise HTTPException(status_code=400, detail="Uploaded filename must be a plain basename.")
    filename = _ascii_filename(raw_filename)
    relative = Path("jobs") / job_id / "deliverables" / f"{uuid.uuid4().hex[:12]}-{filename}"
    target = _data_dir() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    row = jobs.add_artifact(
        job_id,
        relative_path=relative.as_posix(),
        role="operator_deliverable",
        data_dir=_data_dir(),
        media_type="application/x-elf",
    )
    return {
        "artifact": _artifact_metadata(row, verify_hash=True),
        "note": "Stored for download only; not acceptance evidence and never executed.",
    }


@app.get("/api/jobs/{job_id}/artifacts/{artifact_id}")
def job_artifact(job_id: str, artifact_id: str) -> dict[str, Any]:
    return _artifact_metadata(_job_artifact_or_404(job_id, artifact_id), verify_hash=True)


@app.get("/api/jobs/{job_id}/artifacts/{artifact_id}/download")
def download_job_artifact(job_id: str, artifact_id: str) -> FileResponse:
    return _verified_artifact_response(job_id, artifact_id)


@app.get("/api/jobs/{job_id}/analysis")
def get_analysis(job_id: str) -> dict[str, Any]:
    """Screening state: ``available`` / ``requested`` / ``not_provided``."""
    record = _job_or_404(job_id)
    return _analysis_payload(job_id, record)


@app.post("/api/jobs/{job_id}/analysis")
def request_analysis(job_id: str) -> JSONResponse:
    """Record a screening request.

    This build has no screening executor, so an accepted request is recorded and
    reported as ``requested``; it never fabricates an analysis. Once M3's executor
    runs and M5's ingest registers ``analysis_*`` artifacts, ``GET`` moves to
    ``available`` without an endpoint change. The call is idempotent: an existing
    request (or existing artifacts) returns 200 instead of starting a second one.
    """
    record = _job_or_404(job_id)
    payload = _analysis_payload(job_id, record)
    if payload["status"] in {"available", "requested"}:
        return JSONResponse(payload, status_code=200)
    if record.phase == "cancelled":
        raise HTTPException(status_code=409, detail="A cancelled job cannot be screened.")

    meta = _read_meta(job_id)
    contract = meta.get("contract") or {}
    approved = bool(meta.get("approval")) or bool(contract.get("approved"))
    if not approved:
        raise HTTPException(
            status_code=409,
            detail="Approve the repair contract before screening the original.",
        )
    if jobs.latest_run(record.id, data_dir=_data_dir()) is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A repair run already exists for this job; request screening before "
                "starting the run."
            ),
        )

    meta["analysis"] = {"status": "requested", "requested_at": _now()}
    _write_meta(job_id, meta)
    return JSONResponse(_analysis_payload(job_id, record), status_code=202)


@app.get("/api/jobs/{job_id}/comparison")
def get_comparison(job_id: str) -> dict[str, Any]:
    """Original-vs-candidate behavior comparison, only from an ingested artifact."""
    _job_or_404(job_id)
    return _comparison_payload(job_id)


@app.get("/api/jobs/{job_id}/v3")
def get_v3(job_id: str) -> dict[str, Any]:
    """V3 round-trip vs repaired-behaviour, as two separate kinds."""
    _job_or_404(job_id)
    return _v3_payload(job_id)


class AssistantPlanRequest(BaseModel):
    """Optional planner mode for POST /assistant.

    Omitting ``mode`` keeps the server default (``BINARYREPAIR_ASSISTANT_MODE``,
    scripted when unset).  The mode only selects how the plan is produced.
    """

    mode: str | None = Field(
        default=None, pattern="^(scripted|deterministic|llm)$"
    )


@app.get("/api/jobs/{job_id}/assistant")
def get_assistant(job_id: str) -> dict[str, Any]:
    """The latest stored plan.  A plan is advice, never a decision."""
    _job_or_404(job_id)
    from app.services.assistant import get_plan

    record = get_plan(job_id, data_dir=_data_dir())
    if record is None:
        raise HTTPException(status_code=404, detail="No assistant plan for this job.")
    return _assistant_payload(record)


@app.post("/api/jobs/{job_id}/assistant")
def generate_assistant(
    job_id: str, payload: AssistantPlanRequest | None = None
) -> dict[str, Any]:
    """Generate and persist a plan.

    This writes exactly one ``assistant_plans`` row.  It cannot approve the
    contract, start a run, or write a verdict: the plan is guidance the user
    still has to act on through the normal endpoints.
    """
    _job_or_404(job_id)
    from app.services.assistant import generate_plan, get_plan

    try:
        generate_plan(
            job_id=job_id,
            data_dir=_data_dir(),
            mode=_planner_mode(payload.mode if payload is not None else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # ``generate_plan`` returns the plan it built; the stored record carries the
    # row id, version and message link the UI needs, so read it back by rowid.
    record = get_plan(job_id, data_dir=_data_dir())
    if record is None:
        raise HTTPException(status_code=500, detail="Plan was not persisted.")
    return _assistant_payload(record)


class StandalonePlanRequest(BaseModel):
    """Ask for a plan without requiring a job row.

    ``description`` is the defect text. When ``job_id`` is present the plan is
    stored against that job; otherwise the plan is returned as a preview and
    nothing is written, because an ``assistant_plans`` row requires a job id.
    """

    description: str | None = Field(default=None, max_length=2000)
    context: dict[str, Any] | None = None
    job_id: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(
        default=None, pattern="^(scripted|deterministic|llm)$"
    )


def _planner_mode(mode: str | None) -> str | None:
    """Map the older ``scripted`` label onto M5's ``deterministic``."""
    if mode == "scripted":
        return "deterministic"
    return mode


def _agent_block(plan: Any) -> dict[str, Any]:
    """How the plan was produced, flattened for M2.

    ``mode`` reports what actually ran, ``fallback`` says whether the LLM path
    degraded, and ``tokens`` is the M5 ``{prompt, completion}`` shape — not the
    ``ai_usage`` ``{input_tokens, output_tokens}`` shape used for job calls.
    """
    return {
        "mode": plan.mode,
        "fallback": plan.fallback,
        "fallback_reason": plan.fallback_reason,
        "model": plan.model,
        "tokens": plan.tokens,
    }


def _assistant_unavailable(reason: str) -> JSONResponse:
    """The agreed 503 body for a plan that cannot be produced.

    A caller has to be able to tell "no plan" apart from "a deterministic plan
    dressed up as the LLM result", so this is an error response with an explicit
    code, not a 200 carrying a ``fallback`` flag.
    """
    return JSONResponse(
        status_code=503,
        content={
            "plan": None,
            "error": "assistant_unavailable",
            "fallback_reason": reason,
        },
    )


@app.post("/api/assistant/plan")
def plan_from_description(payload: StandalonePlanRequest) -> Any:
    """Build a plan from free text, with or without a job.

    Contract::

        200 {"plan": {...}}
        503 {"plan": null, "error": "assistant_unavailable",
             "fallback_reason": "..."}

    A plan is advice. This endpoint writes at most one ``assistant_plans`` row
    and never touches contracts, runs, attempts, obligations or a verdict.

    ``mode="llm"`` answers 503 when no client is configured; it never returns a
    deterministic plan labelled as the LLM path. Omitting ``mode`` asks for the
    deterministic planner, which is always available and is a different answer,
    not a substitute one.

    ``context`` is accepted as free-form caller state for forward
    compatibility. The deterministic planner does not read it, and a job id makes
    the plan use the job's stored context instead, so no caller can shift what
    the plan sees. Accepting a field it ignores is recorded here rather than
    implied by a passing status code.
    """
    from app.domain.assistant import deterministic_plan
    from app.services.assistant import generate_plan, get_plan

    mode = _planner_mode(payload.mode)

    if payload.job_id:
        _job_or_404(payload.job_id)
        if mode == "llm":
            return _assistant_unavailable(
                "no LLM client is configured on this host"
            )
        try:
            plan = generate_plan(
                payload.description,
                job_id=payload.job_id,
                data_dir=_data_dir(),
                mode="deterministic",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record = get_plan(payload.job_id, data_dir=_data_dir())
        if record is None:
            raise HTTPException(status_code=500, detail="Plan was not persisted.")
        return {
            "job_id": payload.job_id,
            "persisted": True,
            "plan": record["plan"],
            "agent": _agent_block(plan),
        }

    description = (payload.description or "").strip()
    if not description:
        raise HTTPException(
            status_code=422, detail="description or job_id is required."
        )
    if mode == "llm":
        return _assistant_unavailable("no LLM client is configured on this host")

    plan = deterministic_plan(description)
    return {
        "plan": plan.model_dump(),
        "job_id": None,
        "persisted": False,
        "agent": _agent_block(plan),
    }


class ProposalRequest(BaseModel):
    """A provider repair suggestion.

    Staging stores the text as input. It is not a candidate and it is never
    evaluated here; a route still has to produce candidate bytes and those bytes
    still have to pass V0-V5.
    """

    text: str = Field(min_length=1, max_length=70000)
    source: str = Field(default="provider", max_length=64)
    model: str | None = Field(default=None, max_length=200)


@app.get("/api/jobs/{job_id}/proposals")
def list_job_proposals(job_id: str) -> dict[str, Any]:
    """Staged provider proposals for this job, newest first."""
    _job_or_404(job_id)
    from app.services.candidate_staging import list_proposals

    return {"proposals": list_proposals(job_id, data_dir=_data_dir())}


@app.post("/api/jobs/{job_id}/proposals")
def stage_job_proposal(job_id: str, payload: ProposalRequest) -> dict[str, Any]:
    """Stage provider text as input.

    The response reports ``status: staged`` and ``candidate: false``. No run,
    attempt, obligation, or verdict is created, so a model suggestion can never
    read as a successful repair.
    """
    _job_or_404(job_id)
    from app.services.candidate_staging import stage_proposal

    try:
        return stage_proposal(
            job_id,
            data_dir=_data_dir(),
            text=payload.text,
            source=payload.source,
            model=payload.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> dict[str, Any]:
    row = jobs.get_artifact(artifact_id, data_dir=_data_dir())
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return _artifact_metadata(row, verify_hash=True)


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str) -> FileResponse:
    row = jobs.get_artifact(artifact_id, data_dir=_data_dir())
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return _verified_artifact_response(str(row.get("job_id") or ""), artifact_id)
