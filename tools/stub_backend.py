"""Stub controller for local GUI development.

Serves synthetic data from app/demo.py so the GUI http mode can be used
before the real controller is available. The GUI still shows demo=true."""

from __future__ import annotations

import hashlib

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app import demo, visuals

app = FastAPI(title="BinaryRepair stub controller (synthetic)")


def _job_or_404(job_id: str) -> dict:
    job = demo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.post("/api/jobs")
async def create_job(
    binary: UploadFile = File(...),
    defect: str = Form(""),
    valid_tests: str = Form(""),
    environment: str = Form(""),
    valid_domain: str = Form(""),
    forbidden: str = Form(""),
    invalid_outcome: str = Form(""),
    assumptions: str = Form(""),
):
    raw = await binary.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    job = demo.create_job(
        original_sha256=hashlib.sha256(raw).hexdigest(),
        filename=binary.filename or "sample.bin",
        defect=defect,
        valid_tests=valid_tests,
        environment=environment,
        valid_domain=valid_domain,
        forbidden=forbidden,
        invalid_outcome=invalid_outcome,
        assumptions=assumptions,
    )
    return JSONResponse(
        {"id": job["id"], "original_sha256": job["original_sha256"]},
        status_code=201,
    )


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return demo.snapshot(_job_or_404(job_id))


@app.post("/api/jobs/{job_id}/start")
async def start_job(job_id: str):
    job = _job_or_404(job_id)
    if not job["contract"].get("approved"):
        raise HTTPException(status_code=409, detail="Contract is not approved for this job.")
    demo.start(job)
    return JSONResponse({"ok": True}, status_code=202)


@app.post("/api/jobs/{job_id}/contract")
async def update_contract(
    job_id: str,
    expected_revision: str = Form(""),
    valid_domain: str = Form(""),
    forbidden: str = Form(""),
    invalid_outcome: str = Form(""),
    assumptions: str = Form(""),
):
    job = _job_or_404(job_id)
    current = job["contract"]["revision"]
    if str(expected_revision).strip() != str(current):
        raise HTTPException(
            status_code=409,
            detail=(
                "Contract revision is stale: presented "
                f"{expected_revision}, current is {current}. "
                "Reload and review the current contract."
            ),
        )
    return demo.update_contract(
        job,
        valid_domain=valid_domain,
        forbidden=forbidden,
        invalid_outcome=invalid_outcome,
        assumptions=assumptions,
    )


@app.post("/api/jobs/{job_id}/approve")
async def approve_contract(
    job_id: str,
    expected_sha256: str = Form(""),
    reviewer_label: str = Form(""),
):
    job = _job_or_404(job_id)
    if expected_sha256.strip().lower() != job["contract"]["sha256"]:
        raise HTTPException(
            status_code=409,
            detail="Contract approval is stale: the presented hash does not match the current revision.",
        )
    return demo.approve_contract(job, reviewer_label=reviewer_label)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    demo.cancel(_job_or_404(job_id))
    return JSONResponse({"ok": True}, status_code=202)


@app.get("/api/jobs/{job_id}/obligations")
async def get_obligations(job_id: str):
    return demo.obligations(_job_or_404(job_id))


@app.get("/api/jobs/{job_id}/artifacts")
async def get_artifacts(job_id: str):
    return demo.artifacts(_job_or_404(job_id))


@app.get("/api/jobs/{job_id}/visuals")
async def get_visuals(job_id: str):
    return visuals.build(_job_or_404(job_id))


@app.get("/healthz")
async def healthz():
    return {"ok": True, "backend": "stub", "demo": True}
