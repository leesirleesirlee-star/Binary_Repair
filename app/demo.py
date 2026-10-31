"""Synthetic demo data for GUI-only mode.

All upload/API/export data is English ASCII; Chinese UI labels live in
app/i18n.py. Demo data must never be presented as accepted-patch evidence."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

STAGE_SECONDS = 1.2

ROUTE_STAGES: dict[str, list[str]] = {
    "A": ["screening", "analyzing", "generating", "applying", "validating"],
    "B": ["screening", "analyzing", "roundtrip", "generating", "applying", "validating"],
}

ROUTE_NAMES_EN: dict[str, str] = {
    "A": "Route A - Direct instruction repair",
    "B": "Route B - Recovered C reintegration",
}

JOBS: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_job(job_id: str) -> dict[str, Any] | None:
    return JOBS.get(job_id)


CONTRACT_DEFAULTS = {
    "valid_domain": "Input length 0-64 bytes; out-of-range lengths rejected",
    "forbidden": "Out-of-bounds write to the target buffer (more than 8 bytes)",
    "invalid_outcome": "Return error code 1 without out-of-bounds access or crash",
    "assumptions": "Single process\nLinux x86-64 ELF\nStack protector disabled",
}


def _contract_sha(body: dict[str, Any]) -> str:
    return _sha(json.dumps(body, sort_keys=True, ensure_ascii=True))


def contract_body(
    *,
    valid_domain: str = "",
    forbidden: str = "",
    invalid_outcome: str = "",
    assumptions: str = "",
) -> dict[str, Any]:
    """Normalize the four contract fields (English ASCII, demo defaults when blank)."""

    return {
        "valid_domain": valid_domain.strip() or CONTRACT_DEFAULTS["valid_domain"],
        "forbidden": forbidden.strip() or CONTRACT_DEFAULTS["forbidden"],
        "invalid_outcome": invalid_outcome.strip() or CONTRACT_DEFAULTS["invalid_outcome"],
        "assumptions": [
            line.strip()
            for line in (assumptions or CONTRACT_DEFAULTS["assumptions"]).splitlines()
            if line.strip()
        ],
    }


def build_contract(
    *,
    valid_domain: str = "",
    forbidden: str = "",
    invalid_outcome: str = "",
    assumptions: str = "",
) -> dict[str, Any]:
    body = contract_body(
        valid_domain=valid_domain,
        forbidden=forbidden,
        invalid_outcome=invalid_outcome,
        assumptions=assumptions,
    )
    return {
        "revision": 1,
        "sha256": _contract_sha(body),
        "approved": False,
        "approved_at": None,
        "reviewer_label": None,
        **body,
    }


def update_contract(
    job: dict[str, Any],
    *,
    valid_domain: str = "",
    forbidden: str = "",
    invalid_outcome: str = "",
    assumptions: str = "",
) -> dict[str, Any]:
    """Create a new contract revision and invalidate the previous approval."""

    body = contract_body(
        valid_domain=valid_domain,
        forbidden=forbidden,
        invalid_outcome=invalid_outcome,
        assumptions=assumptions,
    )
    job["contract"] = {
        "revision": job["contract"]["revision"] + 1,
        "sha256": _contract_sha(body),
        "approved": False,
        "approved_at": None,
        "reviewer_label": None,
        **body,
    }
    return job["contract"]


def approve_contract(job: dict[str, Any], *, reviewer_label: str = "") -> dict[str, Any]:
    """Record the reviewer approval for the current contract revision."""

    contract = job["contract"]
    contract["approved"] = True
    contract["approved_at"] = _now_iso()
    contract["reviewer_label"] = reviewer_label.strip() or "reviewer"
    return contract


def create_job(
    original_sha256: str,
    filename: str,
    defect: str,
    valid_tests: str,
    environment: str,
    valid_domain: str,
    forbidden: str,
    invalid_outcome: str,
    assumptions: str,
) -> dict[str, Any]:
    """Register a synthetic demo job."""

    job: dict[str, Any] = {
        "id": "job-" + uuid.uuid4().hex[:8],
        "demo": True,
        "filename": filename,
        "original_sha256": original_sha256,
        "defect": defect.strip() or "strcpy in stack_buf does not check length; overlong input writes out of bounds",
        "valid_tests": [line.strip() for line in valid_tests.splitlines() if line.strip()],
        "environment": environment.strip() or "Ubuntu x86-64 VM (demo: execution disabled)",
        "created_at": _now_iso(),
        "run_started_at": None,
        "cancelled": False,
        "frozen_elapsed": None,
        "phase": "draft",
        "contract": build_contract(
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        ),
        "routes": {
            "A": _route_state("A"),
            "B": _route_state("B"),
        },
    }
    JOBS[job["id"]] = job
    return job


def _route_state(key: str) -> dict[str, Any]:
    return {
        "name": ROUTE_NAMES_EN[key],
        "phase": "draft",
        "outcome": None,
        "outcome_reason": "",
        "stages": [],
        "tests": {"passed": 0, "total": 8},
        "verification": "not_run",
        "compatibility": "not_run",
        "changed_regions": [],
        "manual_interventions": 0,
        "ai_usage": "unavailable",
        "elapsed_s": 0.0,
    }


def start(job: dict[str, Any]) -> None:
    """Advance the demo job through time-based phases."""
    if job["run_started_at"] is None:
        job["run_started_at"] = time.time()
        job["phase"] = "screening"


def cancel(job: dict[str, Any]) -> None:
    """Mark the demo job as cancelled."""
    if job["run_started_at"] is None:
        return
    job["cancelled"] = True
    job["frozen_elapsed"] = _elapsed(job)


def _elapsed(job: dict[str, Any]) -> float:
    if job["frozen_elapsed"] is not None:
        return job["frozen_elapsed"]
    if job["run_started_at"] is None:
        return 0.0
    return max(0.0, time.time() - job["run_started_at"])


def _obligations_for(route: str) -> list[dict[str, Any]]:
    """Return deterministic V0-V5 demo obligations."""
    passed = [
        {
            "id": "V0",
            "kind": "Identity and preconditions",
            "description": "Input hash, contract revision, and test manifest hash match",
        },
        {
            "id": "V1",
            "kind": "Structure and placement",
            "description": "Instruction boundaries, changed intervals, and branch targets are auditable",
        },
        {
            "id": "V2",
            "kind": "Interface and compatibility",
            "description": "Signature, stack/register conventions, and loader execution checks",
        },
    ]
    if route == "A":
        return [
            *[
                {**item, "required": True, "result": "passed", "scope": "frozen job inputs", "reason": "Demo data: passed"}
                for item in passed
            ],
            {
                "id": "V3",
                "kind": "Concrete behavior",
                "description": "Reproducer, invalid-input oracle, valid-input regression",
                "required": True,
                "result": "failed",
                "scope": "Frozen test manifest (8 valid inputs)",
                "reason": "Valid-input regression case 3 failed: return value 7 != 3",
            },
            {
                "id": "V4",
                "kind": "Bounded symbolic security",
                "description": "Candidate-specific model, input domain, out-of-bounds access predicate",
                "required": True,
                "result": "not_run",
                "scope": "n/a",
                "reason": "V3 failed; diagnostic evidence retained and verification not continued",
            },
            {
                "id": "V5",
                "kind": "Reproducibility",
                "description": "Final hashes, versions, replay inputs, independent replay",
                "required": True,
                "result": "not_run",
                "scope": "n/a",
                "reason": "Candidate did not pass prerequisite checks",
            },
        ]
    return [
        *[
            {**item, "required": True, "result": "passed", "scope": "frozen job inputs", "reason": "Demo data: passed"}
            for item in passed
        ],
        {
            "id": "V3",
            "kind": "Concrete behavior",
            "description": "Reproducer, invalid-input oracle, valid-input regression",
            "required": True,
            "result": "passed",
            "scope": "Frozen test manifest (8 valid inputs)",
            "reason": "8/8 passed; invalid input follows the rejection path",
        },
        {
            "id": "V4",
            "kind": "Bounded symbolic security",
            "description": "Candidate-specific model, input domain, out-of-bounds access predicate",
            "required": True,
            "result": "inconclusive",
            "scope": "Function level - entry preconditions - input length 0-64",
            "reason": "Exploration budget (120 s) exhausted; memory-safety obligation unresolved",
        },
        {
            "id": "V5",
            "kind": "Reproducibility",
            "description": "Final hashes, versions, replay inputs, independent replay",
            "required": True,
            "result": "not_run",
            "scope": "n/a",
            "reason": "V4 incomplete; replay not started",
        },
    ]


def _apply_finished(route: dict[str, Any], key: str) -> None:
    """Fill the final synthetic route data."""
    if key == "A":
        route.update(
            {
                "tests": {"passed": 6, "total": 8},
                "verification": "failed",
                "compatibility": "passed",
                "changed_regions": [
                    {
                        "address": "0x0000000000401136",
                        "kind": "in-place edit",
                        "before": "e8 75 fe ff ff",
                        "after": "e8 15 ff ff ff",
                    }
                ],
                "manual_interventions": 3,
                "ai_usage": "unavailable",
                "elapsed_s": round(len(ROUTE_STAGES["A"]) * STAGE_SECONDS, 1),
                "outcome": "rejected",
                "outcome_reason": "Valid-input regression case 3 failed (return value 7 != 3)",
            }
        )
    else:
        route.update(
            {
                "tests": {"passed": 8, "total": 8},
                "verification": "inconclusive",
                "compatibility": "passed",
                "changed_regions": [
                    {
                        "address": "0x00000000004011a0",
                        "kind": "added code region",
                        "before": "original function 0x1a3 bytes",
                        "after": "recompiled 0x1f2 bytes",
                    }
                ],
                "manual_interventions": 1,
                "ai_usage": "8.2k input / 1.4k output tokens",
                "elapsed_s": round(len(ROUTE_STAGES["B"]) * STAGE_SECONDS, 1),
                "outcome": "inconclusive",
                "outcome_reason": "Bounded symbolic verification: exploration budget exhausted; memory-safety obligation unresolved",
            }
        )


def snapshot(job: dict[str, Any]) -> dict[str, Any]:
    """Return the read-only demo snapshot used by pages and the API."""
    elapsed = _elapsed(job)
    cancelled = job["cancelled"]
    started = job["run_started_at"] is not None

    routes: dict[str, dict[str, Any]] = {}
    for key, stage_names in ROUTE_STAGES.items():
        route = job["routes"][key]
        total_seconds = len(stage_names) * STAGE_SECONDS
        route_finished = started and elapsed >= total_seconds
        done_count = min(int(elapsed // STAGE_SECONDS), len(stage_names))

        stages = []
        for index, name in enumerate(stage_names):
            if cancelled:
                state = "done" if index < done_count else ("cancelled" if index == done_count else "pending")
            elif route_finished:
                state = "done"
            elif started and index < done_count:
                state = "done"
            elif started and index == done_count:
                state = "active"
            else:
                state = "pending"
            stages.append({"name": name, "state": state})

        if cancelled:
            route["phase"] = "finished"
            route["outcome"] = "cancelled"
            route["outcome_reason"] = "Operator cancelled (demo data)"
        elif route_finished:
            route["phase"] = "finished"
            if route["outcome"] is None:
                _apply_finished(route, key)
        elif started:
            active_index = min(done_count, len(stage_names) - 1)
            route["phase"] = stage_names[active_index]
        else:
            route["phase"] = "draft"

        route["stages"] = stages
        if route.get("outcome"):
            route["verdict"] = {
                "outcome": route["outcome"],
                "reason": route.get("outcome_reason") or None,
                "failing_obligation": "V3" if key == "A" else "V4",
                "detail": route.get("outcome_reason") or "",
            }
        routes[key] = route

    if cancelled:
        job_phase = "finished"
    elif not started:
        job_phase = "draft"
    elif routes["A"]["phase"] == "finished" and routes["B"]["phase"] == "finished":
        job_phase = "finished"
    else:
        order = ["screening", "analyzing", "roundtrip", "generating", "applying", "validating"]
        phases = [routes["A"]["phase"], routes["B"]["phase"]]
        job_phase = min(phases, key=lambda p: order.index(p) if p in order else 99)

    if cancelled:
        demo_verdict = {
            "outcome": "cancelled",
            "reason": "operator_cancelled",
            "failing_obligation": None,
            "detail": "Operator cancelled (demo data)",
        }
    elif job_phase == "finished":
        # Demo fixture verdict; the GUI never computes this itself.
        demo_verdict = {
            "outcome": "rejected",
            "reason": "valid_input_regression_failed",
            "failing_obligation": "V3",
            "detail": "Demo fixture: required behaviour obligation failed",
        }
    else:
        demo_verdict = None

    snapshot = {
        "id": job["id"],
        "demo": True,
        "filename": job["filename"],
        "original_sha256": job["original_sha256"],
        "defect": job["defect"],
        "valid_tests": job["valid_tests"],
        "environment": job["environment"],
        "created_at": job["created_at"],
        "phase": job_phase,
        "cancelled": cancelled,
        "contract": job["contract"],
        "routes": routes,
        "verdict": demo_verdict,
    }
    return snapshot


def obligations(job: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {"A": _obligations_for("A"), "B": _obligations_for("B")}


def artifacts(job: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return synthetic artifact metadata."""
    base = _sha(job["id"] + job["original_sha256"])[:16]
    return {
        "A": [
            {"name": "candidate-A.elf", "role": "candidate binary", "sha256": _sha(base + "A.elf")},
            {"name": "edit-manifest-A.json", "role": "edit manifest", "sha256": _sha(base + "A.edit")},
        ],
        "B": [
            {"name": "candidate-B.elf", "role": "candidate binary", "sha256": _sha(base + "B.elf")},
            {"name": "recovered-B.c", "role": "recovered C (raw / normalized / repaired)", "sha256": _sha(base + "B.c")},
            {"name": "roundtrip-B.json", "role": "unchanged round-trip report", "sha256": _sha(base + "B.rt")},
        ],
    }
