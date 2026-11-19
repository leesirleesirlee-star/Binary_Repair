"""Persist V0-V5 obligations for one attempt.

The acceptance engine decides; this module only gives the worker a single write
path for ``obligations``. A ``passed`` result must point at an evidence artifact;
V0 falls back to the job's original artifact, every other gate must supply one.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Mapping

from app.db.connection import connect
from app.domain.acceptance import Obligation
from app.domain.evidence import verify_artifact
from app.domain.interface import InterfaceCheckError, validate_interface_report
from app.domain.outcomes import CheckResult
from app.domain.scopes import scope_for_kind
from app.domain.v4 import V4EvidenceError, validate_v4_evidence
from app.services.orchestration import GATES, build_obligations


class ObligationWriteError(ValueError):
    """Raised when a passed obligation has no usable evidence artifact."""


def _original_artifact(connection: Any, job_id: str) -> str | None:
    row = connection.execute(
        "SELECT id FROM artifacts WHERE job_id = ? AND role = 'original' LIMIT 1",
        (job_id,),
    ).fetchone()
    return None if row is None else str(row["id"])


def persist_obligations(
    *,
    data_dir: str | Path,
    job_id: str,
    attempt_id: str,
    results: Mapping[str, str | Any],
    evidence: Mapping[str, str] | None = None,
    details: Mapping[str, str] | None = None,
    candidate_sha256: str | None = None,
) -> int:
    """Upsert one row per V0-V5 gate for ``attempt_id``.

    ``evidence`` maps gate ids (``V0``..``V5``) to artifact ids. V0 may omit it
    and will use the job's original artifact. Any other ``passed`` gate without
    evidence is rejected rather than silently written.
    """

    evidence = evidence or {}
    details = dict(details or {})
    obligations = build_obligations(results)

    connection = connect(data_dir)
    try:
        original = _original_artifact(connection, job_id)
        job_row = connection.execute(
            "SELECT original_sha256 FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        job_original = str(job_row["original_sha256"]) if job_row is not None else None
        target_entry = ""
        run_row = connection.execute(
            "SELECT r.contract_id FROM attempts a JOIN runs r ON r.id = a.run_id "
            "WHERE a.id = ? AND r.job_id = ?",
            (attempt_id, job_id),
        ).fetchone()
        if run_row is not None:
            contract_row = connection.execute(
                "SELECT body_json FROM contracts WHERE id = ? AND job_id = ?",
                (run_row["contract_id"], job_id),
            ).fetchone()
            if contract_row is not None:
                try:
                    target_entry = str(
                        json.loads(contract_row["body_json"]).get("target_entry") or ""
                    )
                except (TypeError, ValueError):
                    target_entry = ""

        # A V2 passed claim is only usable when its scored report has all five
        # checks. A measured observation's pass:true is not a verdict.
        for index, (gate, obligation) in enumerate(
            zip((item[0] for item in GATES), obligations)
        ):
            if gate != "V2" or obligation.result != CheckResult.PASSED:
                continue
            reason = None
            artifact_id = evidence.get("V2")
            if not artifact_id:
                reason = "missing V2 evidence artifact"
            elif not candidate_sha256 or not job_original:
                reason = "candidate/original hash unavailable"
            else:
                try:
                    verified = verify_artifact(
                        data_dir=data_dir,
                        job_id=job_id,
                        attempt_id=attempt_id,
                        artifact_id=str(artifact_id),
                        gate="V2",
                    )
                    payload = json.loads(
                        (Path(data_dir) / str(verified["relative_path"])).read_text(
                            encoding="utf-8"
                        )
                    )
                    validate_interface_report(
                        payload,
                        candidate_sha256=candidate_sha256,
                        original_sha256=job_original,
                        target_entry=target_entry,
                    )
                except (OSError, ValueError, json.JSONDecodeError, InterfaceCheckError) as exc:
                    reason = str(exc)
            if reason is not None:
                obligations[index] = Obligation(
                    kind=obligation.kind,
                    required=obligation.required,
                    result=CheckResult.NOT_RUN,
                    detail=f"V2 interface not verified: {reason}",
                )
                details["V2"] = obligations[index].detail

        # A V4 passed claim is only usable when its typed evidence validates
        # against the candidate and original. Otherwise it becomes NOT VERIFIED
        # (unknown), never a pass just because the file hash matched.
        for index, (gate, obligation) in enumerate(
            zip((item[0] for item in GATES), obligations)
        ):
            if gate != "V4" or obligation.result != CheckResult.PASSED:
                continue
            reason = None
            artifact_id = evidence.get("V4")
            if not artifact_id:
                reason = "missing V4 evidence artifact"
            elif not candidate_sha256 or not job_original:
                reason = "candidate/original hash unavailable"
            else:
                try:
                    verified = verify_artifact(
                        data_dir=data_dir,
                        job_id=job_id,
                        attempt_id=attempt_id,
                        artifact_id=str(artifact_id),
                        gate="V4",
                    )
                    payload = json.loads(
                        (Path(data_dir) / str(verified["relative_path"])).read_text(
                            encoding="utf-8"
                        )
                    )
                    validate_v4_evidence(
                        payload,
                        candidate_sha256=candidate_sha256,
                        original_sha256=job_original,
                        require_independent=True,
                    )
                except (OSError, ValueError, json.JSONDecodeError, V4EvidenceError) as exc:
                    reason = str(exc)
            if reason is not None:
                obligations[index] = Obligation(
                    kind=obligation.kind,
                    required=obligation.required,
                    result=CheckResult.NOT_RUN,
                    detail=f"V4 not_run: {reason}",
                )
                details["V4"] = obligations[index].detail

        rows = []
        for gate, obligation in zip((item[0] for item in GATES), obligations):
            artifact_id = evidence.get(gate)
            if gate == "V0" and artifact_id is None:
                artifact_id = original
            if obligation.result.value == "passed":
                if not artifact_id:
                    raise ObligationWriteError(
                        f"{gate} passed requires evidence_artifact_id"
                    )
                try:
                    verify_artifact(
                        data_dir=data_dir,
                        job_id=job_id,
                        attempt_id=attempt_id,
                        artifact_id=str(artifact_id),
                        gate=gate,
                    )
                except Exception as exc:
                    raise ObligationWriteError(str(exc)) from exc
            rows.append(
                (
                    uuid.uuid4().hex,
                    attempt_id,
                    obligation.kind,
                    scope_for_kind(obligation.kind),
                    1 if obligation.required else 0,
                    obligation.result.value,
                    artifact_id,
                    details.get(gate, obligation.detail),
                )
            )

        # V3 has two distinct evidence kinds. The gate row above still carries
        # the behaviour verdict, but the kind itself is persisted so a reader
        # never has to infer "was this a repair or only an unchanged trip?"
        # from a detail string.
        v3_kind = details.get("V3_kind")
        if v3_kind in {"round_trip", "repaired_behaviour"}:
            behaviour = rows[3]
            rows.append(
                (
                    uuid.uuid4().hex,
                    attempt_id,
                    v3_kind,
                    scope_for_kind(v3_kind),
                    0,
                    behaviour[5],
                    behaviour[6],
                    details.get("V3", "V3 behaviour"),
                )
            )

        with connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT r.phase FROM attempts a JOIN runs r ON r.id = a.run_id "
                "WHERE a.id = ? AND r.job_id = ?", (attempt_id, job_id),
            ).fetchone()
            if state is None or state["phase"] in {"finished", "cancelled", "cancelling"}:
                return 0
            for row in rows:
                connection.execute(
                    "INSERT INTO obligations "
                    "(id, attempt_id, kind, scope, required, result, "
                    "evidence_artifact_id, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(attempt_id, kind) DO UPDATE SET "
                    "scope = excluded.scope, required = excluded.required, "
                    "result = excluded.result, "
                    "evidence_artifact_id = excluded.evidence_artifact_id, detail = excluded.detail",
                    row,
                )
    finally:
        connection.close()
    return len(rows)


__all__ = ["ObligationWriteError", "persist_obligations"]
