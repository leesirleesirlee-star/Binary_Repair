#!/usr/bin/env python3
"""Ingest a self-describing execution bundle into the controller store.

The guest writes a bundle directory; this host-side adapter verifies paths,
symlinks, sizes and hashes before registering artifacts and handing a result
bundle to the worker.  It is deliberately separate from ``host_importer``:
that module imports a declarative result JSON, while this module is the only
place that accepts guest files.

Expected ``bundle.json``::

    {
      "schema_version": 1,
      "route": "route_a" | "route_b",
      "candidate_sha256": "<64 hex>",
      "gates": {
        "V0": {"status": "passed", "artifacts": ["candidate.elf"]},
        ...
      },
      "analysis": [{"role": "analysis_v3", "path": "analysis/v3.json"}],
      "comparison": "behavior-comparison.json"
    }

``gates`` may be omitted; then all gates are ``not_run``.  A passed gate must
name at least one artifact.  No file is read from outside the bundle root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.assistant_key_store import is_secret_file
from app.domain.analysis import ANALYSIS_ROLES, AnalysisArtifacts
from app.domain.interface import InterfaceCheckError, check_interface
from app.domain.v4 import V4EvidenceError, validate_v4_evidence
from app.services import worker
from tools.host_importer import SHA256, ResultReceipt, receive_result

GATES = ("V0", "V1", "V2", "V3", "V4", "V5")
STATUS = {"passed", "failed", "not_run", "unknown", "timed_out"}
LEGACY_STATUS = {"pass": "passed", "passed": "passed", "fail": "failed",
                 "failed": "failed", "not_run": "not_run", "unknown": "unknown",
                 "timed_out": "timed_out"}
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


class IngestError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if not relative or rel.anchor or ".." in rel.parts:
        raise IngestError(f"artifact path must be relative: {relative!r}")
    root_resolved = root.resolve()
    raw = root_resolved
    for piece in rel.parts:
        raw = raw / piece
        if raw.is_symlink():
            raise IngestError(f"artifact path is a symlink: {relative!r}")
    candidate = raw.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise IngestError(f"artifact path escapes bundle: {relative!r}") from exc
    if not candidate.is_file():
        raise IngestError(f"artifact is missing: {relative!r}")
    return candidate


def _read_handoff(bundle: Path) -> dict[str, Any]:
    from scripts.check_handoff import validate
    errors, meta = validate(bundle)
    if errors:
        raise IngestError("legacy handoff rejected: " + "; ".join(errors))
    return meta


def _legacy_payload(bundle: Path, route: str) -> tuple[dict[str, Any], list[str]]:
    """Accept INTERFACE-v1 handoff.json + SHA256SUMS as a compatibility path.

    The old package has no bundle.json, so gates are read from an optional
    result-bundle.json (the same shape used by Route A) or default to not_run.
    Artifact paths are still validated by _register_file.
    """
    meta = _read_handoff(bundle)
    if meta.get("route") != route:
        raise IngestError(f"legacy handoff route {meta.get('route')!r} != {route!r}")
    raw_meta = json.loads((bundle / "handoff.json").read_text(encoding="utf-8"))
    files = [str(x) for x in raw_meta.get("files") or []]
    result_path = bundle / "result-bundle.json"
    if result_path.is_file():
        # A gate-carrying file that is not hashed and not declared is exactly
        # how an unverified bundle would slip past review.  Require both.
        if "result-bundle.json" not in files:
            raise IngestError("result-bundle.json must be listed in handoff.json and SHA256SUMS")
        if "result-bundle.json" not in _sums_names(bundle):
            raise IngestError("result-bundle.json must be listed in handoff.json and SHA256SUMS")
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    identity = result.get("identity") or {}
    candidate = identity.get("candidate_sha256")
    if not candidate and (bundle / "candidate.elf").is_file():
        candidate = _sha256(bundle / "candidate.elf")
    if not isinstance(candidate, str) or len(candidate) != 64:
        raise IngestError("legacy package has no candidate_sha256/candidate.elf")
    gate_entries = result.get("gates") or {}
    results = {}
    evidence_specs = {}
    for gate in GATES:
        entry = gate_entries.get(gate) or {}
        status = LEGACY_STATUS.get(str(entry.get("status", "not_run")), str(entry.get("status", "not_run")))
        if status not in STATUS:
            raise IngestError(f"legacy gate {gate} has unknown status")
        results[gate] = status
        evidence_specs[gate] = [
            str(item.get("relative_path")) for item in entry.get("evidence") or []
            if isinstance(item, dict) and item.get("relative_path")
        ]
    return (
        {"schema_version": 1, "route": route, "candidate_sha256": candidate,
         "gates": {g: {"status": results[g], "artifacts": evidence_specs[g]} for g in GATES}},
        files,
    )


def _sums_names(bundle: Path) -> set[str]:
    """Return the file names declared by SHA256SUMS, without verifying bytes."""
    path = bundle / "SHA256SUMS"
    if not path.is_file():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            names.add(parts[1].lstrip("* ").strip())
    return names


def _verify_sums(bundle: Path) -> int:
    """If the bundle ships SHA256SUMS, every listed file must match.

    Accepts GNU coreutils text/binary markers (``hash  file`` and ``hash *file``).
    """
    path = bundle / "SHA256SUMS"
    if not path.is_file():
        return 0
    count = 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise IngestError(f"SHA256SUMS:{lineno}: malformed line")
        expected, raw = parts
        rel = raw.lstrip("* ")
        source = _safe_file(bundle, rel)
        if _sha256(source) != expected:
            raise IngestError(f"SHA256SUMS:{lineno}: hash mismatch: {rel}")
        count += 1
    return count


def _read_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / "bundle.json"
    if not path.is_file():
        raise IngestError("bundle.json is missing")
    try:
        data = json.loads(_safe_file(bundle, "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IngestError(f"bundle.json is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise IngestError("bundle.json must be an object")
    if data.get("schema_version") != 1:
        raise IngestError("bundle.json schema_version must be 1")
    candidate = data.get("candidate_sha256")
    if not isinstance(candidate, str) or SHA256.fullmatch(candidate) is None:
        raise IngestError("bundle.json candidate_sha256 must be 64 lowercase hex characters")
    gates = data.get("gates") or {}
    if not isinstance(gates, dict):
        raise IngestError("bundle.json gates must be an object")
    for gate, entry in gates.items():
        if gate not in GATES:
            raise IngestError(f"unknown gate: {gate}")
        if not isinstance(entry, dict):
            raise IngestError(f"gate {gate} must be an object")
        if str(entry.get("status", "not_run")) not in STATUS:
            raise IngestError(f"gate {gate} has unknown status")
    return data


def _register_file(
    *,
    receipt: ResultReceipt,
    bundle: Path,
    relative: str,
    role: str,
) -> str:
    source = _safe_file(bundle, relative)
    size = source.stat().st_size
    if size > MAX_FILE_BYTES:
        raise IngestError(f"artifact too large: {relative}")
    job_id, attempt_id = receipt.binding["job_id"], receipt.binding["attempt_id"]
    destination_rel = f"jobs/{job_id}/evidence/{attempt_id}/{Path(relative).name}"
    destination = receipt.data_dir / destination_rel
    digest = _sha256(source)
    row = receipt.connection.execute(
        "SELECT id, sha256, attempt_id FROM artifacts WHERE job_id = ? AND relative_path = ?",
        (job_id, destination_rel),
    ).fetchone()
    if row is not None:
        if (
            row["attempt_id"] != attempt_id or row["sha256"] != digest
            or not destination.is_file() or _sha256(destination) != digest
        ):
            raise IngestError(f"conflicting existing artifact: {destination_rel}")
        return str(row["id"])

    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        receipt.created.append(destination)
        shutil.copyfileobj(input_file, output_file)
    if _sha256(destination) != digest:
        raise IngestError(f"artifact changed during receipt: {relative}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifact_id = uuid.uuid4().hex
    receipt.connection.execute(
        "INSERT INTO artifacts "
        "(id, job_id, attempt_id, relative_path, sha256, size_bytes, media_type, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'application/octet-stream', ?, ?)",
        (artifact_id, job_id, attempt_id, destination_rel, digest, size, role, now),
    )
    return artifact_id


def ingest(
    *,
    data_dir: str | Path,
    job_id: str,
    run_id: str,
    attempt_id: str,
    route: str,
    bundle: str | Path,
    trigger: bool = True,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    try:
        with receive_result(
            data_dir=data_dir, job_id=job_id, run_id=run_id, attempt_id=attempt_id, route=route,
        ) as receipt:
            result = _ingest(receipt, Path(bundle))
    except ValueError as exc:
        if isinstance(exc, IngestError):
            raise
        raise IngestError(str(exc)) from exc
    imported = result["imported"]
    imported["worker_processed"] = worker.run_once(data_dir=data_dir) if trigger and imported["changed"] else 0
    return result


def _ingest(receipt: ResultReceipt, bundle: Path) -> dict[str, Any]:
    route = receipt.binding["route"]
    target_entry = ""
    row = receipt.connection.execute(
        "SELECT c.body_json FROM contracts AS c JOIN runs AS r ON c.id = r.contract_id "
        "AND c.job_id = r.job_id WHERE r.id = ?", (receipt.binding["run_id"],),
    ).fetchone()
    if row is not None:
        try:
            target_entry = str(json.loads(row["body_json"]).get("target_entry") or "")
        except (TypeError, ValueError):
            target_entry = ""
    if not bundle.is_dir():
        raise IngestError("bundle must be a directory")
    if not (bundle / "bundle.json").is_file() and (bundle / "handoff.json").is_file():
        manifest, legacy_files = _legacy_payload(bundle, route)
    else:
        manifest = _read_manifest(bundle)
        legacy_files = []
    _verify_sums(bundle)
    receipt.validate_binding(manifest)
    v0 = (manifest.get("gates") or {}).get("V0") or {}
    if v0.get("status") == "passed":
        names = v0.get("artifacts")
        if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
            raise IngestError("gate V0 passed without evidence")
        if not any(_sha256(_safe_file(bundle, name)) == manifest["candidate_sha256"] for name in names):
            raise IngestError(
                "gate V0 evidence does not contain the declared candidate "
                f"{manifest['candidate_sha256']}"
            )

    total = 0
    evidence: dict[str, str] = {}
    results: dict[str, str] = {}
    details: dict[str, str] = {}
    for gate in GATES:
        entry = (manifest.get("gates") or {}).get(gate) or {}
        status = str(entry.get("status", "not_run"))
        names = entry.get("artifacts") or []
        if not isinstance(names, list):
            raise IngestError(f"gate {gate} artifacts must be a list")
        names = list(names)

        if gate == "V2":
            # Do not trust a measured observation's pass:true. Recompute the
            # five static checks from the binary pair; only 5/5 becomes passed.
            candidate_for_v2 = bundle / "candidate.elf"
            if not candidate_for_v2.is_file() and (bundle / "route_a/candidate.elf").is_file():
                candidate_for_v2 = bundle / "route_a/candidate.elf"
            try:
                report = check_interface(
                    bundle / "original.elf",
                    candidate_for_v2,
                    target_entry,
                )
            except InterfaceCheckError as exc:
                report = {"pass": False, "checks": {}, "score": {"passed": 0, "total": 5}, "details": {"error": str(exc)}}
            if report["pass"]:
                status = "passed"
                scored = bundle / "_v2-interface-scored.json"
                scored.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                names.append(scored.name)
                details["V2"] = f"{report['score']['passed']}/{report['score']['total']} static interface checks passed"
            else:
                status = "not_run"
                failed = [name for name, value in report.get("checks", {}).items() if not value]
                details["V2"] = "V2 static interface check not passed" + (
                    ": " + ", ".join(failed) if failed else ""
                )

        if gate == "V4" and status == "passed":
            v4_names = [
                name for name in names
                if isinstance(name, str) and Path(name).name == "v4-symbolic.json"
            ]
            json_names = v4_names or [
                name for name in names if isinstance(name, str) and name.endswith(".json")
            ]
            if not json_names:
                status = "unknown"
                details["V4"] = "V4 passed without typed evidence; NOT VERIFIED"
            else:
                try:
                    payload = json.loads(_safe_file(bundle, json_names[0]).read_text(encoding="utf-8"))
                    validate_v4_evidence(
                        payload,
                        candidate_sha256=manifest["candidate_sha256"],
                        original_sha256=receipt.binding["original_sha256"],
                        require_independent=True,
                    )
                except (OSError, ValueError, json.JSONDecodeError, V4EvidenceError) as exc:
                    status = "not_run"
                    details["V4"] = f"V4 not_run: {exc}"

        results[gate] = status
        ids = []
        ids_by_name: dict[str, str] = {}
        for name in names:
            if not isinstance(name, str):
                raise IngestError(f"gate {gate} artifact name must be a string")
            path = _safe_file(bundle, name)
            total += path.stat().st_size
            if total > MAX_TOTAL_BYTES:
                raise IngestError("bundle exceeds total size limit")
            artifact_id = _register_file(receipt=receipt, bundle=bundle,
                                         relative=name, role=f"{gate.lower()}_evidence")
            ids.append(artifact_id)
            ids_by_name[name] = artifact_id
        if status == "passed" and not ids:
            raise IngestError(f"gate {gate} passed without evidence")
        if ids and status == "passed":
            # A gate can carry several files (identity carries original, candidate,
            # manifests).  V0 is bound only by content: the candidate is the file
            # whose bytes match candidate_sha256, and its absence is a rejection.
            # There is deliberately no fallback to the first entry - that would
            # bind V0 to whatever the bundle happened to list first.
            #
            # M5 017ca87 reached the same rule independently; this is the single
            # implementation, and the message below stays stable because record
            # 101 and its test assert on it.
            if gate == "V0":
                matches = [
                    artifact_id
                    for name, artifact_id in ids_by_name.items()
                    if _sha256(_safe_file(bundle, name)) == manifest["candidate_sha256"]
                ]
                if not matches:
                    raise IngestError(
                        "gate V0 evidence does not contain the declared candidate "
                        f"{manifest['candidate_sha256']}"
                    )
                evidence[gate] = matches[0]
            elif gate == "V2" and "_v2-interface-scored.json" in ids_by_name:
                evidence[gate] = ids_by_name["_v2-interface-scored.json"]
            else:
                evidence[gate] = ids[0]

    analysis_ids = []
    for item in manifest.get("analysis") or []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise IngestError("analysis entries need path")
        role = str(item.get("role") or "analysis")
        if role not in ANALYSIS_ROLES and not role.startswith("analysis_"):
            raise IngestError(f"unknown analysis role: {role}")
        path = _safe_file(bundle, item["path"])
        if role in ANALYSIS_ROLES:
            try:
                AnalysisArtifacts.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                raise IngestError(f"invalid analysis artifact {item['path']}: {exc}") from exc
        analysis_ids.append(_register_file(
            receipt=receipt, bundle=bundle,
            relative=item["path"], role=role,
        ))
    if manifest.get("comparison"):
        _register_file(receipt=receipt, bundle=bundle, relative=str(manifest["comparison"]),
                       role="behavior_comparison")

    for name in legacy_files:
        # result-bundle.json gets its own role below; registering it as a
        # generic legacy_artifact first would win the path and leave the role
        # wrong (M4 ac84862 finding).
        if name in ("handoff.json", "SHA256SUMS", "result-bundle.json"):
            continue
        _register_file(receipt=receipt, bundle=bundle, relative=name, role="legacy_artifact")

    supplemental_id = None
    legacy_result = bundle / "result-bundle.json"
    if legacy_result.is_file():
        supplemental_id = _register_file(
            receipt=receipt,
            bundle=bundle, relative="result-bundle.json",
            role="legacy_result_bundle",
        )

    payload = {
        "route": route,
        "candidate_sha256": manifest["candidate_sha256"],
        "results": results,
        "evidence": evidence,
        "details": {
            "analysis_artifact_ids": analysis_ids,
            "legacy_result_bundle_id": supplemental_id,
            **details,
        },
    }
    imported = receipt.store(payload)
    return {"imported": imported, "evidence": evidence, "analysis_artifact_ids": analysis_ids}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/route_bundle_ingest.py")
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument("--route", required=True, choices=("route_a", "route_b"))
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--no-trigger", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = ingest(data_dir=args.data_dir, job_id=args.job_id, run_id=args.run_id,
                        attempt_id=args.attempt_id, route=args.route,
                        bundle=args.bundle, trigger=not args.no_trigger)
    except (IngestError, OSError, ValueError) as exc:
        print(f"route_bundle_ingest: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
