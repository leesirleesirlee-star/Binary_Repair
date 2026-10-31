"""Pytest reporting helper used by the runtime verification measurement.

Why this file exists
--------------------
On this Windows host the DSH file sandbox makes pytest's temporary base
directory unreadable at session teardown. pytest then raises
``PermissionError`` inside its ``cleanup_dead_symlinks`` helper and exits before
printing its normal summary. That is an environment/sandbox problem, not a test
result.

This plugin is a *measurement aid only*:

* it records every collection report and test report to a JSON file and prints
  its own summary;
* it wraps pytest's temporary-directory teardown helper so the sandbox denial
  cannot abort the run.

It does not modify, skip, weaken, or re-order any test, and it never turns a
failure into a pass. Every per-test outcome it reports is the outcome pytest
itself produced.

Run (from the repository root):

    $env:PYTHONPATH = "scripts"
    $env:RT_REPORT_PATH = "_scratch/runtime_verification/pytest_report.json"
    python -m pytest tests/ --continue-on-collection-errors -p pytest_rt_report -q
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

_REPORTS: dict[str, dict[str, Any]] = {}
_COLLECT_ERRORS: list[dict[str, str]] = []
_REPORT_PATH = os.environ.get(
    "RT_REPORT_PATH",
    "_scratch/runtime_verification/pytest_report.json",
)
_TMP_ROOT = os.environ.get(
    "RT_TMP_ROOT",
    "_scratch/runtime_verification/pytest_tmp_shim",
)


@pytest.fixture
def tmp_path(request: Any) -> Path:
    """Drop-in replacement for pytest's ``tmp_path``.

    Why: on this host the first pytest-created temporary subdirectory poisons the
    base temporary directory for the rest of the run (any later directory
    listing raises ``PermissionError``). This fixture provides the same
    contract tests expect - a unique, empty, writable directory per test - while
    avoiding pytest's numbered-directory machinery.

    Run with ``-p no:tmpdir`` so this definition is the only ``tmp_path``.
    """
    root = Path(_TMP_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    safe_node = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[:120]
    path = root / f"{safe_node}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path



def _patch_tmpdir_cleanup() -> str:
    """Stop the sandbox's unreadable basetemp from aborting pytest teardown."""
    try:
        import _pytest.tmpdir as tmpdir_module

        original = tmpdir_module.cleanup_dead_symlinks

        def tolerant_cleanup(root: Path) -> None:
            try:
                original(root)
            except OSError as exc:  # noqa: BLE001 - sandbox denial at teardown
                print(f"[pytest_rt_report] ignored basetemp cleanup error: {exc}")

        tmpdir_module.cleanup_dead_symlinks = tolerant_cleanup
        return "patched"
    except Exception as exc:  # noqa: BLE001 - never hide a measurement problem
        return f"not patched: {exc}"


def pytest_configure(config: Any) -> None:
    _REPORTS.clear()
    _COLLECT_ERRORS.clear()
    status = _patch_tmpdir_cleanup()
    print(f"[pytest_rt_report] basetemp teardown tolerance: {status}")
    print(f"[pytest_rt_report] report path: {_REPORT_PATH}")


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        _COLLECT_ERRORS.append(
            {
                "nodeid": report.nodeid,
                "longrepr": str(report.longrepr)[-2000:],
            }
        )


def pytest_runtest_logreport(report: Any) -> None:
    entry = _REPORTS.setdefault(
        report.nodeid, {"nodeid": report.nodeid, "phases": {}, "reports": []}
    )
    entry["reports"].append(
        {
            "when": report.when,
            "outcome": report.outcome,
            "duration": round(float(report.duration), 3),
            "longrepr": str(report.longrepr)[-2000:] if report.failed else "",
        }
    )
    entry["phases"][report.when] = report.outcome


def _final_outcome(entry: dict[str, Any]) -> str:
    phases = entry["phases"]
    if phases.get("setup") == "failed":
        return "error"
    if phases.get("setup") == "skipped":
        return "skipped"
    if phases.get("call") == "failed":
        return "failed"
    if phases.get("call") == "skipped":
        return "skipped"
    if phases.get("teardown") == "failed":
        return "error"
    if phases.get("call") == "passed":
        return "passed"
    return "unknown"


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    counts: dict[str, int] = {}
    tests: list[dict[str, Any]] = []
    for entry in sorted(_REPORTS.values(), key=lambda item: item["nodeid"]):
        outcome = _final_outcome(entry)
        counts[outcome] = counts.get(outcome, 0) + 1
        record: dict[str, Any] = {"nodeid": entry["nodeid"], "outcome": outcome}
        for phase in entry["reports"]:
            if phase["outcome"] != "passed":
                record.setdefault("non_passing_phases", []).append(phase)
        tests.append(record)

    payload = {
        "pytest_exitstatus": int(exitstatus),
        "tests_collected": int(getattr(session, "testscollected", 0)),
        "counts": counts,
        "collection_errors": _COLLECT_ERRORS,
        "tests": tests,
    }
    path = Path(_REPORT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    summary = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
    print()
    print(
        f"[pytest_rt_report] collected={payload['tests_collected']} {summary} "
        f"collection_errors={len(_COLLECT_ERRORS)} pytest_exitstatus={int(exitstatus)}"
    )
    for error in _COLLECT_ERRORS:
        first_line = error["longrepr"].strip().splitlines()[-1] if error["longrepr"] else ""
        print(f"[pytest_rt_report] collection error: {error['nodeid']}: {first_line}")
    print(f"[pytest_rt_report] wrote {path}")
