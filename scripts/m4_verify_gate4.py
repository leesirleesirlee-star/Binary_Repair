#!/usr/bin/env python3
"""Host-side verification helper for M4's Gate 4 independent check (JOIN-1).

Reads a delivered Gate 4 evidence package (interface_version 1, see
``scripts/check_handoff.py``) and optionally the Gate 4 record and a finished
controller job. It never parses or executes an ELF: identity is a plain SHA-256 over
the delivered bytes and the Gate 4 rows are JSON/text assertions.

Status vocabulary (fail closed):

* ``PASS``         - the item was checked against the delivered bytes/JSON and is consistent.
* ``FAIL``         - a contradiction: tampered/mismatched hash, inconsistent fields, a
                     six-case row that is not identical, or isolation evidence that
                     contradicts the required controls.
* ``NOT VERIFIED`` - a required file, row, or value is missing, so the item cannot be
                     checked. Missing is never a pass.

This script deliberately does not import ``scripts/check_handoff.py`` or any producer
module: M4 recomputes hashes and reads the package independently. It does not replace
the byte-level commands in ``docs/handoffs/42-m4-gate4-verification-checklist.md``;
it makes the missing-vs-contradiction distinction explicit.

Exit codes: 0 = all PASS, 1 = at least one FAIL, 2 = no FAIL but at least one
NOT VERIFIED, 3 = usage error or package not found.

Usage:

    python scripts/m4_verify_gate4.py --package docs/handoffs/evidence/P4-2026-10-30
    python scripts/m4_verify_gate4.py --package ... --record <gate4-record.md>
    python scripts/m4_verify_gate4.py --package ... --controller http://127.0.0.1:8801 \
        --job-id <real-job-id>

This is verifier tooling, not Gate 4 evidence. A synthetic self-test package must
never be placed under ``docs/handoffs/evidence/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

EXPECTED_ORIGINAL = "aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393"
EXPECTED_CONTRACT = "24bfe568e162be57de013abaeda96060d7edf4cad4b2eca0b003d929d9ec4e37"
DOCUMENTED_CI_ROUNDTRIP = "decdcdb821bfe4c41a070b0a23a2f6497f46a92f439cb02a1e4139a82cde954b"
SIX_CASES = {
    "valid-empty": {"kind": "valid", "input_len": 0, "rc": 0, "stdout": "0a", "stderr": ""},
    "valid-smallest": {"kind": "valid", "input_len": 1, "rc": 0, "stdout": "410a", "stderr": ""},
    "valid-typical": {"kind": "valid", "input_len": 10, "rc": 0, "stdout": "41" * 10 + "0a", "stderr": ""},
    "valid-maximum": {"kind": "valid", "input_len": 49, "rc": 0, "stdout": "41" * 49 + "0a", "stderr": ""},
    # The frozen original silently overflows on the first invalid case (exit 0) and
    # SIGSEGVs on the longer one. Gate 4 requires the round trip to reproduce these
    # rows exactly; the repaired-oracle shape (rc 1, 42-byte stderr) is a different gate.
    "invalid-first": {"kind": "invalid", "input_len": 50, "rc": 0, "stdout": "41" * 50 + "0a", "stderr": ""},
    "invalid-longer": {"kind": "invalid", "input_len": 100, "rc": -11, "stdout": "", "stderr": ""},
}
OBLIGATION_KINDS = {
    "identity", "structure", "interface", "behaviour", "symbolic", "reproducibility",
}
VALID_OUTCOMES = {
    "success_within_scope", "unsupported", "rejected", "inconclusive", "error", "cancelled",
}
SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BLANK_SIGNATURES = {"", "—", "–", "-", "n/a", "pending", "blank", "unsigned"}

Rows = list[tuple[str, str, str]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add(rows: Rows, status: str, check_id: str, detail: str) -> None:
    rows.append((status, check_id, detail))


def text_of(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def json_of(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(text_of(path))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def relative_files(package: Path) -> list[str]:
    return sorted(
        str(path.relative_to(package)).replace("\\", "/")
        for path in package.rglob("*")
        if path.is_file()
    )


def find_file(
    package: Path,
    handoff: dict[str, Any] | None,
    name_hints: tuple[str, ...],
    role_hints: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> Path | None:
    """Find a package file by artifact role first, then by filename hints."""
    candidates: list[str] = []
    for item in (handoff or {}).get("artifacts") or []:
        if isinstance(item, dict):
            role = str(item.get("role") or "").lower()
            path = str(item.get("path") or "")
            if path and any(hint in role for hint in role_hints):
                candidates.append(path)
    files = relative_files(package)
    for path in files:
        lower = path.lower()
        if any(hint in lower for hint in name_hints) and not any(h in lower for h in exclude):
            candidates.append(path)
    for raw in candidates:
        resolved = (package / raw).resolve()
        try:
            resolved.relative_to(package.resolve())
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def check_package_mechanics(package: Path, rows: Rows, expect_from: str, expect_to: str) -> dict[str, Any] | None:
    files = relative_files(package)
    add(rows, "PASS", "A2-file-list", f"{len(files)} files")

    handoff_path = package / "handoff.json"
    if not handoff_path.is_file():
        add(rows, "FAIL", "A6-handoff", "missing handoff.json")
        return None
    handoff = json_of(handoff_path)
    if handoff is None:
        add(rows, "FAIL", "A6-handoff", "handoff.json is not a JSON object")
        return None

    required_str = ("id", "from", "to", "task", "created_at", "deadline", "requested_action")
    required_list = ("commands", "expected", "observed")
    missing = [key for key in required_str if not str(handoff.get(key) or "").strip()]
    missing += [key for key in required_list if not handoff.get(key)]
    if missing:
        add(rows, "FAIL", "A6-handoff", f"missing/non-empty keys: {missing}")
    if handoff.get("interface_version") != 1:
        add(rows, "FAIL", "A6-handoff", f"interface_version={handoff.get('interface_version')!r} (expected 1)")
    if handoff.get("from") != expect_from or handoff.get("to") != expect_to:
        add(rows, "FAIL", "A6-handoff", f"from/to={handoff.get('from')!r}/{handoff.get('to')!r}")
    if not missing and handoff.get("interface_version") == 1:
        add(rows, "PASS", "A6-handoff", f"id={handoff.get('id')!r} from={handoff.get('from')} to={handoff.get('to')}")

    # --- SHA256SUMS: own strict parser, then recompute every hash -----------------
    sums_path = package / "SHA256SUMS"
    sums: dict[str, str] = {}
    format_ok = True
    if not sums_path.is_file():
        add(rows, "FAIL", "A3-sums", "missing SHA256SUMS")
        format_ok = False
    else:
        lines = text_of(sums_path).splitlines()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            match = SUM_LINE.match(line)
            if not match:
                add(rows, "FAIL", "A3-sums", f"line {line_number} is not '<64hex>  <path>': {line[:80]!r}")
                format_ok = False
                continue
            digest, raw_path = match.group(1), match.group(2)
            if raw_path in sums:
                add(rows, "FAIL", "A3-sums", f"duplicate path {raw_path}")
                format_ok = False
            sums[raw_path] = digest
        ordered = sorted(sums, key=lambda value: value.encode("utf-8"))
        if list(sums) != ordered:
            add(rows, "FAIL", "A3-sums", "not sorted bytewise by path (LC_ALL=C)")
            format_ok = False
        if format_ok and sums:
            add(rows, "PASS", "A3-sums", f"{len(sums)} valid rows, sorted")
        coverage = set(files) - {"SHA256SUMS"}
        missing_from_sums = sorted(coverage - set(sums))
        extra_sums = sorted(set(sums) - coverage)
        if missing_from_sums:
            add(rows, "FAIL", "A4-coverage", f"not covered by SHA256SUMS: {missing_from_sums[:8]}")
        if extra_sums:
            add(rows, "FAIL", "A4-coverage", f"SHA256SUMS lists absent files: {extra_sums[:8]}")
        if not missing_from_sums and not extra_sums:
            add(rows, "PASS", "A4-coverage", "every file except SHA256SUMS is covered")
        mismatched = 0
        for raw_path, expected in sums.items():
            resolved = (package / raw_path).resolve()
            if not resolved.is_file():
                add(rows, "FAIL", "A5-rehash", f"listed file missing: {raw_path}")
                mismatched += 1
                continue
            actual = sha256_file(resolved)
            if actual != expected:
                add(rows, "FAIL", "A5-rehash", f"mismatch {raw_path}: expected {expected} actual {actual}")
                mismatched += 1
        if format_ok and sums and mismatched == 0:
            add(rows, "PASS", "A5-rehash", f"all {len(sums)} hashes recomputed and equal")

    # --- handoff.json artifacts: safe paths + rehash -----------------------------
    listed: set[str] = set()
    artifacts = handoff.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        add(rows, "FAIL", "A7-artifacts", "artifacts list is missing/empty")
    else:
        artifact_bad = 0
        for index, item in enumerate(artifacts):
            if not isinstance(item, dict):
                add(rows, "FAIL", "A7-artifacts", f"artifacts[{index}] is not an object")
                artifact_bad += 1
                continue
            raw_path = str(item.get("path") or "")
            checksum = str(item.get("sha256") or "")
            role = str(item.get("role") or "")
            if not role.strip():
                add(rows, "FAIL", "A7-artifacts", f"artifacts[{index}] role missing")
                artifact_bad += 1
            if not HEX64.match(checksum):
                add(rows, "FAIL", "A7-artifacts", f"artifacts[{index}] sha256 invalid")
                artifact_bad += 1
                continue
            resolved = (package / raw_path).resolve()
            try:
                resolved.relative_to(package.resolve())
            except ValueError:
                add(rows, "FAIL", "A7-artifacts", f"path escapes package: {raw_path}")
                artifact_bad += 1
                continue
            if not resolved.is_file():
                add(rows, "FAIL", "A7-artifacts", f"artifact missing: {raw_path}")
                artifact_bad += 1
                continue
            actual = sha256_file(resolved)
            if actual != checksum:
                add(rows, "FAIL", "A7-artifacts", f"artifact hash mismatch: {raw_path}")
                artifact_bad += 1
                continue
            if raw_path in listed:
                add(rows, "FAIL", "A7-artifacts", f"artifact listed twice: {raw_path}")
                artifact_bad += 1
                continue
            listed.add(raw_path)
        metadata = {"handoff.json", "SHA256SUMS", "README.md"}
        unlisted = sorted(set(files) - metadata - listed)
        if unlisted:
            add(rows, "FAIL", "A7-artifacts", f"evidence files not listed: {unlisted[:8]}")
            artifact_bad += 1
        if artifact_bad == 0:
            add(rows, "PASS", "A7-artifacts", f"{len(listed)} artifacts safe and hash-bound")

    if sums_path.is_file():
        add(rows, "PASS", "A1-package-hash", f"SHA256SUMS sha256={sha256_file(sums_path)}")
    return handoff


def check_gate4_identity(package: Path, handoff: dict[str, Any] | None, rows: Rows) -> None:
    original = find_file(package, handoff, ("original",), ("original",), ("roundtrip", "candidate"))
    if original is None:
        add(rows, "NOT VERIFIED", "B1-original", "no original.elf artifact in the package")
    else:
        digest = sha256_file(original)
        if digest != EXPECTED_ORIGINAL:
            add(rows, "FAIL", "B1-original", f"{digest} != frozen {EXPECTED_ORIGINAL}")
        else:
            add(rows, "PASS", "B1-original", f"{original.name}: {digest}")

    status = find_file(package, handoff, ("gate4-status", "gate4_status"), ("gate4", "status"))
    status_json = json_of(status) if status else None
    if status is None or status_json is None:
        add(rows, "NOT VERIFIED", "B2-gate4-status", "gate4-status.json missing/unreadable")
        status_json = None
    else:
        gate4 = str(status_json.get("gate4") or "").lower()
        if gate4 != "pass":
            add(rows, "FAIL", "B2-gate4-status", f"gate4={status_json.get('gate4')!r} (expected pass)")
        uid = status_json.get("uid")
        expected_uid = status_json.get("expected_uid")
        if str(uid) != "1003" or str(expected_uid) != "1003":
            add(rows, "FAIL", "B2-gate4-status", f"uid={uid!r} expected_uid={expected_uid!r}")
        elf = str(status_json.get("elf_sha256") or "")
        if elf != EXPECTED_ORIGINAL:
            add(rows, "FAIL", "B2-gate4-status", f"elf_sha256={elf!r}")
        if gate4 == "pass" and str(uid) == "1003":
            add(rows, "PASS", "B2-gate4-status", "gate4=pass, uid=1003")

    candidate = find_file(
        package,
        handoff,
        ("roundtrip", "candidate.elf", "patched.elf"),
        ("roundtrip", "candidate", "patched"),
        ("original", "before", "after", ".txt", ".json", ".log", ".md"),
    )
    candidate_hash: str | None = None
    if candidate is None:
        add(rows, "NOT VERIFIED", "B2-candidate", "no roundtrip/candidate ELF in the package")
    else:
        candidate_hash = sha256_file(candidate)
        add(rows, "PASS", "B2-candidate", f"{candidate.name}: {candidate_hash}")
        if status_json is not None:
            patched = str(status_json.get("patched_sha256") or "")
            if patched and patched != candidate_hash:
                add(rows, "FAIL", "B3-status-binding", f"gate4-status patched_sha256={patched} != candidate bytes {candidate_hash}")
            elif not patched:
                add(rows, "NOT VERIFIED", "B3-status-binding", "gate4-status has no patched_sha256")
            else:
                add(rows, "PASS", "B3-status-binding", "gate4-status patched hash equals candidate bytes")

    before = find_file(package, handoff, ("before",), ("before",))
    after = find_file(package, handoff, ("after",), ("after",))
    if before is None or after is None:
        add(rows, "NOT VERIFIED", "B4-before-after", "candidate before/after hash files missing")
    else:
        before_hash = sha256_file(before)
        after_hash = sha256_file(after)
        if before_hash != after_hash:
            add(rows, "FAIL", "B4-before-after", f"before {before_hash} != after {after_hash}")
        elif candidate_hash and candidate_hash not in (text_of(before) + text_of(after)):
            add(rows, "NOT VERIFIED", "B4-before-after", "hashes present but do not cite the candidate bytes")
        else:
            add(rows, "PASS", "B4-before-after", "candidate hash unchanged before/after")

    kit_dir = Path(__file__).resolve().parents[1]
    kit = {
        "routeb/gate4_runner.sh": None,
        "routeb/probe_patcherex2.py": None,
        "routeb/run_manifest_compare.py": None,
        "routeb/verify.py": None,
    }
    actual, missing = {}, []
    for rel in kit:
        path = kit_dir / rel
        if path.is_file():
            actual[rel] = sha256_file(path)
        else:
            missing.append(rel)
    if missing:
        add(rows, "NOT VERIFIED", "B5-kit", f"repo kit files missing: {missing}")
    else:
        pinned_text = "\n".join(text_of(f) for f in (package / f for f in relative_files(package)) if f.suffix in {".txt", ".md", ".json", ".log"})
        pinned_hits = {rel: digest for rel, digest in actual.items() if digest in pinned_text}
        if len(pinned_hits) == len(actual):
            add(rows, "PASS", "B5-kit", f"{len(actual)} kit hashes recomputed from the repo and cited")
        else:
            add(rows, "NOT VERIFIED", "B5-kit", f"kit hash not cited in package for: {sorted(set(actual) - set(pinned_hits))} (recomputed: {actual})")

    notes = "\n".join(text_of(package / f) for f in relative_files(package) if f.endswith((".json", ".md", ".log", ".txt")))
    if re.search(r"\bGate\s*4\b[^\n]{0,40}\b(repaired|repair verified|fix verified)\b", notes, re.IGNORECASE):
        add(rows, "FAIL", "B6-no-repair-claim", "package text claims a repair in Gate 4 context")
    else:
        add(rows, "PASS", "B6-no-repair-claim", "no repair/V3 claim found in Gate 4 package text")


def check_six_cases(package: Path, handoff: dict[str, Any] | None, rows: Rows) -> None:
    """Gate 4 native six-case evidence is manifest-compare.json.

    Per-case raw stdout/stderr files are supplementary only; their absence is not a
    reason to mark a complete package NOT VERIFIED.
    """
    manifest_path = find_file(package, handoff, ("manifest-compare", "manifest_compare"), ("manifest", "compare"))
    if manifest_path is None:
        add(rows, "NOT VERIFIED", "C1-manifest", "manifest-compare.json missing")
        return
    data = json_of(manifest_path)
    if data is None:
        add(rows, "FAIL", "C1-manifest", "manifest-compare.json is not a JSON object")
        return
    if data.get("original_sha256") != EXPECTED_ORIGINAL:
        add(rows, "FAIL", "C1-manifest", f"original_sha256={data.get('original_sha256')!r}")
    cases = data.get("cases")
    if not isinstance(cases, list):
        add(rows, "FAIL", "C1-manifest", "cases is not a list")
        return
    ids = [str(case.get("id")) for case in cases if isinstance(case, dict)]
    if len(cases) != 6 or set(ids) != set(SIX_CASES) or len(set(ids)) != 6:
        add(rows, "FAIL", "C1-manifest", f"expected the six frozen ids, got {ids}")
        return
    add(rows, "PASS", "C1-manifest", f"six frozen ids: {ids}")
    if data.get("total") != 6 or data.get("same_count") != 6 or data.get("gate4_pass") is not True:
        add(rows, "FAIL", "C2-counts", f"total={data.get('total')} same_count={data.get('same_count')} gate4_pass={data.get('gate4_pass')}")
    else:
        add(rows, "PASS", "C2-counts", "total=6 same_count=6 gate4_pass=true")

    by_id = {str(case.get("id")): case for case in cases if isinstance(case, dict)}
    problems: list[str] = []
    for case_id, expected in SIX_CASES.items():
        case = by_id[case_id]
        original = case.get("original")
        patched = case.get("patched")
        if not isinstance(original, dict) or not isinstance(patched, dict):
            problems.append(f"{case_id}: missing original/patched row")
            continue
        if case.get("same") is not True:
            problems.append(f"{case_id}: same is not true")
        if original != patched:
            problems.append(f"{case_id}: original != patched")
        if original.get("returncode") != expected["rc"]:
            problems.append(f"{case_id}: rc {original.get('returncode')!r} != frozen {expected['rc']}")
        if str(original.get("stdout_hex", "")) != expected["stdout"]:
            problems.append(f"{case_id}: stdout != frozen shape")
        if str(original.get("stderr_hex", "")) != expected["stderr"]:
            problems.append(f"{case_id}: stderr != frozen shape")
        if case.get("kind") != expected["kind"]:
            problems.append(f"{case_id}: kind {case.get('kind')!r} != {expected['kind']}")
        if case.get("input_len") != expected["input_len"]:
            problems.append(f"{case_id}: input_len {case.get('input_len')!r} != {expected['input_len']}")
    if problems:
        add(rows, "FAIL", "C3-rows", "; ".join(problems[:8]))
    else:
        add(rows, "PASS", "C3-rows", "six rows identical original==roundtrip and match the frozen original shape")

    candidate = find_file(
        package,
        handoff,
        ("roundtrip", "candidate.elf", "patched.elf"),
        ("roundtrip", "candidate", "patched"),
        ("original", "before", "after", ".txt", ".json", ".log", ".md"),
    )
    if candidate is not None:
        candidate_hash = sha256_file(candidate)
        if data.get("patched_sha256") != candidate_hash:
            add(rows, "FAIL", "C4-binding", f"manifest patched_sha256={data.get('patched_sha256')!r} != candidate bytes {candidate_hash}")
        else:
            add(rows, "PASS", "C4-binding", "manifest patched hash equals candidate bytes")
        if data.get("patched_sha256") != DOCUMENTED_CI_ROUNDTRIP:
            add(rows, "NOT VERIFIED", "C5-pin", f"round trip {candidate_hash} differs from documented CI round trip {DOCUMENTED_CI_ROUNDTRIP}; explanation required")
        else:
            add(rows, "PASS", "C5-pin", "round trip matches the documented CI round-trip hash")


def check_isolation(package: Path, handoff: dict[str, Any] | None, rows: Rows) -> None:
    environment = find_file(package, handoff, ("environment", "env."), ("environment",))
    env_text = text_of(environment) if environment else ""
    if environment is None:
        add(rows, "NOT VERIFIED", "D8-environment", "environment.txt missing")
    else:
        lower = env_text.lower()
        tool_hits = [tool for tool in ("ghidra", "patcherex2", "clang", "ld.lld", "python") if tool in lower]
        version_hits = [tool for tool in ("java", "version") if tool in lower]
        if len(tool_hits) >= 3 and version_hits:
            add(rows, "PASS", "D8-environment", f"tools recorded: {tool_hits}")
        else:
            add(rows, "NOT VERIFIED", "D8-environment", f"missing tool versions (found {tool_hits})")

    isolation = find_file(package, handoff, ("isolation", "attestation"), ("isolation", "attestation"))
    iso_text = text_of(isolation) if isolation else ""
    combined = env_text + "\n" + iso_text
    lower = combined.lower()

    uid_match = re.search(r"uid\s*[:=]\s*(\d+)", lower)
    if uid_match is None:
        add(rows, "NOT VERIFIED", "D3-uid", "no uid recorded")
    elif uid_match.group(1) != "1003":
        add(rows, "FAIL", "D3-uid", f"uid={uid_match.group(1)} != 1003")
    else:
        add(rows, "PASS", "D3-uid", "uid=1003")
    cap_match = re.search(r"cap_?eff\s*[:=]\s*([0-9a-f]+)", lower)
    if cap_match is None:
        add(rows, "NOT VERIFIED", "D3-caps", "CapEff not recorded")
    elif set(cap_match.group(1)) != {"0"}:
        add(rows, "FAIL", "D3-caps", f"CapEff={cap_match.group(1)} != 0")
    else:
        add(rows, "PASS", "D3-caps", "CapEff=0")
    nnp_match = re.search(r"no_?new_?privs\s*[:=]\s*(\d+)", lower)
    if nnp_match is None:
        add(rows, "NOT VERIFIED", "D3-nnp", "NoNewPrivs not recorded")
    elif nnp_match.group(1) != "1":
        add(rows, "FAIL", "D3-nnp", f"NoNewPrivs={nnp_match.group(1)} != 1")
    else:
        add(rows, "PASS", "D3-nnp", "NoNewPrivs=1")
    if "seccomp" in lower:
        add(rows, "PASS", "D7-seccomp", "seccomp state recorded")
    else:
        add(rows, "NOT VERIFIED", "D7-seccomp", "Seccomp: 0 not recorded")

    if isolation is None:
        add(rows, "NOT VERIFIED", "D1-isolation", "isolation attestation missing")
        for check_id, label in (
            ("D1-snapshot", "snapshot identity"),
            ("D2-revert", "revert evidence"),
            ("D4-egress", "no-egress evidence"),
            ("D5-evaluator", "evaluator unreadable"),
            ("D6-wrapper", "wrapper conditions"),
        ):
            add(rows, "NOT VERIFIED", check_id, f"{label} cannot be checked without the isolation attestation")
        return
    for pattern, check_id, label in (
        (r"snapshot", "D1-snapshot", "snapshot identity"),
        (r"revert", "D2-revert", "revert evidence"),
        (r"egress|no[_ -]?route|unreachable|no internet|network", "D4-egress", "no-egress evidence"),
        (r"evaluator", "D5-evaluator", "evaluator unreadable"),
        (r"execv|wrapper|owner|mode", "D6-wrapper", "wrapper conditions"),
    ):
        if re.search(pattern, lower):
            add(rows, "PASS", check_id, label)
        else:
            add(rows, "NOT VERIFIED", check_id, f"{label} not found in isolation/environment text")


def check_record(record: Path, package: Path, handoff: dict[str, Any] | None, rows: Rows) -> None:
    if not record.is_file():
        add(rows, "FAIL", "E1-record", f"record not found: {record}")
        return
    text = text_of(record)
    sums_path = package / "SHA256SUMS"
    sums_sha = sha256_file(sums_path) if sums_path.is_file() else ""
    package_id = str((handoff or {}).get("id") or "")
    if sums_sha and sums_sha not in text.lower():
        add(rows, "NOT VERIFIED", "E1-record", "record does not cite the package SHA256SUMS hash")
    else:
        add(rows, "PASS", "E1-record", "record cites the package SHA256SUMS hash")
    if package_id and package_id not in text:
        add(rows, "NOT VERIFIED", "E1-record-id", f"record does not cite package id {package_id!r}")
    else:
        add(rows, "PASS", "E1-record-id", "record cites the package id")

    rows_by_first: dict[str, list[str]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells:
            rows_by_first[cells[0].lower()] = cells

    def signature_cell(cells: list[str]) -> str:
        # Signature tables are Role | Name | Date | Signature (4 columns); tolerate an
        # extra column by taking the last cell. Placeholder dashes count as blank.
        value = cells[-1] if len(cells) >= 4 else ""
        return "" if value.strip().lower() in BLANK_SIGNATURES else value

    m5_cells = next((cells for first, cells in rows_by_first.items() if first.startswith("m5")), None)
    m4_cells = next((cells for first, cells in rows_by_first.items() if first.startswith("m4")), None)
    lead_cells = next((cells for first, cells in rows_by_first.items() if first.startswith("lead")), None)
    if m5_cells is None:
        add(rows, "NOT VERIFIED", "E5-m5", "no M5 signature row")
    elif signature_cell(m5_cells):
        add(rows, "PASS", "E5-m5", "M5 row filled")
    else:
        add(rows, "NOT VERIFIED", "E5-m5", "M5 signature row blank")
    if m4_cells is None:
        add(rows, "FAIL", "E5-m4", "no M4 verifier row in the record")
    else:
        cell = signature_cell(m4_cells)
        if not cell:
            add(rows, "PASS", "E5-m4", "M4 verifier row blank at receipt")
        else:
            add(rows, "FAIL", "E5-m4", f"M4 row already filled: {cell!r} (self-signing/duplicate)")
    if lead_cells is None:
        add(rows, "NOT VERIFIED", "E5-lead", "no Lead approval row")
    elif signature_cell(lead_cells):
        add(rows, "FAIL", "E5-lead", "Lead row signed before M4 verification (order violation)")
    else:
        add(rows, "PASS", "E5-lead", "Lead row blank (not approved yet)")
    if re.search(r"gate\s*4[^\n]{0,30}(approved|closed)", text, re.IGNORECASE) and (
        lead_cells is None or not signature_cell(lead_cells)
    ):
        add(rows, "FAIL", "E6-preapproval", "record claims Gate 4 approved/closed while the Lead row is blank")
    else:
        add(rows, "PASS", "E6-preapproval", "no premature Gate 4 approval wording")
    add(rows, "PASS", "E8-record-hash", f"record sha256={sha256_file(record)}")


def check_controller(controller: str, job_id: str, rows: Rows) -> None:
    url = controller.rstrip("/") + f"/api/jobs/{job_id}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            snapshot = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        add(rows, "NOT VERIFIED", "F3-job", f"cannot read {url}: {exc}")
        return
    if not isinstance(snapshot, dict):
        add(rows, "FAIL", "F3-job", "job snapshot is not an object")
        return
    if snapshot.get("phase") != "finished":
        add(rows, "FAIL", "F3-job", f"phase={snapshot.get('phase')!r} (expected finished)")
    else:
        add(rows, "PASS", "F3-job", "phase=finished")
    if snapshot.get("demo") is not False:
        add(rows, "FAIL", "F3-job", f"demo={snapshot.get('demo')!r} (expected False)")
    else:
        add(rows, "PASS", "F3-job", "demo=False")
    if not snapshot.get("verdict"):
        add(rows, "FAIL", "F3-job", "no verdict on the finished job")
    else:
        add(rows, "PASS", "F3-job", f"verdict={snapshot.get('verdict')!r}")
    routes = snapshot.get("routes") if isinstance(snapshot.get("routes"), dict) else {}
    for key in ("A", "B"):
        route = routes.get(key) if isinstance(routes.get(key), dict) else {}
        outcome = route.get("outcome")
        obligations = route.get("obligations") or []
        kinds = {item.get("kind") for item in obligations if isinstance(item, dict)}
        if outcome not in VALID_OUTCOMES:
            add(rows, "FAIL", f"F4-{key}", f"invalid outcome {outcome!r}")
        elif kinds != OBLIGATION_KINDS:
            add(rows, "FAIL", f"F4-{key}", f"obligation kinds {sorted(kinds)} != V0-V5")
        else:
            add(rows, "PASS", f"F4-{key}", f"outcome={outcome!r}, six V0-V5 obligations")
    if re.search(r"synthetic", json.dumps(snapshot), re.IGNORECASE):
        add(rows, "FAIL", "F6-synthetic", "snapshot text contains 'synthetic'")
    else:
        add(rows, "PASS", "F6-synthetic", "no synthetic label in the job snapshot")


def main(argv: list[str] | None = None) -> int:
    try:  # Windows consoles may default to a non-UTF-8 code page.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="M4 host-side Gate 4 verification helper")
    parser.add_argument("--package", type=Path, required=True, help="Gate 4 evidence package directory")
    parser.add_argument("--record", type=Path, default=None, help="Gate 4 record markdown")
    parser.add_argument("--controller", default="", help="controller base URL for job checks")
    parser.add_argument("--job-id", default="", help="real p2 job id")
    parser.add_argument("--expect-from", default="M3")
    parser.add_argument("--expect-to", default="M5")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    package: Path = args.package
    rows: Rows = []
    if not package.is_dir():
        add(rows, "NOT VERIFIED", "A0-package", f"package not found: {package}")
    else:
        handoff = check_package_mechanics(package, rows, args.expect_from, args.expect_to)
        if handoff is not None:
            check_gate4_identity(package, handoff, rows)
            check_six_cases(package, handoff, rows)
            check_isolation(package, handoff, rows)
        if args.record is not None:
            check_record(args.record, package, handoff, rows)
        if args.controller and args.job_id:
            check_controller(args.controller, args.job_id, rows)

    width = max((len(check_id) for _, check_id, _ in rows), default=8) + 2
    for status, check_id, detail in rows:
        print(f"{status:<12} {check_id:<{width}} {detail}")
    counts = {status: sum(1 for row in rows if row[0] == status) for status in ("PASS", "FAIL", "NOT VERIFIED")}
    print(f"\nsummary: {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['NOT VERIFIED']} NOT VERIFIED")
    if args.json:
        print(json.dumps({"rows": [{"status": s, "check": c, "detail": d} for s, c, d in rows], "counts": counts}))
    if counts["FAIL"]:
        return 1
    if counts["NOT VERIFIED"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
