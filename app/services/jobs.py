"""Job controller on the shared 002/003 schema.

Controller-only slice: no GUI and no acceptance decision. Approval stores the
canonical SecurityContract body in ``contracts.body_json``; the input hash stays
on ``jobs``, the manifest on ``runs``, and the reviewed scope is the contract
hash. A run pre-creates one attempt per route so attempts have ids immediately.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import connect
from app.domain.contracts import (
    SecurityContract,
    canonical_contract_json,
    contract_sha256,
)

TERMINAL_PHASES = {"finished", "cancelled"}
ROUTES = ("route_a", "route_b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class JobRecord:
    id: str
    original_sha256: str
    phase: str
    filename: str
    defect_description: str
    original_bytes: bytes
    created_at: str
    classification: str = "public"
    cancel_requested: int = 0


@dataclass(frozen=True)
class Approval:
    job_id: str
    contract_id: str
    contract_revision: int
    input_sha256: str
    manifest_sha256: str
    scope_sha256: str
    reviewer_label: str
    approved_at: str


@dataclass(frozen=True)
class RunRecord:
    id: str
    job_id: str
    contract_id: str
    idempotency_key: str
    phase: str
    started_at: str
    manifest_sha256: str = ""
    reviewed_scope_sha256: str = ""
    created: bool = True


@dataclass(frozen=True)
class AttemptRecord:
    id: str
    run_id: str
    route: str
    sequence: int
    phase: str
    outcome: str | None = None
    reason: str | None = None
    failing_obligation: str | None = None
    detail: str | None = None


def _job_dir(data_dir: str | Path, job_id: str) -> Path:
    return Path(data_dir) / "jobs" / job_id


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _validate_sha256(name: str, value: str | None) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} must be a 64-character hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a 64-character hex digest") from exc


def _legacy_contract() -> dict[str, Any]:
    """Compatibility body for callers that still pass hash-only approvals.

    The real reviewed v2 body must be supplied as ``contract_body``; this only
    keeps the old identity contract callable and is not a reviewed contract.
    """

    return {
        "schema_version": 2,
        "target_entry": "target",
        "target_signature": "void target(void)",
        "valid_domain": "unspecified",
        "valid_behavior": "unchanged",
        "forbidden_behavior": "none",
        "invalid_input_response": "none",
        "dest_capacity_bytes": 50,
        "max_valid_input_bytes": 49,
        "capacity_includes_terminator": True,
        "invalid_exit_code": 1,
        "invalid_stdout": "",
        "invalid_stderr": "",
        "authorized_edits": [],
        "allowed_calls": [],
        "environment_assumptions": [],
        "required_obligations": [
            "identity",
            "structure",
            "interface",
            "behaviour",
            "symbolic",
            "reproducibility",
        ],
    }


def create_job(
    *,
    original_bytes: bytes,
    filename: str,
    defect_description: str,
    data_dir: str | Path,
) -> JobRecord:
    if not isinstance(original_bytes, (bytes, bytearray)):
        raise TypeError("original_bytes must be bytes")
    payload = bytes(original_bytes)
    job_id = uuid.uuid4().hex
    sha256 = hashlib.sha256(payload).hexdigest()
    relative_path = f"jobs/{job_id}/original.bin"
    _write_atomic(Path(data_dir) / relative_path, payload)

    created_at = _now()
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute(
                "INSERT INTO jobs "
                "(id, original_sha256, filename, defect_description, phase, created_at, "
                " classification, cancel_requested) "
                "VALUES (?, ?, ?, ?, 'draft', ?, 'public', 0)",
                (job_id, sha256, filename, defect_description, created_at),
            )
            connection.execute(
                "INSERT INTO artifacts "
                "(id, job_id, attempt_id, relative_path, sha256, size_bytes, media_type, role, created_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, 'application/octet-stream', 'original', ?)",
                (
                    uuid.uuid4().hex,
                    job_id,
                    relative_path,
                    sha256,
                    len(payload),
                    created_at,
                ),
            )
    finally:
        connection.close()

    return JobRecord(
        id=job_id,
        original_sha256=sha256,
        phase="draft",
        filename=filename,
        defect_description=defect_description,
        original_bytes=payload,
        created_at=created_at,
    )


def get_job(job_id: str, *, data_dir: str | Path) -> JobRecord | None:
    connection = connect(data_dir)
    try:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        artifact = connection.execute(
            "SELECT relative_path FROM artifacts WHERE job_id = ? AND role = 'original'",
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    original = b""
    if artifact is not None:
        path = Path(data_dir) / artifact["relative_path"]
        if path.exists():
            original = path.read_bytes()

    return JobRecord(
        id=row["id"],
        original_sha256=row["original_sha256"],
        phase=row["phase"],
        filename=row["filename"],
        defect_description=row["defect_description"],
        original_bytes=original,
        created_at=row["created_at"],
        classification=row["classification"],
        cancel_requested=int(row["cancel_requested"]),
    )


def approve_contract(
    job_id: str,
    *,
    contract_revision: int,
    reviewer_label: str,
    data_dir: str | Path,
    contract_body: dict[str, Any] | None = None,
    input_sha256: str | None = None,
    manifest_sha256: str | None = None,
    scope_sha256: str | None = None,
) -> Approval:
    """Store the canonical reviewed contract and bind it to one job."""

    if contract_revision < 1:
        raise ValueError("contract_revision must be >= 1")
    if manifest_sha256 is None:
        manifest_sha256 = "0" * 64
    _validate_sha256("manifest_sha256", manifest_sha256)

    body = contract_body if contract_body is not None else _legacy_contract()
    try:
        contract = SecurityContract.from_dict(body)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid contract body: {exc}") from exc
    body_json = canonical_contract_json(contract.to_dict())
    contract_sha = contract_sha256(contract.to_dict())

    connection = connect(data_dir)
    try:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"unknown job id: {job_id}")
        if input_sha256 is not None and input_sha256 != job["original_sha256"]:
            raise ValueError("approved input hash does not match the stored job bytes")

        with connection:
            existing = connection.execute(
                "SELECT * FROM contracts WHERE job_id = ? AND revision = ?",
                (job_id, contract_revision),
            ).fetchone()
            if existing is not None:
                if existing["body_json"] != body_json or existing["sha256"] != contract_sha:
                    raise ValueError(
                        "contract revision already exists with different approval content"
                    )
                contract_id = existing["id"]
                approved_at = existing["approved_at"]
            else:
                contract_id = uuid.uuid4().hex
                approved_at = _now()
                connection.execute(
                    "INSERT INTO contracts "
                    "(id, job_id, revision, body_json, sha256, approved_at, reviewer_label) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        contract_id,
                        job_id,
                        contract_revision,
                        body_json,
                        contract_sha,
                        approved_at,
                        reviewer_label,
                    ),
                )
                connection.execute(
                    "UPDATE jobs SET phase = 'awaiting_review' WHERE id = ?",
                    (job_id,),
                )
    finally:
        connection.close()

    return Approval(
        job_id=job_id,
        contract_id=contract_id,
        contract_revision=contract_revision,
        input_sha256=job["original_sha256"],
        manifest_sha256=manifest_sha256,
        scope_sha256=contract_sha,
        reviewer_label=reviewer_label,
        approved_at=approved_at,
    )


def get_contract(job_id: str, revision: int, *, data_dir: str | Path) -> Any:
    connection = connect(data_dir)
    try:
        return connection.execute(
            "SELECT * FROM contracts WHERE job_id = ? AND revision = ?",
            (job_id, revision),
        ).fetchone()
    finally:
        connection.close()


def contract_body(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(row["body_json"])
    except (KeyError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def approval_from_contract(row: Any, *, manifest_sha256: str) -> Approval:
    return Approval(
        job_id=row["job_id"],
        contract_id=row["id"],
        contract_revision=int(row["revision"]),
        input_sha256="",
        manifest_sha256=manifest_sha256,
        scope_sha256=str(row["sha256"]),
        reviewer_label=str(row["reviewer_label"] or ""),
        approved_at=str(row["approved_at"] or ""),
    )


def _approval_field(approval: Any, name: str) -> Any:
    if isinstance(approval, dict):
        return approval.get(name)
    return getattr(approval, name, None)


def _run_record(row: Any, *, created: bool) -> RunRecord:
    return RunRecord(
        id=row["id"],
        job_id=row["job_id"],
        contract_id=row["contract_id"],
        idempotency_key=row["idempotency_key"],
        phase=row["phase"],
        started_at=row["started_at"],
        manifest_sha256=row["manifest_sha256"],
        reviewed_scope_sha256=row["reviewed_scope_sha256"],
        created=created,
    )


def start_run(
    job_id: str,
    *,
    approval: Approval | dict[str, Any] | None,
    idempotency_key: str,
    data_dir: str | Path,
) -> RunRecord:
    """Start or return the run for (job, key), creating two queued attempts."""

    if approval is None:
        raise ValueError("start_run requires an approval")
    if not idempotency_key:
        raise ValueError("idempotency_key must not be empty")
    if _approval_field(approval, "job_id") != job_id:
        raise ValueError("approval belongs to a different job")

    connection = connect(data_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT * FROM runs WHERE job_id = ? AND idempotency_key = ?",
                (job_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                requested_contract = _approval_field(approval, "contract_id")
                requested_manifest = _approval_field(approval, "manifest_sha256") or "0" * 64
                if (
                    existing["contract_id"] != requested_contract
                    or existing["manifest_sha256"] != requested_manifest
                ):
                    raise ValueError(
                        "idempotency key already used with a different contract or manifest"
                    )
                connection.commit()
                return _run_record(existing, created=False)

            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise ValueError(f"unknown job id: {job_id}")
            approved_input = _approval_field(approval, "input_sha256")
            if approved_input and approved_input != job["original_sha256"]:
                raise ValueError("approval input hash does not match the stored job bytes")

            contract_id = _approval_field(approval, "contract_id")
            contract = connection.execute(
                "SELECT * FROM contracts WHERE id = ? AND job_id = ?",
                (contract_id, job_id),
            ).fetchone()
            if contract is None:
                raise ValueError("approval contract is not part of this job")

            manifest = _approval_field(approval, "manifest_sha256") or "0" * 64
            _validate_sha256("manifest_sha256", manifest)
            reviewed_scope = str(contract["sha256"])
            run_id = uuid.uuid4().hex
            started_at = _now()
            connection.execute(
                "INSERT INTO runs "
                "(id, job_id, contract_id, manifest_sha256, reviewed_scope_sha256, "
                " idempotency_key, phase, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                (run_id, job_id, contract_id, manifest, reviewed_scope, idempotency_key, started_at),
            )
            for route in ROUTES:
                connection.execute(
                    "INSERT INTO attempts (id, run_id, route, sequence, phase, outcome) "
                    "VALUES (?, ?, ?, 1, 'queued', NULL)",
                    (uuid.uuid4().hex, run_id, route),
                )
            connection.execute("UPDATE jobs SET phase = 'queued' WHERE id = ?", (job_id,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()

    return RunRecord(
        id=run_id,
        job_id=job_id,
        contract_id=contract_id,
        idempotency_key=idempotency_key,
        phase="queued",
        started_at=started_at,
        manifest_sha256=manifest,
        reviewed_scope_sha256=reviewed_scope,
        created=True,
    )


def get_run(run_id: str, *, data_dir: str | Path) -> RunRecord | None:
    connection = connect(data_dir)
    try:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        connection.close()
    return None if row is None else _run_record(row, created=False)


def list_queued_runs(*, data_dir: str | Path) -> list[RunRecord]:
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT * FROM runs WHERE phase = 'queued' ORDER BY started_at, rowid"
        ).fetchall()
    finally:
        connection.close()
    return [_run_record(row, created=False) for row in rows]


def latest_run(job_id: str, *, data_dir: str | Path) -> RunRecord | None:
    connection = connect(data_dir)
    try:
        row = connection.execute(
            "SELECT * FROM runs WHERE job_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    finally:
        connection.close()
    return None if row is None else _run_record(row, created=False)


def get_attempt(attempt_id: str, *, data_dir: str | Path) -> AttemptRecord | None:
    connection = connect(data_dir)
    try:
        row = connection.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return AttemptRecord(
        id=row["id"],
        run_id=row["run_id"],
        route=row["route"],
        sequence=int(row["sequence"]),
        phase=row["phase"],
        outcome=row["outcome"],
        reason=row["reason"],
        failing_obligation=row["failing_obligation"],
        detail=row["detail"],
    )


def get_artifact(artifact_id: str, *, data_dir: str | Path) -> dict[str, Any] | None:
    connection = connect(data_dir)
    try:
        row = connection.execute(
            "SELECT * FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "attempt_id": row["attempt_id"],
        "relative_path": row["relative_path"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "media_type": row["media_type"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def list_artifacts(job_id: str, *, data_dir: str | Path) -> list[dict[str, Any]]:
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT a.*, t.route FROM artifacts a "
            "LEFT JOIN attempts t ON t.id = a.attempt_id "
            "WHERE a.job_id = ? ORDER BY a.created_at, a.id",
            (job_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row["id"],
            "job_id": row["job_id"],
            "attempt_id": row["attempt_id"],
            "route": row["route"],
            "relative_path": row["relative_path"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "media_type": row["media_type"],
            "role": row["role"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def list_obligations(attempt_id: str, *, data_dir: str | Path) -> list[dict[str, Any]]:
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT kind, required, result, evidence_artifact_id, detail "
            "FROM obligations WHERE attempt_id = ? ORDER BY kind",
            (attempt_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "kind": row["kind"],
            "required": bool(row["required"]),
            "result": row["result"],
            "evidence_artifact_id": row["evidence_artifact_id"],
            "detail": row["detail"],
        }
        for row in rows
    ]


def list_attempts(run_id: str, *, data_dir: str | Path) -> list[AttemptRecord]:
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT * FROM attempts WHERE run_id = ? ORDER BY route, sequence",
            (run_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        AttemptRecord(
            id=row["id"],
            run_id=row["run_id"],
            route=row["route"],
            sequence=int(row["sequence"]),
            phase=row["phase"],
            outcome=row["outcome"],
            reason=row["reason"],
            failing_obligation=row["failing_obligation"],
            detail=row["detail"],
        )
        for row in rows
    ]


def retry_attempt(run_id: str, route: str, *, data_dir: str | Path) -> AttemptRecord:
    if route not in ROUTES:
        raise ValueError("route must be route_a or route_b")
    connection = connect(data_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise ValueError(f"unknown run id: {run_id}")
            if run["phase"] in TERMINAL_PHASES:
                raise ValueError("run is terminal; cannot append an attempt")
            row = connection.execute(
                "SELECT MAX(sequence) AS seq FROM attempts WHERE run_id = ? AND route = ?",
                (run_id, route),
            ).fetchone()
            sequence = int(row["seq"] or 0) + 1
            attempt_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO attempts (id, run_id, route, sequence, phase, outcome) "
                "VALUES (?, ?, ?, ?, 'queued', NULL)",
                (attempt_id, run_id, route, sequence),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()
    attempt = get_attempt(attempt_id, data_dir=data_dir)
    if attempt is None:
        raise ValueError("attempt was not persisted")
    return attempt


def cancel_job(job_id: str, *, data_dir: str | Path) -> JobRecord:
    connection = connect(data_dir)
    try:
        with connection:
            row = connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown job id: {job_id}")
            connection.execute(
                "UPDATE jobs SET phase = 'cancelled', cancel_requested = 1 WHERE id = ?",
                (job_id,),
            )
            connection.execute(
                "UPDATE runs SET phase = 'cancelled', finished_at = ? WHERE job_id = ?",
                (_now(), job_id),
            )
    finally:
        connection.close()
    record = get_job(job_id, data_dir=data_dir)
    if record is None:
        raise ValueError(f"unknown job id: {job_id}")
    return record


__all__ = [
    "Approval",
    "AttemptRecord",
    "JobRecord",
    "ROUTES",
    "RunRecord",
    "approval_from_contract",
    "approve_contract",
    "cancel_job",
    "contract_body",
    "create_job",
    "get_artifact",
    "get_attempt",
    "get_contract",
    "get_job",
    "get_run",
    "latest_run",
    "list_artifacts",
    "list_attempts",
    "list_obligations",
    "list_queued_runs",
    "retry_attempt",
    "start_run",
]
