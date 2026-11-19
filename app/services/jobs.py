"""Job controller on the shared 002/003 schema.

Controller-only slice: no GUI and no acceptance decision. Approval stores the
canonical SecurityContract body in ``contracts.body_json``; the input hash stays
on ``jobs``, the manifest on ``runs``, and the reviewed scope is the contract
hash. A run pre-creates one attempt per route so attempts have ids immediately.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import contextmanager
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


class RunConflict(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


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
    controller_metadata: dict[str, Any] | None = None,
) -> JobRecord:
    if not isinstance(original_bytes, (bytes, bytearray)):
        raise TypeError("original_bytes must be bytes")
    payload = bytes(original_bytes)
    job_id = uuid.uuid4().hex
    sha256 = hashlib.sha256(payload).hexdigest()
    relative_path = f"jobs/{job_id}/original.bin"
    original_path = Path(data_dir) / relative_path

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
            if controller_metadata is not None:
                connection.execute(
                    "INSERT INTO controller_job_metadata (job_id, payload_json) VALUES (?, ?)",
                    (job_id, json.dumps(controller_metadata, sort_keys=True, ensure_ascii=True)),
                )
            _write_atomic(original_path, payload)
    except BaseException:
        try:
            original_path.with_suffix(".tmp").unlink(missing_ok=True)
            original_path.unlink(missing_ok=True)
            original_path.parent.rmdir()
        except OSError:
            pass
        raise
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
    connection: sqlite3.Connection | None = None,
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

    @contextmanager
    def approval_connection():
        if connection is not None:
            if not connection.in_transaction:
                raise ValueError("approval requires an open transaction")
            yield connection
            return
        owned = connect(data_dir)
        try:
            with owned:
                owned.execute("BEGIN IMMEDIATE")
                yield owned
        finally:
            owned.close()

    with approval_connection() as db:
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"unknown job id: {job_id}")
        if input_sha256 is not None and input_sha256 != job["original_sha256"]:
            raise ValueError("approved input hash does not match the stored job bytes")

        existing = db.execute(
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
            db.execute(
                "INSERT INTO contracts "
                "(id, job_id, revision, body_json, sha256, approved_at, reviewer_label) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    contract_id, job_id, contract_revision, body_json, contract_sha,
                    approved_at, reviewer_label,
                ),
            )
            db.execute(
                "UPDATE jobs SET phase = 'awaiting_review' WHERE id = ? AND phase = 'draft'",
                (job_id,),
            )

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
    review_digest: str | None = None,
    approval_for_start: Callable[[sqlite3.Connection], Approval] | None = None,
) -> RunRecord:
    """Start or return the run for (job, key), creating two queued attempts."""

    if approval is None and approval_for_start is None:
        raise ValueError("start_run requires an approval")
    if not idempotency_key:
        raise ValueError("idempotency_key must not be empty")
    if approval is not None and _approval_field(approval, "job_id") != job_id:
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
                if review_digest is not None:
                    ack = connection.execute(
                        "SELECT review_digest FROM v1_run_starts WHERE run_id = ?",
                        (existing["id"],),
                    ).fetchone()
                    if ack is None or ack["review_digest"] != review_digest:
                        raise RunConflict(
                            "idempotency_key_conflict",
                            "This idempotency key was used for a different start request.",
                        )
                    connection.commit()
                    return _run_record(existing, created=False)
                if approval_for_start is not None:
                    approval = approval_for_start(connection)
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

            active = connection.execute(
                "SELECT id FROM runs WHERE job_id = ? "
                "AND phase NOT IN ('finished', 'cancelled') LIMIT 1",
                (job_id,),
            ).fetchone()
            if active is not None:
                raise RunConflict("invalid_transition", "A run is already active for this job.")

            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise ValueError(f"unknown job id: {job_id}")
            if approval_for_start is not None:
                approval = approval_for_start(connection)
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
            if review_digest is not None:
                connection.execute(
                    "INSERT INTO v1_run_starts (run_id, review_digest) VALUES (?, ?)",
                    (run_id, review_digest),
                )
            for route in ROUTES:
                connection.execute(
                    "INSERT INTO attempts (id, run_id, route, sequence, phase, outcome) "
                    "VALUES (?, ?, ?, 1, 'queued', NULL)",
                    (uuid.uuid4().hex, run_id, route),
                )
            connection.execute(
                "UPDATE jobs SET phase = 'queued', cancel_requested = 0 WHERE id = ?", (job_id,)
            )
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


def record_executor_unavailable(run: RunRecord, *, data_dir: str | Path) -> None:
    """Record a worker preflight only while the named run is still unclaimed."""
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute(
                "INSERT INTO run_executor_preflight (run_id, observed_at) "
                "SELECT id, ? FROM runs WHERE id = ? AND job_id = ? AND phase = 'queued' "
                "ON CONFLICT(run_id) DO UPDATE SET observed_at = excluded.observed_at",
                (_now(), run.id, run.job_id),
            )
    finally:
        connection.close()


def claim_run(run: RunRecord, *, data_dir: str | Path) -> bool:
    """Claim a queued run before work begins, in the same store used for cancellation."""
    connection = connect(data_dir)
    try:
        with connection:
            updated = connection.execute(
                "UPDATE runs SET phase = 'screening' WHERE id = ? AND job_id = ? AND phase = 'queued' "
                "AND EXISTS (SELECT 1 FROM jobs WHERE jobs.id = runs.job_id AND cancel_requested = 0)",
                (run.id, run.job_id),
            )
            if updated.rowcount != 1:
                return False
            connection.execute("DELETE FROM run_executor_preflight WHERE run_id = ?", (run.id,))
            connection.execute("UPDATE jobs SET phase = 'screening' WHERE id = ?", (run.job_id,))
            return True
    finally:
        connection.close()


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


def add_artifact(
    job_id: str,
    *,
    relative_path: str,
    role: str,
    data_dir: str | Path,
    media_type: str = "application/octet-stream",
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Register an existing file under the job's data directory.

    This is a storage/registration primitive, not an acceptance result. Callers
    must not turn an uploaded deliverable into an obligation or verdict.
    """
    data_root = Path(data_dir).resolve()
    path = (data_root / relative_path).resolve()
    try:
        path.relative_to(data_root)
    except ValueError as exc:
        raise ValueError("artifact path escapes the data directory") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError("artifact path is not a regular file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_id = uuid.uuid4().hex
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute(
                "INSERT INTO artifacts "
                "(id, job_id, attempt_id, relative_path, sha256, size_bytes, media_type, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, job_id, attempt_id, relative_path, digest,
                 path.stat().st_size, media_type, role, _now()),
            )
    finally:
        connection.close()
    return get_artifact(artifact_id, data_dir=data_dir) or {}


def list_artifacts(job_id: str, *, data_dir: str | Path) -> list[dict[str, Any]]:
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT a.*, t.route, t.run_id FROM artifacts a "
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
            "run_id": row["run_id"],
            "relative_path": row["relative_path"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "media_type": row["media_type"],
            "role": row["role"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]



def list_jobs(*, data_dir: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """Return job summaries newest first, with the latest run's outcome.

    ``outcome`` is the latest persisted run outcome and stays ``None`` until a
    run reaches a terminal state; it is not an evaluation projection.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer from 1 through 50")
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT j.id, j.defect_description, j.phase, j.created_at, "
            " (SELECT r.outcome FROM runs r WHERE r.job_id = j.id "
            "  ORDER BY r.started_at DESC, r.rowid DESC LIMIT 1) AS outcome "
            "FROM jobs j ORDER BY j.created_at DESC, j.rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row["id"],
            "defect": row["defect_description"],
            "phase": row["phase"],
            "created_at": row["created_at"],
            "outcome": row["outcome"],
        }
        for row in rows
    ]


def _scope_for_kind(kind: str) -> str:
    from app.domain.scopes import scope_for_kind

    return scope_for_kind(kind)


def list_job_attempts(job_id: str, *, data_dir: str | Path) -> list[dict[str, Any]]:
    """Every attempt of a job across all runs, oldest first.

    The GUI needs attempt ids to show retry history and to call
    ``/api/attempts/{id}/evidence``; no other endpoint exposes them as a list.
    """
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT a.id, a.run_id, a.route, a.sequence, a.phase, a.outcome, "
            "a.reason, a.candidate_sha256, a.started_at, a.finished_at "
            "FROM attempts a JOIN runs r ON r.id = a.run_id "
            "WHERE r.job_id = ? "
            "ORDER BY r.started_at, r.id, a.sequence",
            (job_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row["id"],
            "run_id": row["run_id"],
            "route": row["route"],
            "attempt_no": int(row["sequence"]),
            "status": row["phase"],
            "outcome": row["outcome"],
            "reason": row["reason"],
            "candidate_sha256": row["candidate_sha256"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
        for row in rows
    ]

def list_obligations(attempt_id: str, *, data_dir: str | Path) -> list[dict[str, Any]]:
    connection = connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT kind, scope, required, result, evidence_artifact_id, detail "
            "FROM obligations WHERE attempt_id = ? ORDER BY kind",
            (attempt_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "kind": row["kind"],
            "scope": row["scope"] or _scope_for_kind(row["kind"]),
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
            if run["phase"] in TERMINAL_PHASES | {"cancelling"}:
                raise ValueError("run no longer accepts attempts")
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


def _request_cancellation(connection, job_id: str, run):
    phase = run["phase"]
    if phase in {"cancelling", "cancelled"}:
        return run
    if phase == "finished":
        raise RunConflict("invalid_transition", "A completed or failed run cannot be cancelled.")
    # Only a run that no worker has claimed can be confirmed as never executed.
    target = "cancelled" if phase == "queued" else "cancelling"
    finished_at = _now() if target == "cancelled" else None
    connection.execute(
        "UPDATE runs SET phase = ?, finished_at = ? WHERE id = ?",
        (target, finished_at, run["id"]),
    )
    connection.execute(
        "UPDATE jobs SET phase = ?, cancel_requested = 1 WHERE id = ?", (target, job_id),
    )
    return connection.execute("SELECT * FROM runs WHERE id = ?", (run["id"],)).fetchone()


def cancel_run(job_id: str, run_id: str, *, data_dir: str | Path):
    """Resolve the named run and cancel it only if it is still current."""
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            named = connection.execute(
                "SELECT * FROM runs WHERE job_id = ? AND id = ?", (job_id, run_id),
            ).fetchone()
            if named is None:
                return None
            latest = connection.execute(
                "SELECT id FROM runs WHERE job_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if latest["id"] != run_id:
                raise RunConflict("invalid_transition", "That run is not the current run for this job.")
            return _request_cancellation(connection, job_id, named)
    finally:
        connection.close()


def cancel_job(job_id: str, *, data_dir: str | Path) -> JobRecord:
    """Legacy job-scoped cancellation of the current run or draft."""
    connection = connect(data_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT phase FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown job id: {job_id}")
            active = connection.execute(
                "SELECT * FROM runs WHERE job_id = ? "
                "AND phase NOT IN ('finished', 'cancelled') "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if active is not None:
                _request_cancellation(connection, job_id, active)
            elif row["phase"] not in {"draft", "queued", "awaiting_review", "cancelled"}:
                raise ValueError("no active run to cancel")
            else:
                connection.execute(
                    "UPDATE jobs SET phase = 'cancelled', cancel_requested = 1 WHERE id = ?",
                    (job_id,),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()
    record = get_job(job_id, data_dir=data_dir)
    if record is None:
        raise ValueError(f"unknown job id: {job_id}")
    return record


_PLACEHOLDER_CONTRACT_SHA: str | None = None


def placeholder_contract_sha() -> str:
    """SHA-256 of the legacy identity placeholder used by hash-only approvals."""
    global _PLACEHOLDER_CONTRACT_SHA
    if _PLACEHOLDER_CONTRACT_SHA is None:
        _PLACEHOLDER_CONTRACT_SHA = contract_sha256(_legacy_contract())
    return _PLACEHOLDER_CONTRACT_SHA


def reviewed_scope_state(job_id: str, run_id: str, *, data_dir: str | Path) -> tuple[bool, str]:
    """Fail-closed check: may any executor run under this run's recorded scope?

    A run is executable only when its persisted contract is a canonical
    ``SecurityContract`` whose hash is the run scope, the scope is not the
    legacy identity placeholder, and a managed v1 job additionally carries the
    approval markers written by the reviewed-approval transaction.
    """
    connection = connect(data_dir)
    try:
        run = connection.execute(
            "SELECT * FROM runs WHERE id = ? AND job_id = ?", (run_id, job_id),
        ).fetchone()
        if run is None:
            return False, "run not found for this job"
        contract = connection.execute(
            "SELECT * FROM contracts WHERE id = ? AND job_id = ?",
            (run["contract_id"], job_id),
        ).fetchone()
        meta_row = connection.execute(
            "SELECT payload_json FROM controller_job_metadata WHERE job_id = ?", (job_id,),
        ).fetchone()
        start_ack = connection.execute(
            "SELECT review_digest FROM v1_run_starts WHERE run_id = ?", (run_id,),
        ).fetchone()
    finally:
        connection.close()
    if contract is None:
        return False, "run has no approved contract"
    if not contract["approved_at"]:
        return False, "the run contract has no recorded approval"
    try:
        body = json.loads(contract["body_json"])
        canonical = SecurityContract.from_dict(body) if isinstance(body, dict) else None
    except (TypeError, ValueError):
        canonical = None
    if canonical is None:
        return False, "the run contract is not a canonical security contract"
    sha = canonical.sha256()
    if (
        hashlib.sha256(contract["body_json"].encode("utf-8")).hexdigest() != sha
        or sha != str(contract["sha256"])
    ):
        return False, "the run contract bytes do not match the stored contract hash"
    if sha != str(run["reviewed_scope_sha256"] or ""):
        return False, "the run contract hash does not match the run scope"
    if sha == placeholder_contract_sha():
        return False, "the run scope is the legacy identity placeholder"
    meta: dict[str, Any] = {}
    if meta_row is not None:
        try:
            loaded = json.loads(meta_row["payload_json"])
        except (TypeError, ValueError):
            return False, "the job approval metadata cannot be read"
        if not isinstance(loaded, dict):
            return False, "the job approval metadata cannot be read"
        meta = loaded
    if meta.get("review_v1") or start_ack is not None:
        approval = meta.get("approval")
        if not isinstance(approval, dict):
            return False, "the managed approval is missing"
        if (
            approval.get("canonical_contract_sha256") != sha
            or approval.get("revision") != contract["revision"]
            or meta.get("manifest_sha256") != run["manifest_sha256"]
        ):
            return False, "the managed approval is not bound to the run contract"
        typed_sha = approval.get("typed_contract_sha256")
        if not isinstance(typed_sha, str) or typed_sha != meta.get("typed_contract_sha256"):
            return False, "the managed approval has no typed contract source"
        if start_ack is not None and approval.get("review_binding_digest") != start_ack["review_digest"]:
            return False, "the managed approval differs from the recorded start"
    return True, ""


def fail_unbound_run(job_id: str, run_id: str, *, detail: str, data_dir: str | Path) -> None:
    """Terminate a run that cannot execute, without invoking an executor or a verdict."""
    reason = "unbound_review_scope"
    connection = connect(data_dir)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT phase FROM runs WHERE id = ? AND job_id = ?", (run_id, job_id),
            ).fetchone()
            if state is None or state["phase"] in TERMINAL_PHASES | {"cancelling"}:
                return
            finished = _now()
            connection.execute(
                "UPDATE runs SET phase = 'finished', outcome = 'error', reason = ?, "
                "failing_obligation = NULL, detail = ?, finished_at = ? WHERE id = ?",
                (reason, detail, finished, run_id),
            )
            connection.execute(
                "UPDATE attempts SET phase = 'finished', outcome = 'error', reason = ?, detail = ? "
                "WHERE run_id = ? AND phase NOT IN ('finished', 'cancelled')",
                (reason, detail, run_id),
            )
            connection.execute("UPDATE jobs SET phase = 'finished' WHERE id = ?", (job_id,))
    finally:
        connection.close()


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
    "fail_unbound_run",
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
    "placeholder_contract_sha",
    "retry_attempt",
    "reviewed_scope_state",
    "start_run",
]
