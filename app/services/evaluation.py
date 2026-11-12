"""Read-only evidence observability scoring.

This module deliberately never derives a verdict. It reports coverage,
counts, missing evidence and bottlenecks over whatever the controller
returned. An unavailable field stays ``None``/``"unknown"``; it is never
silently scored as a pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LEVELS = ("V0", "V1", "V2", "V3", "V4", "V5")

LEVEL_LABELS = {
    "V0": "静态/字节观察",
    "V1": "符号/约束",
    "V2": "路径/输入可达",
    "V3": "动态执行",
    "V4": "模型级验证",
    "V5": "差分/行为对比",
}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _empty_rates() -> dict[str, Any]:
    return {"total": 0, "pass": 0, "fail": 0, "not_run": 0, "unknown": 0, "pass_rate": None}


def _rate(counts: dict[str, Any]) -> float | None:
    total = counts.get("total") or 0
    if not total:
        return None
    return round((counts.get("pass", 0) / total) * 100, 1)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _levels_from_evidence(evidence: dict[str, Any], route: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, bool]:
    """Observe which levels actually carry fields, never infer a pass."""
    evidence = _as_dict(evidence)
    route = _as_dict(route)
    out = {level: False for level in LEVELS}
    out["V0"] = bool(snapshot.get("original_sha256")) or bool(route.get("changed_regions"))
    for key in ("v1", "v2", "v3", "v4"):
        if evidence.get(key) not in (None, {}, []):
            out[key.upper()] = True
    # Some controller versions expose gates instead of v1..v4 objects.
    gates = _as_list(evidence.get("gates"))
    for gate in gates:
        name = str(_as_dict(gate).get("gate") or "").upper()
        for level in LEVELS:
            if name.startswith(level):
                out[level] = True
    if route.get("verification") not in (None, "", "not_run"):
        out["V4"] = True
    if evidence.get("comparison") or snapshot.get("comparison"):
        out["V5"] = True
    return out


def evaluate(
    snapshot: dict[str, Any],
    *,
    attempts: dict[str, Any] | None = None,
    artifacts: Any = None,
    evidence_by_attempt: dict[str, Any] | None = None,
    comparison_available: bool = False,
) -> dict[str, Any]:
    snapshot = _as_dict(snapshot)
    routes = _as_dict(snapshot.get("routes"))
    evidence_by_attempt = _as_dict(evidence_by_attempt)
    attempts = _as_dict(attempts)

    obligation_counts = _empty_rates()
    test_passed = 0
    test_total = 0
    route_elapsed = 0.0
    ai_calls = 0
    ai_tokens = 0
    ai_cost = 0.0
    changed_regions = 0
    route_rows: list[dict[str, Any]] = []
    level_evidence: dict[str, bool] = {level: False for level in LEVELS}
    level_rows: dict[str, list[str]] = {level: [] for level in LEVELS}

    for key, raw_route in routes.items():
        route = _as_dict(raw_route)
        obligations = _as_list(route.get("obligations")) or _as_list(snapshot.get("obligations"))
        route_counts = _empty_rates()
        for item in obligations:
            item = _as_dict(item)
            result = str(item.get("result") or "unknown").lower()
            route_counts["total"] += 1
            if result in ("pass", "passed", "ok", "success", "met"):
                route_counts["pass"] += 1
                obligation_counts["pass"] += 1
            elif result in ("fail", "failed", "error", "violated", "unmet"):
                route_counts["fail"] += 1
                obligation_counts["fail"] += 1
            elif result in ("not_run", "pending", "skipped"):
                route_counts["not_run"] += 1
                obligation_counts["not_run"] += 1
            else:
                route_counts["unknown"] += 1
                obligation_counts["unknown"] += 1
            obligation_counts["total"] += 1
        route_counts["pass_rate"] = _rate(route_counts)

        tests = _as_dict(route.get("tests"))
        passed = tests.get("passed")
        total = tests.get("total")
        if isinstance(passed, int) and isinstance(total, int):
            test_passed += passed
            test_total += total
        try:
            route_elapsed += float(route.get("elapsed_s") or 0)
        except (TypeError, ValueError):
            pass
        usage = _as_dict(route.get("ai_usage"))
        for key_name in ("calls", "requests", "count"):
            if isinstance(usage.get(key_name), int):
                ai_calls += usage[key_name]
                break
        for key_name in ("tokens", "total_tokens", "output_tokens"):
            if isinstance(usage.get(key_name), int):
                ai_tokens += usage[key_name]
                break
        for key_name in ("cost_usd", "cost", "estimated_cost_usd"):
            try:
                ai_cost += float(usage.get(key_name) or 0)
            except (TypeError, ValueError):
                pass
        changed_regions += len(_as_list(route.get("changed_regions")))

        attempt_id = route.get("attempt_id")
        evidence = evidence_by_attempt.get(attempt_id, {}) if attempt_id else {}
        levels = _levels_from_evidence(evidence, route, snapshot)
        for level, present in levels.items():
            if present:
                level_evidence[level] = True
                if key not in level_rows[level]:
                    level_rows[level].append(str(key))
        route_rows.append({
            "route": key,
            "name": route.get("name") or key,
            "phase": route.get("phase") or "",
            "verification": route.get("verification") or "unknown",
            "compatibility": route.get("compatibility") or "unknown",
            "tests": {"passed": passed, "total": total},
            "obligations": route_counts,
            "changed_regions": len(_as_list(route.get("changed_regions"))),
            "elapsed_s": route.get("elapsed_s"),
            "attempt_id": attempt_id,
            "evidence_levels": levels,
        })

    obligation_counts["pass_rate"] = _rate(obligation_counts)

    artifact_list: list[Any] = []
    if isinstance(artifacts, dict):
        for value in artifacts.values():
            artifact_list.extend(_as_list(value))
    else:
        artifact_list = _as_list(artifacts)

    if comparison_available:
        level_evidence["V5"] = True
        if "A" not in level_rows["V5"]:
            level_rows["V5"].append("A")
    if not any(level_evidence.values()) and snapshot.get("original_sha256"):
        level_evidence["V0"] = True
        level_rows["V0"] = ["job"]

    evidence_coverage = round(sum(1 for v in level_evidence.values() if v) / len(LEVELS) * 100, 1)
    test_rate = round(test_passed / test_total * 100, 1) if test_total else None
    artifact_score = 100.0 if artifact_list else 0.0
    comparison_score = 100.0 if comparison_available else 0.0

    # Weighted observability only; this is not a repair verdict.
    weighted_parts = [
        (evidence_coverage, 0.35),
        (obligation_counts["pass_rate"] if obligation_counts["total"] else 0.0, 0.25),
        (test_rate if test_rate is not None else 0.0, 0.20),
        (artifact_score, 0.10),
        (comparison_score, 0.10),
    ]
    observability = round(sum(value * weight for value, weight in weighted_parts), 1)

    created = _parse_time(snapshot.get("created_at"))
    finished = _parse_time(snapshot.get("finished_at"))
    elapsed = route_elapsed
    if created and finished:
        elapsed = max(elapsed, (finished - created).total_seconds())

    bottlenecks: list[dict[str, str]] = []
    if obligation_counts["fail"]:
        bottlenecks.append({"kind": "failed_obligations", "label": f"{obligation_counts['fail']} 条义务失败", "severity": "high"})
    if obligation_counts["not_run"]:
        bottlenecks.append({"kind": "not_run", "label": f"{obligation_counts['not_run']} 条义务未执行", "severity": "medium"})
    if not obligation_counts["total"]:
        bottlenecks.append({"kind": "no_obligations", "label": "未回传义务结果", "severity": "high"})
    if test_total == 0:
        bottlenecks.append({"kind": "no_tests", "label": "未回传有效用例/测试分母", "severity": "high"})
    if not artifact_list:
        bottlenecks.append({"kind": "no_artifacts", "label": "未注册补丁或证据产物", "severity": "medium"})
    if not comparison_available:
        bottlenecks.append({"kind": "no_comparison", "label": "未提供 V5 行为对比", "severity": "medium"})
    if not snapshot.get("verdict"):
        bottlenecks.append({"kind": "no_verdict", "label": "验收引擎尚未给出 verdict", "severity": "high"})

    attempt_items = _as_list(attempts.get("attempts"))
    return {
        "job_id": snapshot.get("id"),
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": snapshot.get("phase"),
        "verdict": snapshot.get("verdict"),
        "not_verdict": True,
        "counts": {
            "routes": len(routes),
            "obligations": obligation_counts,
            "tests": {"passed": test_passed, "total": test_total, "pass_rate": test_rate},
            "artifacts": len(artifact_list),
            "attempts": len(attempt_items),
            "changed_regions": changed_regions,
            "ai": {"calls": ai_calls, "tokens": ai_tokens, "cost_usd": round(ai_cost, 4)},
        },
        "scorecard": {
            "evidence_coverage": evidence_coverage,
            "obligation_pass_rate": obligation_counts["pass_rate"],
            "test_pass_rate": test_rate,
            "artifact_completeness": artifact_score,
            "comparison_coverage": comparison_score,
            "observability_index": observability,
            "elapsed_seconds": round(elapsed, 2),
            "formula": "0.35*evidence + 0.25*obligations + 0.20*tests + 0.10*artifacts + 0.10*comparison; coverage only",
        },
        "levels": [
            {
                "level": level,
                "label": LEVEL_LABELS[level],
                "available": level_evidence[level],
                "routes": sorted(level_rows[level]),
            }
            for level in LEVELS
        ],
        "routes": route_rows,
        "bottlenecks": bottlenecks,
        "limitations": [
            "This is an observability/coverage score, not a repair verdict.",
            "Missing or zero denominators stay N/A and are never treated as pass.",
            "Artifact and comparison availability only prove registration, not correctness.",
        ],
    }
