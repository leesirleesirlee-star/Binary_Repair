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
    GET  /api/artifacts/{id}               artifact metadata by id
    GET  /api/artifacts/{id}/download      registered artifact bytes

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
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
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


def _artifact_metadata(row: dict[str, Any]) -> dict[str, Any]:
    path = _artifact_file(row)
    return {
        "id": row["id"],
        "name": path.name if path else Path(str(row["relative_path"])).name,
        "role": row["role"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "media_type": row["media_type"],
        "available": path is not None,
        "content_url": f"/api/artifacts/{row['id']}/download",
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
            "changed_regions": [],
            "manual_interventions": 0,
            "ai_usage": "unavailable",
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


def _snapshot(record: jobs.JobRecord, meta: dict[str, Any]) -> dict[str, Any]:
    contract = dict(meta.get("contract") or _contract_body())
    contract.setdefault("revision", 1)
    contract.setdefault("sha256", _sha_json({k: v for k, v in contract.items() if k not in {"revision", "sha256", "approved", "approved_at", "reviewer_label"}}))
    contract.setdefault("approved", False)

    routes = _placeholder_routes(record.phase)
    run = jobs.latest_run(record.id, data_dir=_data_dir())
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

    return {
        "id": record.id,
        "demo": False,
        "data_source": "controller-http",
        "execution": "not_started" if record.phase == "draft" else record.phase,
        "filename": record.filename,
        "original_sha256": record.original_sha256,
        "defect": record.defect_description,
        "valid_tests": meta.get("valid_tests", []),
        "environment": meta.get("environment", ""),
        "created_at": record.created_at,
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

    filename = _ascii_filename(binary.filename or "sample.bin")
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


@app.get("/api/jobs/{job_id}")
def get_snapshot(job_id: str) -> dict[str, Any]:
    record = _job_or_404(job_id)
    return _snapshot(record, _read_meta(job_id))


@app.post("/api/jobs/{job_id}/start", status_code=202)
def start(job_id: str) -> dict[str, Any]:
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
        run = jobs.start_run(
            job_id,
            approval=approval,
            idempotency_key="http-start-v1",
            data_dir=_data_dir(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"id": run.id, "phase": run.phase}


@app.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: str) -> dict[str, Any]:
    try:
        record = jobs.cancel_job(job_id, data_dir=_data_dir())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
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
        "obligations": _obligation_groups(rows, _route_key(attempt.route)),
    }


@app.get("/api/jobs/{job_id}/obligations")
def obligations(job_id: str) -> dict[str, list[dict[str, Any]]]:
    _job_or_404(job_id)
    return _job_obligation_groups(job_id)


@app.get("/api/jobs/{job_id}/visuals")
def visuals(job_id: str) -> dict[str, Any]:
    _job_or_404(job_id)
    return {
        "available": False,
        "note": "Repair execution is not implemented by the controller yet.",
    }


@app.get("/api/jobs/{job_id}/artifacts")
def artifacts(job_id: str) -> dict[str, list[dict[str, Any]]]:
    _job_or_404(job_id)
    rows = jobs.list_artifacts(job_id, data_dir=_data_dir())
    if not rows:
        record = _job_or_404(job_id)
        return {
            "A": [
                {
                    "id": "",
                    "name": record.filename,
                    "role": "original binary",
                    "sha256": record.original_sha256,
                    "size_bytes": len(record.original_bytes),
                    "available": False,
                }
            ],
            "B": [],
        }

    groups: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    for row in rows:
        meta = _artifact_metadata(row)
        route = row.get("route")
        key = "B" if route == "route_b" else "A"
        groups[key].append(meta)
    return groups


@app.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> dict[str, Any]:
    row = jobs.get_artifact(artifact_id, data_dir=_data_dir())
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return _artifact_metadata(row)


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str) -> FileResponse:
    row = jobs.get_artifact(artifact_id, data_dir=_data_dir())
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    path = _artifact_file(row)
    if path is None:
        raise HTTPException(status_code=404, detail="Artifact content is unavailable.")
    return FileResponse(
        path,
        media_type=row.get("media_type") or "application/octet-stream",
        filename=path.name,
        content_disposition_type="attachment",
    )
