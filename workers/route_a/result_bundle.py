#!/usr/bin/env python3
"""Collect a Route A result bundle (V0-V5) from a work directory.

The collector never runs the candidate and never decides an outcome. It
validates hashes, reads evidence files that a real guest run produced, and
writes result-bundle.json with per-gate status/details/evidence. Missing or
malformed evidence becomes not_run / inconclusive, never pass.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import zipfile

RESULTS = {"pass", "fail", "not_run", "inconclusive", "unsupported"}
OBLIGATIONS = ("V0", "V1", "V2", "V3", "V4", "V5")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def canonical_json_hash(obj):
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def rel(base, path):
    return os.path.relpath(path, base).replace("\\", "/")


def evidence_entry(base, path, role):
    if not path or not os.path.isfile(path):
        return None
    return {"role": role, "relative_path": rel(base, path), "sha256": sha256_file(path),
            "size_bytes": os.path.getsize(path)}


def gate(result, details=None, evidence=None, missing=None):
    if result not in RESULTS:
        raise SystemExit(f"invalid gate result: {result}")
    return {"status": result, "details": details or {}, "evidence": evidence or [],
            "missing": missing or []}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--zip", dest="zip_path")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--image-manifest")
    ap.add_argument("--isolation", help="JSON with uid/caps/nnp/no_egress/...")
    ap.add_argument("--formal-run", help="JSON proving a reverted-snapshot run")
    ap.add_argument("--generated-at")
    args = ap.parse_args(argv)

    job = args.job_dir
    work = args.work_dir
    route = os.path.join(work, "route_a")
    original = os.path.join(job, "original.elf")
    contract = os.path.join(job, "contract.json")
    valid = os.path.join(job, "tests", "valid.json")
    invalid = os.path.join(job, "tests", "invalid.json")
    candidate = os.path.join(route, "candidate.elf")
    status_path = os.path.join(route, "status.json")
    edit = os.path.join(route, "edit-manifest.json")
    audit = os.path.join(route, "audit.json")
    readelf = os.path.join(route, "readelf-audit.txt")
    log = os.path.join(route, "route_a.log")

    for p in (original, contract, valid, invalid, candidate, status_path):
        if not os.path.isfile(p):
            raise SystemExit(f"missing required artifact: {p}")

    v = load_json(valid)
    i = load_json(invalid)
    contract_hash = canonical_json_hash(load_json(contract))
    set_payload = {
        "schema_version": 1,
        "case_id": v["case_id"],
        "original_sha256": v["original_sha256"],
        "manifests": [
            {"name": "invalid.json", "sha256": sha256_file(invalid)},
            {"name": "valid.json", "sha256": sha256_file(valid)},
        ],
    }
    set_hash = canonical_json_hash(set_payload)
    original_hash = sha256_file(original)
    candidate_hash = sha256_file(candidate)
    status = load_json(status_path)

    evidence = []
    for entry in (evidence_entry(job, original, "original"),
                  evidence_entry(job, contract, "contract"),
                  evidence_entry(job, valid, "valid_manifest"),
                  evidence_entry(job, invalid, "invalid_manifest"),
                  evidence_entry(work, candidate, "candidate"),
                  evidence_entry(work, status_path, "status"),
                  evidence_entry(work, edit, "edit_manifest"),
                  evidence_entry(work, audit, "audit"),
                  evidence_entry(work, readelf, "readelf"),
                  evidence_entry(work, log, "worker_log")):
        if entry:
            evidence.append(entry)

    # V0 identity/preconditions
    v0_missing = []
    if original_hash != v.get("original_sha256"):
        v0_missing.append("original hash != manifest original_sha256")
    if candidate_hash != status.get("candidate", {}).get("sha256"):
        v0_missing.append("candidate hash != status.candidate.sha256")
    if contract_hash != status.get("contract_sha256"):
        v0_missing.append("contract hash != status.contract_sha256")
    if set_hash != status.get("manifest_set_sha256"):
        v0_missing.append("manifest-set hash != status.manifest_set_sha256")
    v0 = gate("pass" if not v0_missing else "fail",
              {"original_sha256": original_hash, "candidate_sha256": candidate_hash,
               "contract_sha256": contract_hash, "manifest_set_sha256": set_hash},
              [e for e in evidence if e["role"] in ("original", "contract", "valid_manifest",
                                                    "invalid_manifest", "candidate", "status")],
              v0_missing)

    # V1 structure/placement
    lie = os.path.join(work, "evidence", "v1", "lief.json")
    ev1 = [e for e in evidence if e["role"] in ("edit_manifest", "audit", "readelf")]
    if os.path.isfile(lie):
        ev1.append(evidence_entry(work, lie, "lief"))
    audit_obj = load_json(audit) if os.path.isfile(audit) else {}
    v1_missing = []
    if not audit_obj.get("pass"):
        v1_missing.append("audit.json pass != true")
    if not os.path.isfile(readelf):
        v1_missing.append("readelf-audit.txt missing")
    if not os.path.isfile(edit):
        v1_missing.append("edit-manifest.json missing")
    if not os.path.isfile(lie):
        v1_missing.append("LIEF structural view missing (evidence/v1/lief.json)")
    v1 = gate("pass" if not v1_missing else ("inconclusive" if ev1 else "not_run"),
              {"audit_pass": bool(audit_obj.get("pass"))}, ev1, v1_missing)

    # V2 interface/compatibility
    v2_path = os.path.join(work, "evidence", "v2", "interface.json")
    if os.path.isfile(v2_path):
        obj = load_json(v2_path)
        v2 = gate(obj.get("status", "not_run"), obj.get("details", {}),
                  [evidence_entry(work, v2_path, "interface")], obj.get("missing", []))
    else:
        v2 = gate("not_run", {}, [], ["interface evidence missing"])

    # V3 concrete behaviour
    formal_path = args.formal_run or os.path.join(work, "evidence", "v3", "formal-run.json")
    repaired_path = os.path.join(work, "evidence", "v3", "manifest-repaired.json")
    v3_ev = []
    if os.path.isfile(formal_path):
        v3_ev.append(evidence_entry(work, formal_path, "formal_run"))
    if os.path.isfile(repaired_path):
        v3_ev.append(evidence_entry(work, repaired_path, "manifest_repaired"))
    if os.path.isfile(formal_path):
        fr = load_json(formal_path)
        cases = fr.get("cases", [])
        ok = (fr.get("reverted") is True
              and fr.get("candidate_sha256_before") == candidate_hash
              and fr.get("candidate_sha256_after") == candidate_hash
              and len(cases) == 6 and all(c.get("ok") for c in cases))
        v3 = gate("pass" if ok else "fail",
                  {"reverted": bool(fr.get("reverted")), "cases": len(cases),
                   "candidate_sha256_before": fr.get("candidate_sha256_before"),
                   "candidate_sha256_after": fr.get("candidate_sha256_after")},
                  v3_ev, [] if ok else ["formal-run cases not 6/6 or hash mismatch"])
    elif os.path.isfile(repaired_path):
        mr = load_json(repaired_path)
        v3 = gate("inconclusive",
                  {"repaired_pass": bool(mr.get("repaired_pass")),
                   "passed": mr.get("passed"), "total": mr.get("total"),
                   "note": "non-formal run (not a reverted snapshot)"},
                  v3_ev, ["formal reverted-snapshot run missing"])
    else:
        v3 = gate("not_run", {}, [], ["behaviour evidence missing"])

    # V4 symbolic
    v4_path = os.path.join(work, "evidence", "v4", "symbolic.json")
    if os.path.isfile(v4_path):
        obj = load_json(v4_path)
        v4 = gate(obj.get("status", "not_run"), obj.get("details", {}),
                  [evidence_entry(work, v4_path, "symbolic")], obj.get("missing", []))
    else:
        v4 = gate("not_run", {}, [], ["symbolic evidence missing"])

    # V5 reproducibility
    v5_path = os.path.join(work, "evidence", "v5", "replay.json")
    if os.path.isfile(v5_path):
        obj = load_json(v5_path)
        ok = obj.get("independent_replay") is True and obj.get("candidate_sha256") == candidate_hash
        v5 = gate("pass" if ok else "fail", obj.get("details", {}),
                  [evidence_entry(work, v5_path, "replay")],
                  [] if ok else ["independent replay missing or candidate hash mismatch"])
    else:
        v5 = gate("not_run", {}, [], ["replay evidence missing"])

    image = {}
    if args.image_manifest and os.path.isfile(args.image_manifest):
        image = {"manifest_path": rel(work, args.image_manifest),
                 "manifest_sha256": sha256_file(args.image_manifest)}
    isolation = load_json(args.isolation) if args.isolation and os.path.isfile(args.isolation) else {}

    now = args.generated_at or _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    bundle = {
        "schema_version": 1,
        "job_id": args.job_id,
        "route": "A",
        "generated_at": now,
        "identity": {
            "original_sha256": original_hash,
            "original_size": os.path.getsize(original),
            "contract_sha256": contract_hash,
            "manifest_set_sha256": set_hash,
            "valid_sha256": sha256_file(valid),
            "invalid_sha256": sha256_file(invalid),
            "candidate_sha256": candidate_hash,
            "candidate_size": os.path.getsize(candidate),
        },
        "environment": {"image": image, "isolation": isolation,
                        "tool_versions": status.get("tool_versions", {})},
        "gates": {name: {"V0": v0, "V1": v1, "V2": v2, "V3": v3, "V4": v4, "V5": v5}[name]
                  for name in OBLIGATIONS},
        "evidence": evidence,
        "limitations": [
            "No outcome is computed here; the acceptance engine owns the six outcome labels.",
            "A non-formal run cannot satisfy V3; it is recorded as inconclusive.",
            "V4 is function-level under explicit assumptions; it is never whole-program verification.",
        ],
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(bundle, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps({k: v["status"] for k, v in bundle["gates"].items()}, indent=2))

    if args.zip_path:
        with zipfile.ZipFile(args.zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(args.out, "result-bundle.json")
            for e in evidence:
                p = os.path.join(work if e["relative_path"].startswith(("route_a/", "evidence/")) else job,
                                 e["relative_path"])
                if os.path.isfile(p):
                    z.write(p, e["relative_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
