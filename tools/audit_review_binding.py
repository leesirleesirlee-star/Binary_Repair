"""Read-only audit of reviewed-scope binding in one controller data directory.

Reports managed approvals that are not bound to a canonical typed contract and
runs whose recorded scope is not a canonical contract (for example the legacy
identity placeholder). It never rewrites bytes, hashes, or verdicts; affected
results must be treated as unusable evidence pending a new review.

Usage:
    python -m tools.audit_review_binding --data-dir <dir>

Exit code is 0 for both clean and affected data: this is a report, not a gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.db.connection import connect
from app.domain.contracts import SecurityContract
from app.services import jobs


def _canonical(row: Any) -> SecurityContract | None:
    try:
        body = json.loads(row["body_json"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    try:
        return SecurityContract.from_dict(body)
    except (TypeError, ValueError):
        return None


def _meta(payload: str | None) -> dict[str, Any]:
    if not isinstance(payload, str):
        return {}
    try:
        loaded = json.loads(payload)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def audit(data_dir: str | Path) -> dict[str, Any]:
    """Return a report; never raises for ordinary missing or stale data."""
    placeholder = jobs.placeholder_contract_sha()
    connection = connect(data_dir)
    try:
        job_rows = connection.execute("SELECT id FROM jobs ORDER BY created_at").fetchall()
        run_rows = connection.execute("SELECT * FROM runs ORDER BY started_at").fetchall()
        contract_rows = {
            row["id"]: row for row in connection.execute("SELECT * FROM contracts").fetchall()
        }
        meta_rows = {
            row["job_id"]: row["payload_json"]
            for row in connection.execute(
                "SELECT job_id, payload_json FROM controller_job_metadata"
            ).fetchall()
        }
    finally:
        connection.close()

    affected_jobs: list[dict[str, Any]] = []
    for job in job_rows:
        meta = _meta(meta_rows.get(job["id"]))
        if not meta.get("review_v1"):
            continue
        approval = meta.get("approval")
        if not isinstance(approval, dict):
            continue
        reasons: list[str] = []
        if not isinstance(approval.get("typed_contract_sha256"), str):
            reasons.append("managed approval has no typed contract source")
        if not isinstance(approval.get("canonical_contract_sha256"), str):
            reasons.append("managed approval has no canonical contract digest")
        if reasons:
            affected_jobs.append({"job_id": job["id"], "reasons": reasons})

    affected_runs: list[dict[str, Any]] = []
    for run in run_rows:
        reasons = []
        contract = contract_rows.get(run["contract_id"])
        if contract is None:
            reasons.append("run has no approved contract")
        else:
            canonical = _canonical(contract)
            if canonical is None:
                reasons.append("contract is not a canonical security contract")
            else:
                sha = canonical.sha256()
                if sha != str(contract["sha256"]):
                    reasons.append("contract bytes do not match the stored hash")
                if sha != str(run["reviewed_scope_sha256"] or ""):
                    reasons.append("run scope does not match the contract hash")
                if sha == placeholder:
                    reasons.append("run scope is the legacy identity placeholder")
        meta = _meta(meta_rows.get(run["job_id"]))
        approval = meta.get("approval")
        if meta.get("review_v1") and isinstance(approval, dict):
            canonical_sha = approval.get("canonical_contract_sha256")
            if not isinstance(canonical_sha, str) or canonical_sha != str(
                run["reviewed_scope_sha256"] or ""
            ):
                reasons.append("managed approval is not bound to the run scope")
        if reasons:
            affected_runs.append(
                {
                    "run_id": run["id"],
                    "job_id": run["job_id"],
                    "phase": run["phase"],
                    "outcome": run["outcome"],
                    "reasons": reasons,
                }
            )

    return {
        "data_dir": str(data_dir),
        "placeholder_contract_sha256": placeholder,
        "affected_jobs": affected_jobs,
        "affected_runs": affected_runs,
        "unusable_as_evidence": bool(affected_jobs or affected_runs),
        "note": (
            "Read-only report. Historical bytes and verdicts are kept; affected approvals "
            "need a new review before any real execution and affected results are unusable "
            "as evidence."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="controller data directory")
    args = parser.parse_args(argv)
    print(json.dumps(audit(Path(args.data_dir)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
