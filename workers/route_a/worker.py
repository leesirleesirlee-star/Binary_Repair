#!/usr/bin/env python3
"""Route A worker (deterministic fixture mode) — guest-side.

This worker does not execute the candidate and does not decide an outcome.
It validates the frozen inputs, materialises the candidate (from a supplied
file or a stdlib regenerator), writes the route_a/ artifacts, and emits
status.json. Behaviour/structural/symbolic verification is a separate step.

Run only inside the disposable Ubuntu guest, under the broker/jobwrap fixed
operation. Never run it on the host.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys

PHASES = {
    "draft", "screening", "awaiting_review", "queued", "analyzing",
    "generating", "applying", "validating", "roundtrip", "finished",
}
EVIDENCE_RESULTS = {"pass", "fail", "not_run", "inconclusive", "unsupported"}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def canonical_json_hash(obj) -> str:
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def manifest_set_hash(tests_dir: str) -> tuple[str, str, str]:
    valid_path = os.path.join(tests_dir, "valid.json")
    invalid_path = os.path.join(tests_dir, "invalid.json")
    valid = load_json(valid_path)
    invalid = load_json(invalid_path)
    if valid.get("case_id") != invalid.get("case_id"):
        raise SystemExit("valid/invalid manifests disagree on case_id")
    if valid.get("original_sha256") != invalid.get("original_sha256"):
        raise SystemExit("valid/invalid manifests disagree on original_sha256")
    payload = {
        "schema_version": 1,
        "case_id": valid["case_id"],
        "original_sha256": valid["original_sha256"],
        "manifests": [
            {"name": "invalid.json", "sha256": sha256_file(invalid_path)},
            {"name": "valid.json", "sha256": sha256_file(valid_path)},
        ],
    }
    return canonical_json_hash(payload), sha256_file(valid_path), sha256_file(invalid_path)


def build_candidate(args) -> str:
    out = os.path.join(args.out, "candidate.elf")
    if args.candidate:
        shutil.copyfile(args.candidate, out)
    elif args.regen:
        cmd = [sys.executable, args.regen, args.original, out]
        subprocess.run(cmd, check=True, capture_output=True)
    else:
        raise SystemExit("no candidate source: pass --candidate or --regen")
    os.chmod(out, 0o755)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Route A deterministic fixture worker")
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--tests-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--candidate")
    ap.add_argument("--regen")
    ap.add_argument("--expected-candidate")
    ap.add_argument("--expected-contract")
    ap.add_argument("--edit-manifest")
    ap.add_argument("--audit")
    ap.add_argument("--readelf")
    ap.add_argument("--job-id", default="")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    original_hash = sha256_file(args.original)
    contract_obj = load_json(args.contract)
    contract_hash = canonical_json_hash(contract_obj)
    set_hash, valid_hash, invalid_hash = manifest_set_hash(args.tests_dir)

    valid = load_json(os.path.join(args.tests_dir, "valid.json"))
    if original_hash != valid.get("original_sha256"):
        raise SystemExit("original hash does not match manifest original_sha256")
    if args.expected_contract and contract_hash != args.expected_contract:
        raise SystemExit("contract canonical hash mismatch")
    if args.expected_candidate is None:
        raise SystemExit("--expected-candidate is required (fail-closed)")

    candidate = build_candidate(args)
    candidate_hash = sha256_file(candidate)
    if candidate_hash != args.expected_candidate:
        raise SystemExit(f"candidate hash mismatch: {candidate_hash}")

    artifacts = []
    for role, src, dst, media in (
        ("edit_manifest", args.edit_manifest, os.path.join(args.out, "edit-manifest.json"), "application/json"),
        ("audit", args.audit, os.path.join(args.out, "audit.json"), "application/json"),
        ("readelf", args.readelf, os.path.join(args.out, "readelf-audit.txt"), "text/plain"),
    ):
        if src:
            shutil.copyfile(src, dst)
            artifacts.append({"role": role, "relative_path": f"route_a/{os.path.basename(dst)}",
                              "sha256": sha256_file(dst), "size_bytes": os.path.getsize(dst),
                              "media_type": media})

    audit = {}
    if args.audit:
        audit = load_json(args.audit)
    structure_ok = bool(audit.get("pass")) and bool(args.readelf) and bool(args.edit_manifest)

    log_path = os.path.join(args.out, "route_a.log")
    with open(log_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("route_a deterministic worker\n")
        fh.write(f"python: {sys.version.split()[0]}\n")
        fh.write(f"original_sha256: {original_hash}\n")
        fh.write(f"contract_sha256: {contract_hash}\n")
        fh.write(f"manifest_set_sha256: {set_hash}\n")
        fh.write(f"candidate_sha256: {candidate_hash}\n")
        fh.write(f"candidate_source: {args.candidate or args.regen}\n")
    artifacts.append({"role": "log", "relative_path": "route_a/route_a.log",
                      "sha256": sha256_file(log_path), "size_bytes": os.path.getsize(log_path),
                      "media_type": "text/plain"})

    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = {
        "schema_version": 1,
        "route": "A",
        "job_id": args.job_id,
        "phase": "generating",
        "reason_code": None,
        "started_at": now,
        "finished_at": now,
        "original_sha256": original_hash,
        "contract_sha256": contract_hash,
        "manifest_set_sha256": set_hash,
        "candidate": {
            "relative_path": "route_a/candidate.elf",
            "sha256": candidate_hash,
            "size_bytes": os.path.getsize(candidate),
        },
        "artifacts": artifacts,
        "evidence": [
            {"obligation": "structure", "result": "pass" if structure_ok else "not_run",
             "evidence_path": "route_a/audit.json" if structure_ok else None},
            {"obligation": "behaviour", "result": "not_run", "evidence_path": None},
            {"obligation": "interface", "result": "not_run", "evidence_path": None},
            {"obligation": "symbolic", "result": "not_run", "evidence_path": None},
            {"obligation": "reproducibility", "result": "not_run", "evidence_path": None},
        ],
        "tool_versions": {"python": sys.version.split()[0]},
        "manual_interventions": [],
        "warnings": [] if structure_ok else ["structure evidence incomplete (audit/readelf/edit-manifest)"],
    }
    if status["phase"] not in PHASES:
        raise SystemExit("invalid phase")
    for item in status["evidence"]:
        if item["result"] not in EVIDENCE_RESULTS:
            raise SystemExit("invalid evidence result")

    status_path = os.path.join(args.out, "status.json")
    with open(status_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(status, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps({"candidate_sha256": candidate_hash, "status": status_path,
                      "valid_sha256": valid_hash, "invalid_sha256": invalid_hash}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
