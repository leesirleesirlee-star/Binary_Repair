"""Display helper for the controller-provided run verdict.

The GUI must **not** compute an outcome. It only renders the verdict that the
acceptance engine already persisted (``snapshot["verdict"]``), or a direct
controller state (cancelled / not finished).

All success/failure decisions live on the server side (M1 worker + M4 engine).
"""

from __future__ import annotations

from typing import Any

from app.i18n import OUTCOME_LABELS_ZH


def display_verdict(snapshot: dict[str, Any]) -> dict[str, str]:
    """Map the snapshot verdict to a label; never infer an outcome."""

    verdict = snapshot.get("verdict") or {}
    outcome = verdict.get("outcome")
    if outcome:
        return {"key": str(outcome), "label_zh": OUTCOME_LABELS_ZH.get(str(outcome), str(outcome))}
    if snapshot.get("cancelled"):
        return {"key": "cancelled", "label_zh": "已取消"}
    if snapshot.get("phase") == "finished":
        return {
            "key": "none",
            "label_zh": "尚无 verdict（等待 acceptance engine）",
        }
    return {"key": "none", "label_zh": "—"}


OBLIGATION_ORDER = {
    "identity": 0,
    "structure": 1,
    "interface": 2,
    "behaviour": 3,
    "symbolic": 4,
    "reproducibility": 5,
}


def order_obligations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order controller obligation rows (kind/required/result/detail) V0-V5."""

    return sorted(items or [], key=lambda item: OBLIGATION_ORDER.get(str(item.get("kind")), 99))
