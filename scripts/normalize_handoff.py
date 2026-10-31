#!/usr/bin/env python3
"""Mechanically normalize a provisional handoff.json to INTERFACE v1.

This does **not** relax the checker: it only fills `id`/`files`, sets
`schema_version=1`, and regenerates a sorted SHA256SUMS over the actual payload.
If a required field or a declared file is missing, it fails. The checker is then
run on the output; converter success requires checker PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from check_handoff import validate  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(package: Path, out: Path) -> dict:
    handoff_path = package / "handoff.json"
    if not handoff_path.is_file():
        raise ValueError("missing handoff.json")
    try:
        meta = json.loads(handoff_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"invalid handoff.json: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("handoff.json must be an object")
    for key in ("from", "to", "route", "created_at"):
        if not meta.get(key):
            raise ValueError(f"missing required field: {key}")
    ident = meta.get("id") or meta.get("package_id")
    if not ident:
        raise ValueError("missing id (or package_id)")
    if out.exists():
        raise ValueError(f"output already exists: {out}")

    payload = [
        p.relative_to(package).as_posix()
        for p in package.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS"
    ]
    payload.sort(key=lambda s: s.encode())
    declared = meta.get("files")
    if declared is not None:
        if not isinstance(declared, list):
            raise ValueError("files must be a list when present")
        for raw in declared:
            if not isinstance(raw, str) or raw not in payload:
                raise ValueError(f"declared file missing from payload: {raw!r}")
    meta["schema_version"] = 1
    meta["id"] = str(ident)
    meta["files"] = payload
    meta.pop("package_id", None)

    shutil.copytree(package, out)
    (out / "handoff.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    lines = []
    for rel in payload:
        path = out / rel
        lines.append(f"{_sha(path)}  {rel}")
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    errors, info = validate(out)
    return {"out": str(out), "status": "PASS" if not errors else "FAIL",
            "errors": errors, **info}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=Path(sys.argv[0]).name)
    parser.add_argument("package", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = normalize(args.package, args.out)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
