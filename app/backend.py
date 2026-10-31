"""Data-source abstraction for the GUI.

Modes are selected with BINARYREPAIR_BACKEND: mock (app/demo.py), http
(controller API), and local (controller SQLite store). The GUI only
queries this interface; pages never decide results."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from app import demo, visuals
from app.services import jobs as job_store

BACKEND_ENV = "BINARYREPAIR_BACKEND"
API_BASE_ENV = "BINARYREPAIR_API_BASE"
API_TIMEOUT_ENV = "BINARYREPAIR_API_TIMEOUT"
DATA_DIR_ENV = "BINARYREPAIR_DATA_DIR"
DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_DATA_DIR = "var"

# Route stage names for the placeholder snapshot in local mode (English, no CJK).
ROUTE_STAGES: dict[str, list[str]] = {
    "A": ["screening", "analyzing", "generating", "applying", "validating"],
    "B": ["screening", "analyzing", "roundtrip", "generating", "applying", "validating"],
}
ROUTE_NAMES_EN: dict[str, str] = {
    "A": "Route A - Direct instruction repair",
    "B": "Route B - Recovered C reintegration",
}

_CJK_PATTERN = re.compile(
    r"[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def _canonical_rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


def _route_candidate_sha256(route: dict[str, Any], route_artifacts: list[dict[str, Any]]) -> str | None:
    value = route.get("candidate_sha256")
    if isinstance(value, str) and value:
        return value
    for item in route_artifacts:
        if "candidate" in str(item.get("role", "")).lower():
            sha = item.get("sha256")
            if isinstance(sha, str) and sha:
                return sha
    return None


def _route_outcome(route: dict[str, Any]) -> Any:
    verdict = route.get("verdict")
    if isinstance(verdict, dict) and verdict.get("outcome"):
        return verdict.get("outcome")
    return route.get("outcome")


def _route_verdict_field(route: dict[str, Any], field: str) -> Any:
    verdict = route.get("verdict")
    if isinstance(verdict, dict):
        return verdict.get(field)
    return None


def _artifact_entry(route_key: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "route": route_key,
        "name": item.get("name"),
        "role": item.get("role"),
        "sha256": item.get("sha256"),
        "size_bytes": item.get("size_bytes"),
        "download": bool(item.get("id") and item.get("available", True)),
    }


def _manifest_sha256(artifacts: list[dict[str, Any]], obligations: dict[str, list[dict[str, Any]]]) -> str:
    payload = {"artifacts": artifacts, "obligations": obligations}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def build_export_bundle(
    snapshot: dict[str, Any],
    obligations: dict[str, list[dict[str, Any]]],
    artifacts: dict[str, list[dict[str, Any]]],
    *,
    demo: bool,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the F5 JSON v2 evidence bundle from controller data only."""
    route_artifacts: dict[str, list[dict[str, Any]]] = {}
    for key, items in (artifacts or {}).items():
        route_artifacts[key] = [item for item in (items or []) if isinstance(item, dict)]

    flat_artifacts: list[dict[str, Any]] = []
    for key, items in route_artifacts.items():
        for item in items:
            flat_artifacts.append(_artifact_entry(key, item))

    routes_out: dict[str, dict[str, Any]] = {}
    not_run: list[dict[str, Any]] = []
    for key, route in (snapshot.get("routes") or {}).items():
        route_rows = _canonical_rows(route.get("obligations") or (obligations or {}).get(key))
        route_items = route_artifacts.get(key, [])
        evidence: list[dict[str, Any]] = []
        for row in route_rows:
            artifact_id = row.get("evidence_artifact_id")
            if not artifact_id:
                continue
            matched = next((item for item in route_items if item.get("id") == artifact_id), None)
            evidence.append(
                {
                    "artifact_id": artifact_id,
                    "name": matched.get("name") if matched else None,
                    "sha256": matched.get("sha256") if matched else None,
                    "available": bool(matched and matched.get("available", True)),
                }
            )
        candidate = _route_candidate_sha256(route, route_items)
        outcome = _route_outcome(route)
        verdict = route.get("verdict") if isinstance(route.get("verdict"), dict) else {}
        routes_out[key] = {
            "candidate_sha256": candidate,
            "outcome": outcome,
            "reason": verdict.get("reason") or route.get("outcome_reason"),
            "failing_obligation": verdict.get("failing_obligation"),
            "verification": route.get("verification"),
            "obligations": route_rows,
            "evidence": evidence,
        }
        for row in route_rows:
            if row.get("result") != "passed":
                not_run.append(
                    {
                        "route": key,
                        "obligation": row.get("id") or row.get("kind"),
                        "result": row.get("result"),
                        "reason": row.get("reason") or row.get("detail"),
                    }
                )

    limitations: list[str] = []
    if demo:
        limitations.append("Synthetic demo data; no repair was executed on a real binary.")
    for key, route in routes_out.items():
        if route["candidate_sha256"] is None:
            limitations.append(f"Route {key}: no candidate binary is available.")
        if route["verification"] in (None, "not_run", "not_run"):
            limitations.append(f"Route {key}: verification is not_run.")
    if not flat_artifacts:
        limitations.append("No evidence artifacts are available for download.")

    contract = snapshot.get("contract") or {}
    run = snapshot.get("run") or {}
    obligations_for_manifest = {
        key: _canonical_rows((snapshot.get("routes", {}).get(key) or {}).get("obligations") or (obligations or {}).get(key))
        for key in ("A", "B")
    }
    manifest = _manifest_sha256(flat_artifacts, obligations_for_manifest)
    return {
        "schema_version": 2,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "demo": bool(demo),
        "content_language": "en",
        "disclaimer": "Not production approval",
        "note": "This export is not production approval.",
        "job": {
            "id": snapshot.get("id"),
            "filename": snapshot.get("filename"),
            "original_sha256": snapshot.get("original_sha256"),
            "phase": snapshot.get("phase"),
        },
        "contract": {
            "revision": contract.get("revision"),
            "sha256": contract.get("sha256"),
            "reviewed_scope_sha256": run.get("reviewed_scope_sha256") or contract.get("sha256"),
        },
        "routes": routes_out,
        "artifacts": flat_artifacts,
        "limitations": limitations,
        "not_run": not_run,
        "manifest_sha256": manifest,
    }


def _has_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


def _has_cjk_in_payload(payload: Any) -> bool:
    """Recursively reject CJK characters in JSON payloads."""
    if isinstance(payload, str):
        return _has_cjk(payload)
    if isinstance(payload, dict):
        return any(_has_cjk_in_payload(value) for value in payload.values()) or any(
            _has_cjk_in_payload(key) for key in payload
        )
    if isinstance(payload, (list, tuple)):
        return any(_has_cjk_in_payload(item) for item in payload)
    return False


class BackendError(RuntimeError):
    """Raised when a backend data source cannot fulfil a request."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Backend(Protocol):
    mode: str
    supports_start: bool

    def create_job(self, *, binary_bytes: bytes, filename: str, defect: str, valid_tests: str,
                   environment: str, valid_domain: str, forbidden: str, invalid_outcome: str,
                   assumptions: str) -> str: ...

    def get_snapshot(self, job_id: str) -> dict[str, Any]: ...
    def start(self, job_id: str) -> None: ...
    def cancel(self, job_id: str) -> None: ...
    def update_contract(
        self,
        job_id: str,
        *,
        expected_revision: str,
        valid_domain: str,
        forbidden: str,
        invalid_outcome: str,
        assumptions: str,
    ) -> dict[str, Any]: ...
    def approve_contract(
        self, job_id: str, *, expected_sha256: str, reviewer_label: str
    ) -> dict[str, Any]: ...
    def get_obligations(self, job_id: str) -> dict[str, list[dict[str, Any]]]: ...
    def get_artifacts(self, job_id: str) -> dict[str, list[dict[str, Any]]]: ...
    def get_visuals(self, job_id: str) -> dict[str, Any]: ...
    def export_bundle(self, job_id: str) -> dict[str, Any]: ...


class MockBackend:
    """Synthetic data source backed by app/demo.py."""

    mode = "mock"
    supports_start = True

    def create_job(self, *, binary_bytes: bytes, filename: str, defect: str, valid_tests: str,
                   environment: str, valid_domain: str, forbidden: str, invalid_outcome: str,
                   assumptions: str) -> str:
        job = demo.create_job(
            original_sha256=hashlib.sha256(binary_bytes).hexdigest(),
            filename=filename,
            defect=defect,
            valid_tests=valid_tests,
            environment=environment,
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        )
        return job["id"]

    def _job(self, job_id: str) -> dict[str, Any]:
        job = demo.get_job(job_id)
        if job is None:
            raise BackendError(404, "Job not found.")
        return job

    def get_snapshot(self, job_id: str) -> dict[str, Any]:
        return demo.snapshot(self._job(job_id))

    def start(self, job_id: str) -> None:
        job = self._job(job_id)
        if not job["contract"].get("approved"):
            raise BackendError(409, "Contract is not approved for this job.")
        demo.start(job)

    def cancel(self, job_id: str) -> None:
        demo.cancel(self._job(job_id))

    def update_contract(
        self,
        job_id: str,
        *,
        expected_revision: str,
        valid_domain: str,
        forbidden: str,
        invalid_outcome: str,
        assumptions: str,
    ) -> dict[str, Any]:
        job = self._job(job_id)
        current = job["contract"]["revision"]
        if str(expected_revision).strip() != str(current):
            raise BackendError(
                409,
                "Contract revision is stale: presented "
                f"{expected_revision}, current is {current}. Reload and review the current contract.",
            )
        return demo.update_contract(
            job,
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        )

    def approve_contract(
        self, job_id: str, *, expected_sha256: str, reviewer_label: str
    ) -> dict[str, Any]:
        job = self._job(job_id)
        current = job["contract"]["sha256"]
        if expected_sha256.strip().lower() != current:
            raise BackendError(
                409,
                "Contract approval is stale: the presented hash does not match the current revision.",
            )
        return demo.approve_contract(job, reviewer_label=reviewer_label)

    def get_obligations(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        return demo.obligations(self._job(job_id))

    def get_artifacts(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        return demo.artifacts(self._job(job_id))

    def get_visuals(self, job_id: str) -> dict[str, Any]:
        return visuals.build(self._job(job_id))

    def export_bundle(self, job_id: str) -> dict[str, Any]:
        snapshot = self.get_snapshot(job_id)
        return build_export_bundle(
            snapshot,
            self.get_obligations(job_id),
            self.get_artifacts(job_id),
            demo=bool(snapshot.get("demo")),
        )


class HttpBackend:
    """Data source backed by the controller HTTP API."""

    mode = "http"
    supports_start = True

    def __init__(self, base_url: str | None = None, timeout: float | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = (base_url or os.environ.get(API_BASE_ENV, DEFAULT_API_BASE)).rstrip("/")
        if timeout is None:
            try:
                timeout = float(os.environ.get(API_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS))
            except ValueError:
                timeout = DEFAULT_TIMEOUT_SECONDS
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()


    def _decode(self, response: httpx.Response, where: str) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendError(502, f"Backend returned invalid JSON for {where}.") from exc
        if _has_cjk_in_payload(payload):
            raise BackendError(
                502,
                f"Backend returned non-English data for {where} (language policy violation).",
            )
        return payload

    def _request(self, method: str, path: str, *, where: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(
                502, f"Backend unavailable at {self.base_url}: {exc.__class__.__name__}."
            ) from exc

        if response.status_code >= 400:
            detail = f"Backend request failed for {where}."
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                detail = payload["detail"]
            status = response.status_code if response.status_code in (400, 404, 409, 422) else 502
            raise BackendError(status, detail)

        if response.status_code == 204 or not response.content:
            return None
        return self._decode(response, where)


    def create_job(self, *, binary_bytes: bytes, filename: str, defect: str, valid_tests: str,
                   environment: str, valid_domain: str, forbidden: str, invalid_outcome: str,
                   assumptions: str) -> str:
        payload = self._request(
            "POST",
            "/api/jobs",
            where="create_job",
            files={"binary": (filename, binary_bytes, "application/octet-stream")},
            data={
                "defect": defect,
                "valid_tests": valid_tests,
                "environment": environment,
                "valid_domain": valid_domain,
                "forbidden": forbidden,
                "invalid_outcome": invalid_outcome,
                "assumptions": assumptions,
            },
        )
        job_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise BackendError(502, "Backend create_job response is missing a string 'id'.")
        return job_id

    def get_snapshot(self, job_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/jobs/{job_id}", where="get_snapshot")
        if not isinstance(payload, dict) or "phase" not in payload or "routes" not in payload:
            raise BackendError(502, "Backend snapshot is missing required fields (phase, routes).")
        return payload

    def start(self, job_id: str) -> None:
        self._request("POST", f"/api/jobs/{job_id}/start", where="start")

    def cancel(self, job_id: str) -> None:
        self._request("POST", f"/api/jobs/{job_id}/cancel", where="cancel")

    def get_obligations(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        payload = self._request("GET", f"/api/jobs/{job_id}/obligations", where="get_obligations")
        if not isinstance(payload, dict):
            raise BackendError(502, "Backend obligations response must be an object.")
        return payload

    def update_contract(
        self,
        job_id: str,
        *,
        expected_revision: str,
        valid_domain: str,
        forbidden: str,
        invalid_outcome: str,
        assumptions: str,
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"/api/jobs/{job_id}/contract",
            where="update_contract",
            data={
                "expected_revision": expected_revision,
                "valid_domain": valid_domain,
                "forbidden": forbidden,
                "invalid_outcome": invalid_outcome,
                "assumptions": assumptions,
            },
        )
        contract_payload = payload.get("contract") if isinstance(payload, dict) else None
        if isinstance(contract_payload, dict):
            payload = contract_payload
        if not isinstance(payload, dict) or "revision" not in payload:
            raise BackendError(502, "Backend contract response is missing a revision.")
        return payload

    def approve_contract(
        self, job_id: str, *, expected_sha256: str, reviewer_label: str
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"/api/jobs/{job_id}/approve",
            where="approve_contract",
            data={"expected_sha256": expected_sha256, "reviewer_label": reviewer_label},
        )
        if not isinstance(payload, dict) or not payload.get("approved"):
            raise BackendError(502, "Backend approval response is not approved.")
        return payload

    def get_artifacts(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        payload = self._request("GET", f"/api/jobs/{job_id}/artifacts", where="get_artifacts")
        if not isinstance(payload, dict):
            raise BackendError(502, "Backend artifacts response must be an object.")
        return payload

    def get_visuals(self, job_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/jobs/{job_id}/visuals", where="get_visuals")
        if not isinstance(payload, dict):
            raise BackendError(502, "Backend visuals response must be an object.")
        return payload

    def export_bundle(self, job_id: str) -> dict[str, Any]:
        """Assemble the F5 v2 bundle from the controller snapshot."""
        snapshot = self.get_snapshot(job_id)
        return build_export_bundle(
            snapshot,
            self.get_obligations(job_id),
            self.get_artifacts(job_id),
            demo=bool(snapshot.get("demo")),
        )


class LocalBackend:
    """Data source backed by the local controller SQLite store."""

    mode = "local"
    supports_start = False

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(
            data_dir or os.environ.get(DATA_DIR_ENV, DEFAULT_DATA_DIR)
        ).resolve()


    def _meta_path(self, job_id: str) -> Path:
        return self.data_dir / "jobs" / job_id / "gui_meta.json"

    def _read_meta(self, job_id: str) -> dict[str, Any]:
        path = self._meta_path(job_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_meta(self, job_id: str, payload: dict[str, Any]) -> None:
        path = self._meta_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _record(self, job_id: str) -> job_store.JobRecord:
        record = job_store.get_job(job_id, data_dir=self.data_dir)
        if record is None:
            raise BackendError(404, "Job not found.")
        return record

    @staticmethod
    def _contract_body(
        *,
        valid_domain: str = "",
        forbidden: str = "",
        invalid_outcome: str = "",
        assumptions: str = "",
    ) -> dict[str, Any]:
        return {
            "valid_domain": valid_domain.strip()
            or "Input length 0-64 bytes; out-of-range lengths rejected",
            "forbidden": forbidden.strip()
            or "Out-of-bounds write to the target buffer (more than 8 bytes)",
            "invalid_outcome": invalid_outcome.strip()
            or "Return error code 1 without out-of-bounds access or crash",
            "assumptions": [
                line.strip()
                for line in (
                    assumptions
                    or "Single process\nLinux x86-64 ELF\nStack protector disabled"
                ).splitlines()
                if line.strip()
            ],
        }

    @staticmethod
    def _contract_sha(body: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _contract(
        cls,
        *,
        valid_domain: str = "",
        forbidden: str = "",
        invalid_outcome: str = "",
        assumptions: str = "",
    ) -> dict[str, Any]:
        body = cls._contract_body(
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        )
        return {
            "revision": 1,
            "sha256": cls._contract_sha(body),
            "approved": False,
            "approved_at": None,
            "reviewer_label": None,
            **body,
        }

    @staticmethod
    def _placeholder_routes(phase: str) -> dict[str, dict[str, Any]]:
        routes: dict[str, dict[str, Any]] = {}
        for key, stage_names in ROUTE_STAGES.items():
            routes[key] = {
                "name": ROUTE_NAMES_EN[key],
                "phase": phase,
                "outcome": None,
                "outcome_reason": "",
                "stages": [{"name": name, "state": "pending"} for name in stage_names],
                "tests": {"passed": 0, "total": 8},
                "verification": "not_run",
                "compatibility": "not_run",
                "changed_regions": [],
                "manual_interventions": 0,
                "ai_usage": "unavailable",
                "elapsed_s": 0.0,
            }
        return routes


    def create_job(self, *, binary_bytes: bytes, filename: str, defect: str, valid_tests: str,
                   environment: str, valid_domain: str, forbidden: str, invalid_outcome: str,
                   assumptions: str) -> str:
        record = job_store.create_job(
            original_bytes=binary_bytes,
            filename=filename,
            defect_description=defect or "Defect description not provided.",
            data_dir=self.data_dir,
        )
        self._write_meta(
            record.id,
            {
                "valid_tests": [line.strip() for line in valid_tests.splitlines() if line.strip()],
                "environment": environment.strip()
                or "Ubuntu x86-64 VM (execution not implemented)",
                "contract": self._contract(
                    valid_domain=valid_domain,
                    forbidden=forbidden,
                    invalid_outcome=invalid_outcome,
                    assumptions=assumptions,
                ),
            },
        )
        return record.id

    def get_snapshot(self, job_id: str) -> dict[str, Any]:
        record = self._record(job_id)
        meta = self._read_meta(job_id)
        return {
            "id": record.id,
            "demo": False,
            "data_source": "local-controller",
            "execution": "not_started",
            "filename": record.filename,
            "original_sha256": record.original_sha256,
            "defect": record.defect_description,
            "valid_tests": meta.get("valid_tests", []),
            "environment": meta.get("environment", ""),
            "created_at": record.created_at,
            "phase": record.phase,
            "cancelled": False,
            "contract": meta.get("contract") or self._contract(),
            "routes": self._placeholder_routes(record.phase),
        }

    def start(self, job_id: str) -> None:
        self._record(job_id)
        raise BackendError(
            409, "Local controller mode does not implement run execution yet."
        )

    def cancel(self, job_id: str) -> None:
        self._record(job_id)
        raise BackendError(
            409, "Local controller mode does not implement run execution yet."
        )

    def update_contract(
        self,
        job_id: str,
        *,
        expected_revision: str,
        valid_domain: str,
        forbidden: str,
        invalid_outcome: str,
        assumptions: str,
    ) -> dict[str, Any]:
        self._record(job_id)
        meta = self._read_meta(job_id)
        contract = meta.get("contract") or self._contract()
        if str(expected_revision).strip() != str(contract["revision"]):
            raise BackendError(
                409,
                "Contract revision is stale: presented "
                f"{expected_revision}, current is {contract['revision']}. "
                "Reload and review the current contract.",
            )
        body = self._contract_body(
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        )
        meta["contract"] = {
            "revision": contract["revision"] + 1,
            "sha256": self._contract_sha(body),
            "approved": False,
            "approved_at": None,
            "reviewer_label": None,
            **body,
        }
        self._write_meta(job_id, meta)
        return meta["contract"]

    def approve_contract(
        self, job_id: str, *, expected_sha256: str, reviewer_label: str
    ) -> dict[str, Any]:
        self._record(job_id)
        meta = self._read_meta(job_id)
        contract = meta.get("contract") or self._contract()
        if expected_sha256.strip().lower() != contract["sha256"]:
            raise BackendError(
                409,
                "Contract approval is stale: the presented hash does not match the current revision.",
            )
        contract["approved"] = True
        contract["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        contract["reviewer_label"] = reviewer_label.strip() or "reviewer"
        meta["contract"] = contract
        self._write_meta(job_id, meta)
        return contract

    def get_obligations(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        self._record(job_id)
        identity = {
            "id": "V0",
            "kind": "Identity and preconditions",
            "description": "Job record exists in the controller store and the original hash matches the stored bytes",
            "required": True,
            "result": "passed",
            "scope": "controller job store",
            "reason": "Job record and original hash verified.",
        }
        pending = [
            {
                "id": "V1",
                "kind": "Structure and placement",
                "description": "Instruction boundaries, changed intervals, and branch targets are auditable",
                "required": True,
                "result": "not_run",
                "scope": "n/a",
                "reason": "Repair execution is not implemented by the local controller yet.",
            },
            {
                "id": "V2",
                "kind": "Interface and compatibility",
                "description": "Signature, stack/register conventions, and loader execution checks",
                "required": True,
                "result": "not_run",
                "scope": "n/a",
                "reason": "Repair execution is not implemented by the local controller yet.",
            },
            {
                "id": "V3",
                "kind": "Concrete behavior",
                "description": "Reproducer, invalid-input oracle, valid-input regression",
                "required": True,
                "result": "not_run",
                "scope": "n/a",
                "reason": "Repair execution is not implemented by the local controller yet.",
            },
            {
                "id": "V4",
                "kind": "Bounded symbolic security",
                "description": "Candidate-specific model, input domain, out-of-bounds access predicate",
                "required": True,
                "result": "not_run",
                "scope": "n/a",
                "reason": "Repair execution is not implemented by the local controller yet.",
            },
            {
                "id": "V5",
                "kind": "Reproducibility",
                "description": "Final hashes, versions, replay inputs, independent replay",
                "required": True,
                "result": "not_run",
                "scope": "n/a",
                "reason": "Repair execution is not implemented by the local controller yet.",
            },
        ]
        return {
            "A": [identity, *[dict(item) for item in pending]],
            "B": [dict(identity), *[dict(item) for item in pending]],
        }

    def get_artifacts(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        self._record(job_id)
        return {"A": [], "B": []}

    def get_visuals(self, job_id: str) -> dict[str, Any]:
        self._record(job_id)
        return {
            "available": False,
            "note": "Repair execution is not implemented by the local controller yet.",
        }

    def export_bundle(self, job_id: str) -> dict[str, Any]:
        snapshot = self.get_snapshot(job_id)
        return build_export_bundle(
            snapshot,
            self.get_obligations(job_id),
            self.get_artifacts(job_id),
            demo=False,
        )


def backend_mode() -> str:
    mode = os.environ.get(BACKEND_ENV, "mock").strip().lower() or "mock"
    if mode not in {"mock", "http", "local"}:
        raise ValueError(f"{BACKEND_ENV} must be 'mock', 'http' or 'local', got {mode!r}.")
    return mode


def get_backend() -> Backend:
    """Return the configured backend singleton."""
    mode = backend_mode()
    if mode == "http":
        return HttpBackend()
    if mode == "local":
        return LocalBackend()
    return MockBackend()


def backend_description(backend: Backend) -> str:
    """Describe the active data source for pages and healthz."""
    if backend.mode == "http":
        return f"controller API at {backend.base_url}"  # type: ignore[attr-defined]
    if backend.mode == "local":
        # Do not echo the data directory: it may contain non-ASCII path segments and
        # API payloads must stay English ASCII (language policy).
        return "local controller (SQLite job store)"
    return "synthetic demo data (no execution)"


__all__ = [
    "Backend",
    "BackendError",
    "HttpBackend",
    "LocalBackend",
    "MockBackend",
    "backend_description",
    "backend_mode",
    "get_backend",
]
