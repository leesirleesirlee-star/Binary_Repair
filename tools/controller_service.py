"""Controller HTTP service backed by the real Member 1 job store.

This is the bridge the GUI talks to in ``http`` mode. It is deliberately honest:

- job identity is persisted by ``app/services/jobs.py`` (SQLite + original.bin),
  so snapshots report ``demo: false``;
- contract revision / approval are stored as a sidecar ``meta.json`` and bound
  to the revision hash (stale revision or stale hash -> 409);
- run execution is *not* implemented yet: ``start`` returns 409 with an explicit
  message instead of pretending a repair happened.

Replace this bridge with the final controller service once Member 1 exposes one;
the response contract is documented in ``docs/controller-integration.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile

from app.services.jobs import create_job, get_job

CONTRACT_FIELDS = ("valid_domain", "forbidden", "invalid_outcome", "assumptions")
CONTRACT_DEFAULTS = {
    "valid_domain": "Input length 0-64 bytes; out-of-range lengths rejected",
    "forbidden": "Out-of-bounds write to the target buffer (more than 8 bytes)",
    "invalid_outcome": "Return error code 1 without out-of-bounds access or crash",
    "assumptions": ["Single process", "Linux x86-64 ELF", "Stack protector disabled"],
}
STAGE_NAMES = ["screening", "analyzing", "roundtrip", "generating", "applying", "validating"]
ROUTE_NAMES = {
    "A": "Route A - Direct instruction repair",
    "B": "Route B - Recovered C reintegration",
}


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    root = Path(data_dir or os.environ.get("BINARYREPAIR_CONTROLLER_DATA", "var/controller"))

    app = FastAPI(title="BinaryRepair controller bridge", version="0.1.0")
    app.state.data_dir = root

    def _dir(request: Request) -> Path:
        return Path(request.app.state.data_dir)

    def _meta_path(request: Request, job_id: str) -> Path:
        return _dir(request) / "jobs" / job_id / "meta.json"

    def _read_meta(request: Request, job_id: str) -> dict[str, Any]:
        path = _meta_path(request, job_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_meta(request: Request, job_id: str, meta: dict[str, Any]) -> None:
        path = _meta_path(request, job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")

    def _contract_body(
        *,
        valid_domain: str = "",
        forbidden: str = "",
        invalid_outcome: str = "",
        assumptions: str = "",
    ) -> dict[str, Any]:
        return {
            "valid_domain": valid_domain.strip() or CONTRACT_DEFAULTS["valid_domain"],
            "forbidden": forbidden.strip() or CONTRACT_DEFAULTS["forbidden"],
            "invalid_outcome": invalid_outcome.strip() or CONTRACT_DEFAULTS["invalid_outcome"],
            "assumptions": [
                line.strip()
                for line in (assumptions or "\n".join(CONTRACT_DEFAULTS["assumptions"])).splitlines()
                if line.strip()
            ],
        }

    def _contract_sha(body: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    def _new_contract(body: dict[str, Any], revision: int = 1) -> dict[str, Any]:
        return {
            "revision": revision,
            "sha256": _contract_sha(body),
            "approved": False,
            "approved_at": None,
            "reviewer_label": None,
            **body,
        }

    def _require_job(request: Request, job_id: str):
        record = get_job(job_id, data_dir=_dir(request))
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return record

    def _route() -> dict[str, Any]:
        return {
            "name": "Route",
            "phase": "draft",
            "outcome": None,
            "outcome_reason": "",
            "stages": [{"name": name, "state": "pending"} for name in STAGE_NAMES],
            "tests": {"passed": 0, "total": 0},
            "verification": "not_run",
            "compatibility": "not_run",
            "changed_regions": [],
            "manual_interventions": 0,
            "ai_usage": "unavailable",
            "elapsed_s": 0.0,
        }

    def _snapshot(request: Request, job_id: str) -> dict[str, Any]:
        record = _require_job(request, job_id)
        meta = _read_meta(request, job_id)
        contract = meta.get("contract") or _new_contract(_contract_body())
        routes: dict[str, Any] = {}
        for key in ("A", "B"):
            route = _route()
            route["name"] = ROUTE_NAMES[key]
            routes[key] = route
        return {
            "id": record.id,
            "demo": False,
            "filename": record.filename,
            "original_sha256": record.original_sha256,
            "defect": record.defect_description,
            "valid_tests": meta.get("valid_tests", ""),
            "environment": meta.get("environment", ""),
            "created_at": record.created_at,
            "run_started_at": None,
            "cancelled": bool(meta.get("cancelled", False)),
            "execution": "not_started",
            "phase": record.phase,
            "contract": contract,
            "routes": routes,
        }

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "controller",
            "demo": False,
            "execution": "not_started",
        }

    @app.post("/api/jobs", status_code=201)
    async def create(
        request: Request,
        binary: UploadFile = File(...),
        defect: str = Form(...),
        valid_tests: str = Form(""),
        environment: str = Form(""),
        valid_domain: str = Form(""),
        forbidden: str = Form(""),
        invalid_outcome: str = Form(""),
        assumptions: str = Form(""),
    ) -> dict[str, Any]:
        raw = await binary.read()
        if not raw:
            raise HTTPException(status_code=422, detail="Uploaded file is empty.")
        for name, value in {
            "defect": defect,
            "valid_tests": valid_tests,
            "environment": environment,
            "valid_domain": valid_domain,
            "forbidden": forbidden,
            "invalid_outcome": invalid_outcome,
            "assumptions": assumptions,
        }.items():
            if not value.isascii():
                raise HTTPException(
                    status_code=422,
                    detail=f"Field '{name}' must be English ASCII (language policy).",
                )
        filename = Path(binary.filename or "sample.bin").name
        if not filename.isascii():
            filename = "sample.bin"
        record = create_job(
            original_bytes=raw,
            filename=filename,
            defect_description=defect,
            data_dir=_dir(request),
        )
        body = _contract_body(
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        )
        _write_meta(
            request,
            record.id,
            {
                "contract": _new_contract(body),
                "valid_tests": valid_tests,
                "environment": environment,
                "assumptions": assumptions,
                "cancelled": False,
            },
        )
        return {"id": record.id, "original_sha256": record.original_sha256}

    @app.get("/api/jobs/{job_id}")
    async def snapshot(request: Request, job_id: str) -> dict[str, Any]:
        return _snapshot(request, job_id)

    @app.post("/api/jobs/{job_id}/contract")
    async def update_contract(
        request: Request,
        job_id: str,
        expected_revision: str = Form(""),
        valid_domain: str = Form(""),
        forbidden: str = Form(""),
        invalid_outcome: str = Form(""),
        assumptions: str = Form(""),
    ) -> dict[str, Any]:
        _require_job(request, job_id)
        meta = _read_meta(request, job_id)
        contract = meta.get("contract") or _new_contract(_contract_body())
        current = contract["revision"]
        if str(expected_revision).strip() != str(current):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Contract revision is stale: presented "
                    f"{expected_revision}, current is {current}. Reload and review the current contract."
                ),
            )
        body = _contract_body(
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        )
        meta["contract"] = _new_contract(body, revision=current + 1)
        _write_meta(request, job_id, meta)
        return meta["contract"]

    @app.post("/api/jobs/{job_id}/approve")
    async def approve_contract(
        request: Request,
        job_id: str,
        expected_sha256: str = Form(""),
        reviewer_label: str = Form(""),
    ) -> dict[str, Any]:
        _require_job(request, job_id)
        meta = _read_meta(request, job_id)
        contract = meta.get("contract") or _new_contract(_contract_body())
        if expected_sha256.strip().lower() != contract["sha256"]:
            raise HTTPException(
                status_code=409,
                detail="Contract approval is stale: the presented hash does not match the current revision.",
            )
        if not reviewer_label.isascii():
            raise HTTPException(
                status_code=422,
                detail="Field 'reviewer_label' must be English ASCII (language policy).",
            )
        contract["approved"] = True
        contract["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        contract["reviewer_label"] = reviewer_label.strip() or "reviewer"
        meta["contract"] = contract
        _write_meta(request, job_id, meta)
        return contract

    @app.post("/api/jobs/{job_id}/start")
    async def start(request: Request, job_id: str) -> dict[str, Any]:
        _require_job(request, job_id)
        meta = _read_meta(request, job_id)
        contract = meta.get("contract") or {}
        if not contract.get("approved"):
            raise HTTPException(status_code=409, detail="Contract is not approved for this job.")
        raise HTTPException(
            status_code=409,
            detail="Execution is not implemented by this controller yet.",
        )

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(request: Request, job_id: str) -> dict[str, Any]:
        _require_job(request, job_id)
        raise HTTPException(
            status_code=409,
            detail="Cancellation is not implemented by this controller yet.",
        )

    @app.get("/api/jobs/{job_id}/obligations")
    async def obligations(request: Request, job_id: str) -> dict[str, list[Any]]:
        _require_job(request, job_id)
        return {"A": [], "B": []}

    @app.get("/api/jobs/{job_id}/artifacts")
    async def artifacts(request: Request, job_id: str) -> dict[str, list[Any]]:
        _require_job(request, job_id)
        return {"A": [], "B": []}

    @app.get("/api/jobs/{job_id}/visuals")
    async def visuals(request: Request, job_id: str) -> dict[str, Any]:
        _require_job(request, job_id)
        return {
            "available": False,
            "note": "Execution is not implemented; no disassembly or recovered-C artifacts yet.",
        }

    return app


app = create_app()
