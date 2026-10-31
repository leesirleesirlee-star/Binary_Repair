from __future__ import annotations

import json
from pathlib import Path

from scripts.normalize_handoff import normalize


def _provisional(root: Path) -> Path:
    pkg = root / "pkg"
    (pkg / "raw").mkdir(parents=True)
    (pkg / "raw" / "a.txt").write_text("a\n", encoding="utf-8")
    (pkg / "handoff.json").write_text(json.dumps({
        "from": "M3", "to": "M5", "route": "gate4",
        "created_at": "2026-10-29T00:00:00+08:00",
        "package_id": "P4", "files": ["raw/a.txt"],
    }), encoding="utf-8")
    (pkg / "SHA256SUMS").write_text("0" * 64 + "  raw/a.txt\n", encoding="utf-8")
    return pkg


def test_normalize_provisional_to_v1_pass(tmp_path: Path) -> None:
    pkg = _provisional(tmp_path)
    out = tmp_path / "out"
    result = normalize(pkg, out)
    assert result["status"] == "PASS"
    meta = json.loads((out / "handoff.json").read_text())
    assert meta["id"] == "P4"
    assert "raw/a.txt" in meta["files"]
    assert "handoff.json" in meta["files"]
    assert "package_id" not in meta
    assert (out / "SHA256SUMS").read_text().splitlines()


def test_normalize_fails_missing_id(tmp_path: Path) -> None:
    pkg = _provisional(tmp_path)
    meta = json.loads((pkg / "handoff.json").read_text())
    del meta["package_id"]
    (pkg / "handoff.json").write_text(json.dumps(meta), encoding="utf-8")
    try:
        normalize(pkg, tmp_path / "out2")
    except ValueError as exc:
        assert "id" in str(exc)
    else:
        raise AssertionError("missing id must fail")
