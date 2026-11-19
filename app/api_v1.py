"""Versioned browser API for the new workbench UI.

The routes here are the contract-v1 surface archived in
``docs/interfaces/frontend-backend-contract-v1.md``. Only paths that do not exist
on the pinned baseline are declared here; shared paths branch on :func:`is_v1`
inside ``app.main`` so a request without the version header keeps the baseline
wire format.

Honesty rules that this module must not break:

* the browser never receives a verdict the acceptance engine did not persist;
* a missing check is ``not_run``, never ``passed``;
* the review binding digest is derived from the bound inputs, not displayed labels;
* provider secrets never leave the server, and an LLM plan fails closed when no
  transfer authorization exists.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.backend import BackendError

CONTRACT_VERSION = "1"
VERSION_HEADER = "X-BinaryRepair-Contract-Version"
CSRF_HEADER = "x-csrf-token"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = 24 * 1024 * 1024
MAX_FIELD_BYTES = 16 * 1024
MAX_REVIEWER = 128

PATCH_POLICY = {
    "id": "reviewed-contract-scope",
    "label": "Reviewed contract scope",
    "summary": "Changes stay inside the reviewed contract scope; no new permissions.",
}
VERIFICATION_SCOPE = {
    "id": "v0-v5-both-routes",
    "label": "V0-V5, both routes",
    "summary": "Every required obligation must pass on the final candidate hashes.",
}

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "waiting_for_executor", "running", "cancelling"}
LEVEL_BY_KIND = {
    "identity": "V0",
    "structure": "V1",
    "interface": "V2",
    "behaviour": "V3",
    "behavior": "V3",
    "symbolic": "V4",
    "reproducibility": "V5",
}
CHECK_STATUSES = {"passed", "failed", "not_run", "unknown", "timed_out"}


class ApiError(Exception):
    """A stable v1 error with a machine-readable code."""

    def __init__(self, status_code: int, code: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extra = extra
        self.correlation_id = secrets.token_hex(8)


def is_v1(request: Request) -> bool:
    return bool(getattr(request.state, "v1", False))


def require_v1(request: Request) -> None:
    if not is_v1(request):
        raise ApiError(
            400,
            "unsupported_contract_version",
            f"Send {VERSION_HEADER}: {CONTRACT_VERSION} to use this route.",
        )


def _backend():
    # Imported lazily so tests that monkeypatch app.main.BACKEND keep working.
    from app import main

    return main.BACKEND


def _csrf_token() -> str:
    from app import main

    return main.CSRF_TOKEN


def _require_csrf(request: Request) -> None:
    from app import main

    main._require_csrf(request.headers.get(CSRF_HEADER, ""))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _as_snapshot(job_id: str) -> dict[str, Any]:
    try:
        snapshot = _backend().get_snapshot(job_id)
    except BackendError as exc:
        raise ApiError(
            _code_status(exc.status_code), _code_for(exc.status_code), exc.detail
        ) from exc
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("id"), str):
        raise ApiError(502, "capability_unavailable", "The controller snapshot is malformed.")
    return snapshot


def _code_for(status: int) -> str:
    return {
        400: "invalid_input",
        401: "session_required",
        403: "request_forbidden",
        404: "not_found",
        409: "invalid_transition",
        413: "payload_too_large",
        422: "invalid_input",
        503: "capability_unavailable",
    }.get(status, "capability_unavailable")


def _ascii(field: str, value: str) -> str:
    if len(value.encode("utf-8")) > MAX_FIELD_BYTES:
        raise ApiError(413, "payload_too_large", f"Field '{field}' exceeds 16 KiB.")
    if any(ord(char) > 127 for char in value):
        raise ApiError(422, "invalid_input", f"Field '{field}' must use ASCII characters only.")
    return value


def _assumptions(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _binding(snapshot: dict[str, Any]) -> dict[str, Any]:
    environment_text = str(snapshot.get("environment") or "").strip()
    environment = (
        {
            "id": "execution-environment",
            "label": "Execution environment",
            "summary": environment_text,
        }
        if environment_text
        else None
    )
    manifest = snapshot.get("manifest_sha256")
    original = snapshot.get("original_sha256")
    return {
        "original_sha256": original if isinstance(original, str) and len(original) == 64 else None,
        "test_manifest_sha256": manifest if isinstance(manifest, str) and len(manifest) == 64 else None,
        "environment": environment,
        "patch_policy": dict(PATCH_POLICY),
        "verification_scope": dict(VERIFICATION_SCOPE),
    }


def binding_digest(binding: dict[str, Any], contract: dict[str, Any]) -> str:
    payload = {
        "contract_revision": contract.get("revision"),
        "contract_sha256": contract.get("sha256"),
        "original_sha256": binding.get("original_sha256"),
        "test_manifest_sha256": binding.get("test_manifest_sha256"),
        "environment": binding.get("environment"),
        "patch_policy": binding.get("patch_policy"),
        "verification_scope": binding.get("verification_scope"),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def binding_complete(binding: dict[str, Any]) -> bool:
    return all(
        binding.get(key) is not None
        for key in (
            "original_sha256",
            "test_manifest_sha256",
            "environment",
            "patch_policy",
            "verification_scope",
        )
    )


def _contract_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = snapshot.get("contract") if isinstance(snapshot.get("contract"), dict) else {}
    revision = raw.get("revision", 1)
    try:
        revision = max(1, int(revision))
    except (TypeError, ValueError):
        revision = 1
    contract = {
        "valid_domain": str(raw.get("valid_domain") or ""),
        "forbidden": str(raw.get("forbidden") or ""),
        "invalid_outcome": str(raw.get("invalid_outcome") or ""),
        "assumptions": _assumptions(raw.get("assumptions")),
        "revision": revision,
        "approved": bool(raw.get("approved")),
        "sha256": str(raw.get("sha256") or ""),
        "approved_at": raw.get("approved_at"),
        "reviewer_label": raw.get("reviewer_label"),
    }
    binding = _binding(snapshot)
    contract["review_binding"] = binding
    contract["review_binding_digest"] = binding_digest(binding, contract)
    return contract


def _execution(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = snapshot.get("execution")
    raw = raw if isinstance(raw, dict) else {}
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    run_id = raw.get("run_id") or run.get("id")
    status = raw.get("status")
    if not isinstance(status, str) or not status:
        phase = snapshot.get("phase")
        verdict = snapshot.get("verdict")
        if phase == "cancelled" or (isinstance(verdict, dict) and verdict.get("outcome") == "cancelled"):
            status = "cancelled"
        elif phase in {"draft", "awaiting_review"}:
            status = "not_started"
        elif snapshot.get("demo") and (verdict or phase == "finished"):
            status = "completed"
        elif phase in {"queued", "screening"}:
            status = "queued"
        elif phase == "finished":
            status = "completed"
        else:
            status = "unknown"
    if run_id is None and snapshot.get("demo") and status != "not_started":
        run_id = "demo-run-" + str(snapshot.get("id"))
    terminal = status in TERMINAL_STATUSES
    reason = raw.get("reason_code")
    if not reason and status == "waiting_for_executor":
        reason = "executor_unavailable"
    return {
        "run_id": run_id,
        "status": status,
        "terminal": terminal,
        "reason_code": reason,
        "started_at": raw.get("started_at"),
        "finished_at": raw.get("finished_at"),
    }


def snapshot_payload(job_id: str) -> dict[str, Any]:
    snapshot = _as_snapshot(job_id)
    demo = bool(snapshot.get("demo"))
    return {
        "id": snapshot["id"],
        "defect": str(snapshot.get("defect") or ""),
        "filename": str(snapshot.get("filename") or ""),
        "phase": str(snapshot.get("phase") or "unknown"),
        "created_at": snapshot.get("created_at"),
        "demo": demo,
        "data_source": str(snapshot.get("data_source") or ("synthetic" if demo else "controller-http")),
        "contract": _contract_view(snapshot),
        "execution": _execution(snapshot),
        **({"technical_setup": snapshot["technical_setup"]} if "technical_setup" in snapshot else {}),
    }


def list_jobs(limit: int = 50) -> dict[str, Any]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ApiError(422, "invalid_input", "limit must be an integer from 1 through 50.")
    try:
        rows = _backend().list_jobs(limit=limit)
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    except Exception as exc:  # noqa: BLE001 - the controller boundary is untrusted
        raise ApiError(503, "capability_unavailable", f"Job list unavailable: {type(exc).__name__}.") from exc
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "id": str(row.get("id") or ""),
                "defect": str(row.get("defect") or ""),
                "phase": str(row.get("phase") or "unknown"),
                "created_at": row.get("created_at"),
                "outcome": row.get("outcome"),
            }
        )
    return {"jobs": normalized[:limit]}


def _code_status(status: int) -> int:
    return status if status in {400, 401, 403, 404, 409, 413, 422, 503} else 503


def _historical_evaluation(job_id: str, run_id: str) -> dict[str, Any]:
    """Read a non-current run through the controller's authorized lookup."""
    getter = getattr(_backend(), "get_run_evaluation", None)
    if getter is None:
        raise ApiError(404, "not_found", "Historical runs are not available in this backend.")
    try:
        payload = getter(job_id, run_id)
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    if not isinstance(payload, dict):
        raise ApiError(404, "not_found", "Run not found for this job.")
    if payload.get("job_id") != job_id or payload.get("run_id") != run_id:
        raise ApiError(502, "capability_unavailable", "The historical evaluation identity does not match.")
    payload.setdefault("demo", False)
    payload.setdefault("data_source", "controller-http")
    payload.setdefault("verdict", {"outcome": None, "reason": None})
    payload.setdefault("obligations", [])
    payload.setdefault("limitations", [])
    payload.setdefault("available", bool(payload["obligations"]))
    payload.setdefault("finalized", False)
    return payload


def _attempt_rows(job_id: str, run_id: str | None) -> list[dict[str, Any]]:
    try:
        payload = _backend().get_attempts(job_id)
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    if not isinstance(payload, dict):
        return []
    rows = payload.get("attempts")
    if not isinstance(rows, list):
        return []
    current = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_run = row.get("run_id") or payload.get("run_id")
        if run_id is not None and row_run != run_id:
            continue
        current.append(row)
    return current


def attempts_payload(job_id: str, run_id: str | None = None) -> dict[str, Any]:
    snapshot = _as_snapshot(job_id)
    execution = _execution(snapshot)
    selected = execution.get("run_id") if run_id is None else run_id
    rows = _attempt_rows(job_id, selected)
    if run_id is not None and run_id != execution.get("run_id") and not rows:
        # A historical run with no attempt rows is still valid if the controller
        # can identify it; a truly unknown run is a 404.
        _historical_evaluation(job_id, run_id)
    attempts = []
    for row in rows:
        attempts.append(
            {
                "id": str(row.get("id") or row.get("attempt_id") or ""),
                "run_id": row.get("run_id") or selected,
                "route": str(row.get("route") or ""),
                "attempt_no": row.get("attempt_no"),
                "status": str(row.get("status") or "unknown"),
                "outcome": row.get("outcome"),
                "candidate_sha256": row.get("candidate_sha256"),
                "route_label": row.get("route_label"),
                "timeline_status": row.get("timeline_status") or row.get("status") or "unknown",
                "duration_ms": row.get("duration_ms"),
                "exit_code": row.get("exit_code"),
            }
        )
    return {
        "job_id": job_id,
        "run_id": selected,
        "available": bool(attempts) or selected is not None,
        "attempts": attempts,
    }


def _obligation_rows(snapshot: dict[str, Any], run_id: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    routes = snapshot.get("routes") if isinstance(snapshot.get("routes"), dict) else {}
    for key, route in (routes.items() if isinstance(routes, dict) else []):
        if not isinstance(route, dict):
            continue
        group = route.get("obligations")
        if not isinstance(group, list):
            continue
        route_name = "route_b" if str(key).upper() == "B" else "route_a"
        for row in group:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "")
            level = str(row.get("id") or "").upper()
            if level not in {"V0", "V1", "V2", "V3", "V4", "V5"}:
                level = LEVEL_BY_KIND.get(kind) or LEVEL_BY_KIND.get(kind.lower()) or "unknown"
            status = str(row.get("result") or row.get("status") or "unknown")
            if status not in CHECK_STATUSES:
                status = "unknown"
            reason = row.get("reason") or row.get("reason_code")
            rows.append(
                {
                    "route": route_name,
                    "level": level,
                    "status": status,
                    "reason_code": str(reason) if reason else None,
                }
            )
    order = {f"V{index}": index for index in range(6)}
    rows.sort(key=lambda row: (0 if row["route"] == "route_a" else 1, order.get(row["level"], 99)))
    return rows


def evaluation_payload(job_id: str, run_id: str | None = None) -> dict[str, Any]:
    snapshot = _as_snapshot(job_id)
    execution = _execution(snapshot)
    selected = execution.get("run_id") if run_id is None else run_id
    if run_id is not None and run_id != execution.get("run_id"):
        return _historical_evaluation(job_id, run_id)
    attempts = _attempt_rows(job_id, selected) if selected is not None else []
    obligations = _obligation_rows(snapshot, selected) if attempts or selected is not None else []
    raw_verdict = snapshot.get("verdict") if isinstance(snapshot.get("verdict"), dict) else {}
    finalized = bool(execution.get("terminal")) and bool(raw_verdict.get("outcome"))
    demo = bool(snapshot.get("demo"))
    limitations: list[dict[str, str]] = []
    if selected is None:
        limitations.append(
            {"code": "no_run", "detail": "No run exists for this job; no check has run."}
        )
    elif not finalized:
        limitations.append(
            {
                "code": "evaluation_pending",
                "detail": (
                    "Execution has not committed a finalized evaluation for this run."
                    if execution.get("terminal")
                    else "The run has not finished; no verdict exists yet."
                ),
            }
        )
    incomplete = [row for row in obligations if row["status"] in {"not_run", "unknown", "timed_out"}]
    if incomplete:
        limitations.append(
            {
                "code": "checks_incomplete",
                "detail": f"{len(incomplete)} recorded check(s) did not pass and are not evidence.",
            }
        )
    if demo:
        limitations.append(
            {"code": "synthetic_data", "detail": "Synthetic demo data; no binary was executed."}
        )
    return {
        "job_id": job_id,
        "run_id": selected,
        "available": bool(finalized or attempts or obligations),
        "finalized": finalized,
        "demo": demo,
        "data_source": str(snapshot.get("data_source") or ("synthetic" if demo else "controller-http")),
        "verdict": (
            {
                "outcome": raw_verdict.get("outcome"),
                "reason": raw_verdict.get("reason") or raw_verdict.get("reason_code"),
            }
            if finalized
            else {"outcome": None, "reason": None}
        ),
        "obligations": obligations,
        "limitations": limitations,
    }


def comparison_payload(job_id: str, run_id: str | None = None) -> dict[str, Any]:
    snapshot = _as_snapshot(job_id)
    execution = _execution(snapshot)
    selected = execution.get("run_id") if run_id is None else run_id
    if run_id is not None and run_id != execution.get("run_id"):
        _historical_evaluation(job_id, run_id)
    try:
        raw = _backend().get_comparison(job_id)
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    if raw is None:
        return {
            "job_id": job_id,
            "run_id": selected,
            "status": "not_provided",
            "detail": "No behavior comparison is registered for this run.",
            "comparison": None,
            "artifacts": [],
        }
    status = str(raw.get("status") or "not_provided")
    if status not in {"available", "not_provided", "invalid"}:
        status = "invalid"
    detail = str(raw.get("detail") or "")
    summary = None
    if status == "available":
        summary = detail or "Comparison evidence is registered for this run."
    return {
        "job_id": job_id,
        "run_id": selected,
        "status": status,
        "detail": detail,
        "comparison": {"summary": summary} if summary else None,
        "artifacts": [],
        "details": raw,
    }


def _artifact_kind(row: dict[str, Any]) -> str:
    role = str(row.get("role") or "")
    if role in {"original", "original_input"}:
        return "original_input"
    if role == "operator_deliverable":
        return "manual_deliverable"
    kind = row.get("kind")
    if kind in {"original_input", "manual_deliverable", "run_evidence"}:
        return str(kind)
    return "run_evidence"


def _artifact_view(row: dict[str, Any], job_id: str, fallback_run: str | None, index: int) -> dict[str, Any]:
    artifact_id = row.get("id") or row.get("artifact_id")
    synthetic = not isinstance(artifact_id, str) or not artifact_id
    if synthetic:
        artifact_id = f"demo-artifact-{index}"
    role = str(row.get("role") or "")
    run_id = row.get("run_id")
    if not run_id and row.get("attempt_id"):
        run_id = fallback_run
    kind = _artifact_kind(row)
    if kind == "original_input":
        run_id = None
    if not run_id and kind == "run_evidence":
        run_id = fallback_run
    reason = row.get("unavailable_reason")
    if synthetic and not reason:
        reason = "Synthetic preview artifact; no bytes are stored."
    size = row.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        size = 0
    return {
        "id": artifact_id,
        "job_id": job_id,
        "name": str(row.get("name") or artifact_id),
        "role": role,
        "media_type": str(row.get("media_type") or "application/octet-stream"),
        "run_id": run_id,
        "kind": kind,
        "sha256": str(row.get("sha256") or ""),
        "size_bytes": size,
        "available": bool(row.get("available")) and not synthetic,
        "unavailable_reason": None if row.get("available") and not synthetic else (reason or "content is unavailable"),
    }


artifact_view = _artifact_view


def artifacts_payload(job_id: str, run_id: str | None = None) -> dict[str, Any]:
    snapshot = _as_snapshot(job_id)
    execution = _execution(snapshot)
    target = execution.get("run_id") if run_id is None else run_id
    try:
        groups = _backend().get_artifacts(job_id)
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    rows: list[dict[str, Any]] = []
    if isinstance(groups, dict):
        for value in groups.values():
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
    elif isinstance(groups, list):
        rows.extend(item for item in groups if isinstance(item, dict))
    selected = [
        row for row in rows
        if _artifact_kind(row) != "run_evidence"
        or target is None
        or not row.get("run_id")
        or row.get("run_id") == target
    ]
    return {
        "artifacts": [
            _artifact_view(row, job_id, target, index)
            for index, row in enumerate(selected)
        ]
    }


def artifact_payload(artifact_id: str) -> dict[str, Any]:
    getter = getattr(_backend(), "get_artifact_metadata", None)
    if getter is None:
        raise ApiError(404, "not_found", "Artifact metadata is only available in controller mode.")
    try:
        row = getter(artifact_id)
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    if not isinstance(row, dict) or not row.get("id"):
        raise ApiError(404, "not_found", "Artifact not found.")
    return _artifact_view(row, str(row.get("job_id") or ""), row.get("run_id"), 0)


def export_payload(job_id: str, run_id: str | None = None) -> dict[str, Any]:
    snapshot = _as_snapshot(job_id)
    execution = _execution(snapshot)
    selected = execution.get("run_id") if run_id is None else run_id
    if run_id is not None and run_id != execution.get("run_id"):
        _historical_evaluation(job_id, run_id)
    demo = bool(snapshot.get("demo"))
    return {
        "schema_version": 1,
        "generated_at": _now(),
        "job_id": job_id,
        "run_id": selected,
        "data_source": str(snapshot.get("data_source") or ("synthetic" if demo else "controller-http")),
        "demo": demo,
        "evaluation": evaluation_payload(job_id, selected),
        "comparison": comparison_payload(job_id, selected),
        "artifacts": artifacts_payload(job_id, selected)["artifacts"],
    }


router = APIRouter()


@router.get("/api/session")
def session(request: Request) -> dict[str, Any]:
    require_v1(request)
    return {"session": True, "csrf_token": _csrf_token()}


@router.get("/api/jobs")
def jobs_collection(request: Request, limit: int = 50) -> dict[str, Any]:
    require_v1(request)
    return list_jobs(limit)


@router.post("/api/jobs", status_code=201)
async def create_job(
    request: Request,
    binary: UploadFile = File(...),
    defect: str = Form(""),
    valid_tests: str = Form(""),
    environment: str = Form(""),
    valid_domain: str = Form(""),
    forbidden: str = Form(""),
    invalid_outcome: str = Form(""),
    assumptions: str = Form(""),
    guided_contract: bool = Form(False),
) -> dict[str, Any]:
    require_v1(request)
    _require_csrf(request)
    # This controller predates the typed-contract handoff. Decline the upload
    # here so a multipart contract cannot be silently dropped during creation.
    form = await request.form()
    if "contract" in form:
        contract = form["contract"]
        if not isinstance(contract, str):
            raise ApiError(422, "invalid_input", "The contract must be submitted as JSON text.")
        if len(contract.encode("utf-8")) > MAX_FIELD_BYTES:
            raise ApiError(413, "payload_too_large", "The contract exceeds the 16 KiB text limit.")
        try:
            document = json.loads(contract)
        except ValueError as exc:
            raise ApiError(422, "invalid_input", "The contract must be a JSON object.") from exc
        if not isinstance(document, dict):
            raise ApiError(422, "invalid_input", "The contract must be a JSON object.")
        raise ApiError(409, "invalid_transition", "Typed contract uploads require the reviewed-binding backend.")
    for name, value in (
        ("defect", defect),
        ("valid_tests", valid_tests),
        ("environment", environment),
        ("valid_domain", valid_domain),
        ("forbidden", forbidden),
        ("invalid_outcome", invalid_outcome),
        ("assumptions", assumptions),
    ):
        _ascii(name, value)
    if not defect.strip():
        raise ApiError(422, "invalid_input", "'defect' is required.")
    payload = await binary.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise ApiError(413, "payload_too_large", "Uploaded file exceeds the 20 MiB limit.")
    if not payload:
        raise ApiError(422, "invalid_input", "Uploaded file is empty.")
    filename = _safe_filename(binary.filename or "sample.bin")
    try:
        job_id = _backend().create_job(
            binary_bytes=payload,
            filename=filename,
            defect=defect.strip(),
            valid_tests=valid_tests,
            environment=environment,
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
            **({"guided_contract": True} if guided_contract else {}),
        )
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    return {"id": job_id, "phase": "draft"}


def _safe_filename(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "sample.bin"
    clean = "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "_" for char in base)
    return clean[:120] or "sample.bin"


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise ApiError(422, "invalid_input", "A JSON object body is required.") from exc
    if not isinstance(body, dict):
        raise ApiError(422, "invalid_input", "A JSON object body is required.")
    return body


@router.post("/api/jobs/{job_id}/contract")
async def update_contract(request: Request, job_id: str) -> dict[str, Any]:
    require_v1(request)
    _require_csrf(request)
    body = await _json_body(request)
    expected = body.get("expected_revision")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise ApiError(422, "invalid_input", "'expected_revision' must be an integer.")
    snapshot = _as_snapshot(job_id)
    current = _contract_view(snapshot)
    if expected != current["revision"]:
        raise ApiError(
            409,
            "stale_revision",
            "The contract changed since this page loaded.",
            current={
                "revision": current["revision"],
                "sha256": current["sha256"],
                "review_binding_digest": current["review_binding_digest"],
            },
        )
    if "technical_setup" in snapshot:
        result = _change_guided(job_id, "save", body)
        return _revision_fields(result["contract"], _binding({**snapshot, **result}))
    fields: dict[str, str] = {}
    for key in ("valid_domain", "forbidden", "invalid_outcome", "assumptions"):
        value = body.get(key, None)
        if value is None:
            value = "\n".join(current[key]) if key == "assumptions" else current.get(key, "")
        if not isinstance(value, str):
            raise ApiError(422, "invalid_input", f"'{key}' must be text.")
        fields[key] = _ascii(key, value)
    try:
        _backend().update_contract(
            job_id,
            expected_revision=str(current["revision"]),
            valid_domain=fields["valid_domain"],
            forbidden=fields["forbidden"],
            invalid_outcome=fields["invalid_outcome"],
            assumptions=fields["assumptions"],
        )
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    updated = _contract_view(_as_snapshot(job_id))
    return {
        "revision": updated["revision"],
        "sha256": updated["sha256"],
        "review_binding_digest": updated["review_binding_digest"],
    }


def _revision_fields(contract: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision": contract["revision"], "sha256": contract["sha256"],
        "review_binding_digest": binding_digest(binding, contract),
    }


def _change_guided(job_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return _backend().change_guided_review(job_id, action, body)
    except BackendError as exc:
        if exc.status_code == 409:
            current = _contract_view(_as_snapshot(job_id))
            if body.get("expected_revision") != current["revision"]:
                raise ApiError(
                    409, "stale_revision", "The contract changed since this page loaded.",
                    current={key: current[key] for key in ("revision", "sha256", "review_binding_digest")},
                ) from exc
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc


@router.post("/api/jobs/{job_id}/technical-setup")
async def confirm_technical_setup(request: Request, job_id: str) -> dict[str, Any]:
    require_v1(request)
    _require_csrf(request)
    body = await _json_body(request)
    if type(body.get("expected_revision")) is not int:
        raise ApiError(422, "invalid_input", "'expected_revision' must be an integer.")
    snapshot = _as_snapshot(job_id)
    if "technical_setup" not in snapshot:
        raise ApiError(409, "invalid_transition", "This job does not use guided contract review.")
    result = _change_guided(job_id, "setup", body)
    return _revision_fields(result["contract"], _binding({**snapshot, **result}))


@router.post("/api/jobs/{job_id}/approve")
async def approve_contract(request: Request, job_id: str) -> dict[str, Any]:
    require_v1(request)
    _require_csrf(request)
    body = await _json_body(request)
    expected = body.get("expected_sha256")
    reviewer = body.get("reviewer_label")
    if not isinstance(expected, str) or not expected:
        raise ApiError(422, "invalid_input", "'expected_sha256' is required.")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ApiError(422, "invalid_input", "'reviewer_label' is required.")
    reviewer = _ascii("reviewer_label", reviewer.strip())
    if len(reviewer) > MAX_REVIEWER:
        raise ApiError(422, "invalid_input", "'reviewer_label' must be at most 128 characters.")
    snapshot = _as_snapshot(job_id)
    contract = _contract_view(snapshot)
    current = {
        "revision": contract["revision"],
        "sha256": contract["sha256"],
        "review_binding_digest": contract["review_binding_digest"],
    }
    if not binding_complete(contract["review_binding"]):
        raise ApiError(
            409,
            "invalid_transition",
            "The review binding is missing a required input; approval is disabled.",
            current=current,
        )
    if expected != contract["review_binding_digest"]:
        raise ApiError(
            409,
            "stale_approval",
            "The review binding changed since this page loaded.",
            current=current,
        )
    if "technical_setup" in snapshot:
        result = _change_guided(job_id, "approve", {
            "expected_revision": contract["revision"], "expected_sha256": contract["sha256"],
            "reviewer_label": reviewer,
        })
        approved = result["contract"]
        return {
            "approved": approved["approved"], "sha256": approved["sha256"],
            "review_binding_digest": expected, "approved_at": approved["approved_at"],
        }
    try:
        _backend().approve_contract(
            job_id,
            expected_sha256=contract["sha256"],
            reviewer_label=reviewer,
        )
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    approved = _contract_view(_as_snapshot(job_id))
    return {
        "approved": True,
        "sha256": approved["sha256"],
        "review_binding_digest": approved["review_binding_digest"],
        "approved_at": approved.get("approved_at") or _now(),
    }


@router.post("/api/jobs/{job_id}/start")
async def start_job(request: Request, job_id: str) -> dict[str, Any]:
    require_v1(request)
    _require_csrf(request)
    body = await _json_body(request)
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key or len(key) > 128:
        raise ApiError(422, "invalid_input", "An Idempotency-Key header of 1-128 characters is required.")
    expected = body.get("expected_sha256")
    if not isinstance(expected, str) or not expected:
        raise ApiError(422, "invalid_input", "'expected_sha256' is required.")
    snapshot = _as_snapshot(job_id)
    execution = _execution(snapshot)
    contract = _contract_view(snapshot)
    current = {
        "revision": contract["revision"],
        "sha256": contract["sha256"],
        "review_binding_digest": contract["review_binding_digest"],
    }
    if "technical_setup" not in snapshot and (
        execution["status"] in ACTIVE_STATUSES or execution["status"] == "unknown"
    ):
        raise ApiError(
            409,
            "invalid_transition",
            "A run is already active for this job.",
            current=current,
        )
    if not binding_complete(contract["review_binding"]):
        raise ApiError(
            409,
            "invalid_transition",
            "The review binding is missing a required input; start is disabled.",
            current=current,
        )
    if expected != contract["review_binding_digest"] or not contract["approved"]:
        raise ApiError(
            409,
            "stale_approval",
            "The current approval does not cover this review binding.",
            current=current,
        )
    if "technical_setup" in snapshot:
        return _change_guided(job_id, "start", {
            "expected_revision": contract["revision"], "expected_sha256": contract["sha256"],
            "idempotency_key": key,
        })
    try:
        result = _backend().start(job_id, idempotency_key=key) or {}
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    return {
        "run_id": result.get("run_id"),
        "phase": result.get("phase") or "queued",
    }


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
    require_v1(request)
    _require_csrf(request)
    body = await _json_body(request)
    run_id = body.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ApiError(422, "invalid_input", "'run_id' is required.")
    snapshot = _as_snapshot(job_id)
    execution = _execution(snapshot)
    if execution.get("run_id") is None:
        raise ApiError(404, "not_found", "Run not found for this job.")
    if run_id != execution.get("run_id"):
        raise ApiError(409, "invalid_transition", "That run is not the current run for this job.")
    if execution["status"] in {"completed", "failed"}:
        raise ApiError(409, "invalid_transition", "A completed or failed run cannot be cancelled.")
    try:
        _backend().cancel(job_id)
    except BackendError as exc:
        raise ApiError(_code_status(exc.status_code), _code_for(exc.status_code), exc.detail) from exc
    after = _execution(_as_snapshot(job_id))
    return {"run_id": run_id, "phase": after["status"], "execution": after}


@router.post("/api/jobs/{job_id}/plan")
async def plan_job(request: Request, job_id: str) -> dict[str, Any]:
    require_v1(request)
    _require_csrf(request)
    body = await _json_body(request)
    mode = body.get("mode", "deterministic")
    if mode not in {"deterministic", "llm"}:
        raise ApiError(422, "invalid_input", "'mode' must be 'deterministic' or 'llm'.")

    snapshot = _as_snapshot(job_id)
    description = str(snapshot.get("defect") or "").strip()

    if mode == "llm":
        # Only committed public-fixture material may leave the host for a
        # hosted-provider plan. Client binaries and confidential derived
        # content fail closed here, before any provider call.
        from app.services.plan_authorization import may_transfer_plan

        if not may_transfer_plan(snapshot.get("original_sha256")):
            raise ApiError(
                403,
                "plan_transfer_not_authorized",
                "No transfer authorization covers this job for an external provider.",
            )
        from app.services.assistant_llm import client_from_env, generate_plan

        plan = generate_plan(description, client=client_from_env(), mode="llm")
    else:
        from app.domain.assistant import deterministic_plan

        plan = deterministic_plan(description)

    plan_payload = plan.model_dump(mode="json")
    tokens = plan_payload.get("tokens")
    return {
        "plan": {
            "plan_id": plan_payload["plan_id"],
            "mode": plan_payload["mode"],
            "fallback": plan_payload.get("fallback", False),
            "fallback_reason": plan_payload.get("fallback_reason"),
            "model": plan_payload.get("model"),
            "tokens": tokens,
            "steps": plan_payload["steps"],
        },
        "agent": {
            "requested_mode": mode,
            "mode": plan_payload["mode"],
            "fallback": plan_payload.get("fallback", False),
            "fallback_code": plan_payload.get("fallback_reason"),
            "model": plan_payload.get("model"),
            "tokens": tokens,
        },
    }
