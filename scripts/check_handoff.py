#!/usr/bin/env python3
"""Validate an M3 -> M5 evidence package against INTERFACE v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import tempfile
from pathlib import Path

HEX = re.compile(r"^[0-9a-f]{64}$")
REQUIRED = ("id", "from", "to", "route", "created_at", "files")
ROUTES = {"gate4", "route_a", "route_b"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_rel(raw: str) -> Path | None:
    if not raw or raw.startswith("/") or raw.startswith("./") or ".." in Path(raw).parts:
        return None
    if "\\" in raw:
        return None
    return Path(raw)


def validate(package: Path) -> tuple[list[str], dict]:
    errors: list[str] = []
    handoff_path = package / "handoff.json"
    sums_path = package / "SHA256SUMS"
    if not handoff_path.is_file():
        return ["missing handoff.json"], {}
    if not sums_path.is_file():
        return ["missing SHA256SUMS"], {}
    try:
        meta = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot parse handoff.json: {exc}"], {}
    if not isinstance(meta, dict):
        return ["handoff.json must be an object"], {}
    for key in REQUIRED:
        if key not in meta:
            errors.append(f"handoff.json missing {key}")
    if meta.get("from") != "M3":
        errors.append("handoff.from must be M3")
    if meta.get("to") != "M5":
        errors.append("handoff.to must be M5")
    if meta.get("route") not in ROUTES:
        errors.append(f"handoff.route must be one of {sorted(ROUTES)}")
    files = meta.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(x, str) for x in files):
        errors.append("handoff.files must be a non-empty string list")
        files = []
    rels = []
    for raw in files:
        rel = _safe_rel(raw)
        if rel is None:
            errors.append(f"unsafe relative path: {raw!r}")
        else:
            rels.append(rel)
            if not (package / rel).is_file():
                errors.append(f"listed file missing: {raw}")

    lines = sums_path.read_text(encoding="utf-8").splitlines()
    parsed: list[tuple[str, Path]] = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split("  ")
        if len(parts) != 2:
            errors.append(f"SHA256SUMS:{lineno}: expected '<hash>  <path>'")
            continue
        digest, raw = parts
        if not HEX.fullmatch(digest):
            errors.append(f"SHA256SUMS:{lineno}: invalid lowercase sha256")
            continue
        rel = _safe_rel(raw)
        if rel is None:
            errors.append(f"SHA256SUMS:{lineno}: unsafe path {raw!r}")
            continue
        parsed.append((digest, rel))
        if not (package / rel).is_file():
            errors.append(f"SHA256SUMS:{lineno}: file missing: {raw}")
        elif _sha(package / rel) != digest:
            errors.append(f"SHA256SUMS:{lineno}: hash mismatch: {raw}")
    names = [rel.as_posix() for _, rel in parsed]
    if names != sorted(names):
        errors.append("SHA256SUMS is not LC_ALL=C sorted by path")
    if len(names) != len(set(names)):
        errors.append("SHA256SUMS contains duplicate paths")
    for rel in rels:
        if rel.as_posix() not in names:
            errors.append(f"handoff.json file not in SHA256SUMS: {rel.as_posix()}")
    return errors, {
        "id": meta.get("id"),
        "from": meta.get("from"),
        "to": meta.get("to"),
        "route": meta.get("route"),
        "files": len(files),
        "sums_entries": len(parsed),
        "sha256sums_sha256": _sha(sums_path),
    }


def _self_test(root: Path | None = None) -> int:
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "raw").mkdir(exist_ok=True)
        (root / "raw" / "case-01.txt").write_text("ok\n", encoding="utf-8")
        (root / "handoff.json").write_text(json.dumps({
            "id": "SELFTEST",
            "from": "M3",
            "to": "M5",
            "route": "gate4",
            "created_at": "2026-10-29T00:00:00+08:00",
            "files": ["raw/case-01.txt"],
            "notes": "self-test",
        }), encoding="utf-8")
        digest = _sha(root / "raw" / "case-01.txt")
        (root / "SHA256SUMS").write_text(f"{digest}  raw/case-01.txt\n", encoding="utf-8")
        errors, info = validate(root)
        print(f"env: {platform.platform()} python={sys.version.split()[0]} root={root}")
        if errors:
            print("FAIL: self-test")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"PASS: self-test {json.dumps(info, sort_keys=True)}")
        return 0
    with tempfile.TemporaryDirectory(prefix="handoff-self-test-") as tmp:
        return _self_test(Path(tmp))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_handoff.py")
    parser.add_argument("package", nargs="?", type=Path)
    parser.add_argument("--expect-from", default=None)
    parser.add_argument("--expect-to", default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test(args.self_test_dir)
    if args.package is None:
        parser.error("package is required (or use --self-test)")
    errors, info = validate(args.package)
    if args.expect_from and info.get("from") != args.expect_from:
        errors.append(f"expected from={args.expect_from}, got {info.get('from')}")
    if args.expect_to and info.get("to") != args.expect_to:
        errors.append(f"expected to={args.expect_to}, got {info.get('to')}")
    print(json.dumps({"status": "PASS" if not errors else "FAIL", **info,
                      "errors": errors}, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
