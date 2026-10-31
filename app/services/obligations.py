"""Persist V0-V5 obligations for one attempt.

The acceptance engine decides; this module only gives the worker a single write
path for ``obligations``. A ``passed`` result must point at an evidence artifact;
V0 falls back to the job's original artifact, every other gate must supply one.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Mapping

from app.db.connection import connect
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
) -> int:
    """Upsert one row per V0-V5 gate for ``attempt_id``.

    ``evidence`` maps gate ids (``V0``..``V5``) to artifact ids. V0 may omit it
    and will use the job's original artifact. Any other ``passed`` gate without
    evidence is rejected rather than silently written.
    """

    evidence = evidence or {}
    details = details or {}
    obligations = build_obligations(results)

    connection = connect(data_dir)
    try:
        original = _original_artifact(connection, job_id)
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
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND job_id = ?",
                    (artifact_id, job_id),
                ).fetchone()
                if artifact is None:
                    raise ObligationWriteError(
                        f"{gate} evidence artifact is not part of job {job_id}: {artifact_id}"
                    )
            rows.append(
                (
                    uuid.uuid4().hex,
                    attempt_id,
                    obligation.kind,
                    1 if obligation.required else 0,
                    obligation.result.value,
                    artifact_id,
                    details.get(gate, obligation.detail),
                )
            )

        with connection:
            for row in rows:
                connection.execute(
                    "INSERT INTO obligations "
                    "(id, attempt_id, kind, required, result, evidence_artifact_id, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(attempt_id, kind) DO UPDATE SET "
                    "required = excluded.required, result = excluded.result, "
                    "evidence_artifact_id = excluded.evidence_artifact_id, detail = excluded.detail",
                    row,
                )
    finally:
        connection.close()
    return len(rows)


__all__ = ["ObligationWriteError", "persist_obligations"]
