#!/usr/bin/env python3
"""Local, reproducible quality/efficiency probe.

Scope: static code-repair rules + read-only evidence evaluation over the mock
fixture. It does not execute binaries and never promotes a result to a verdict.
"""
from __future__ import annotations

import glob
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import demo
from app.services.code_repair import repair_code
from app.services.evaluation import evaluate

SAMPLES = [
    ("py-shell", "python", "import subprocess\nsubprocess.check_output(cmd, shell=True)", "CWE-78"),
    ("py-eval", "python", "eval(user_input)", "CWE-95"),
    ("c-strcpy", "c", "strcpy(dst, src);", "CWE-121"),
    ("py-sqli", "python", 'cursor.execute("SELECT * FROM users WHERE id = %s" % uid)', "CWE-89"),
    ("py-path", "python", "open(os.path.join(base, user_path))", "CWE-22"),
]


def main() -> int:
    rows = []
    for name, language, code, expected in SAMPLES:
        times = []
        result = None
        for _ in range(40):
            start = time.perf_counter()
            result = repair_code(code, language)
            times.append((time.perf_counter() - start) * 1000)
        detected = result.get("cwe") or ""
        rows.append({
            "sample": name,
            "expected_cwe": expected,
            "detected_cwe": detected,
            "detected": detected == expected,
            "patch_generated": bool(result.get("patched_code") and result.get("changed")),
            "mode": result.get("mode"),
            "p50_ms": round(statistics.median(times), 3),
            "p95_ms": round(sorted(times)[int(len(times) * 0.95) - 1], 3),
        })

    package_files = sorted(glob.glob(str(ROOT / "artifacts" / "**" / "package.json"), recursive=True))
    package_rows = []
    for package_path in package_files:
        pkg_dir = Path(package_path).parent
        try:
            package = json.loads(Path(package_path).read_text(encoding="utf-8"))
        except Exception:
            continue
        sums_ok = False
        sums_path = pkg_dir / "SHA256SUMS"
        if sums_path.exists():
            sums_ok = True
            for line in sums_path.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                expected, rel = parts[0], parts[1].lstrip("*")
                target = pkg_dir / rel
                if not target.is_file():
                    sums_ok = False
                    break
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected:
                    sums_ok = False
                    break
        audit = {}
        audit_path = pkg_dir / "audit.json"
        if audit_path.exists():
            try:
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
            except Exception:
                audit = {}
        evidence_layers = sorted(
            item.name for item in (pkg_dir / "evidence").glob("*") if item.is_dir()
        ) if (pkg_dir / "evidence").is_dir() else []
        tests = []
        tests_dir = pkg_dir / "tests"
        if tests_dir.is_dir():
            for test_file in tests_dir.glob("*.json"):
                try:
                    payload = json.loads(test_file.read_text(encoding="utf-8"))
                    tests.append({"file": test_file.name, "cases": len(payload.get("tests", []))})
                except Exception:
                    pass
        package_rows.append({
            "package": pkg_dir.name,
            "hashes_verified": sums_ok,
            "audit_pass": audit.get("pass"),
            "audit_checks": audit.get("checks", {}),
            "evidence_layers": evidence_layers,
            "tests": tests,
            "original_sha256": (package.get("expected") or {}).get("original_sha256"),
            "candidate_sha256": (package.get("expected") or {}).get("candidate_sha256"),
        })

    status_files = sorted(glob.glob(str(ROOT / "artifacts" / "**" / "status.json"), recursive=True))
    phases: dict[str, int] = {}
    for path in status_files:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        phase = str(payload.get("phase") or "unknown")
        phases[phase] = phases.get(phase, 0) + 1

    job = demo.create_job(
        original_sha256="b" * 64,
        filename="benchmark.elf",
        defect="benchmark fixture",
        valid_tests="hello",
        environment="local",
        valid_domain="0-64",
        forbidden="oob",
        invalid_outcome="1",
        assumptions="none",
    )
    snapshot = demo.snapshot(job)
    eval_times = []
    evaluation = None
    for _ in range(80):
        start = time.perf_counter()
        evaluation = evaluate(snapshot)
        eval_times.append((time.perf_counter() - start) * 1000)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": "static rules + read-only mock evaluation; no binary execution",
        "code_repair": {
            "samples": len(rows),
            "detected": sum(1 for row in rows if row["detected"]),
            "patch_generated": sum(1 for row in rows if row["patch_generated"]),
            "rows": rows,
        },
        "evidence_evaluation": {
            "iterations": len(eval_times),
            "p50_ms": round(statistics.median(eval_times), 3),
            "p95_ms": round(sorted(eval_times)[int(len(eval_times) * 0.95) - 1], 3),
            "observability_index": evaluation["scorecard"]["observability_index"],
            "not_verdict": evaluation["not_verdict"],
            "bottlenecks": [item["kind"] for item in evaluation["bottlenecks"]],
        },
        "binary_artifacts": {
            "packages": package_rows,
            "status_files": len(status_files),
            "phases": phases,
            "note": "candidate packages are verified by hash; phases are reported verbatim; no new binary execution",
        },
        "limitations": [
            "Rule engine covers known patterns only; 3/5 synthetic cases is not a real-world recall claim.",
            "No binary was executed in this probe; binary evidence is artifact metadata only.",
            "Observability index is coverage, never an acceptance verdict.",
        ],
    }
    target = ROOT / "docs" / "acceptance" / "repair-evaluation-2026-11-12.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
