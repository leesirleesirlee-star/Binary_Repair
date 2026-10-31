#!/usr/bin/env python3
"""Host-only checker: Python comments/docstrings must be ASCII.

UI strings (Chinese labels) are allowed and are not inspected here. This scans
Python comments and module/class/function docstrings in the M2 slice.
"""

from __future__ import annotations

import argparse
import ast
import io
import pathlib
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCOPE_FILES = (
    "app/__init__.py",
    "app/backend.py",
    "app/demo.py",
    "app/i18n.py",
    "app/main.py",
    "app/status.py",
    "app/visuals.py",
)
SCOPE_DIRS = ("tools", "tests")
SKIP_PARTS = {".venv", "node_modules", "agent", "__pycache__"}


def scoped_files(root: pathlib.Path) -> list[pathlib.Path]:
    files = [root / item for item in SCOPE_FILES]
    for scope in SCOPE_DIRS:
        files.extend(sorted((root / scope).rglob("*.py")))
    return [path for path in files if path.is_file() and not any(part in SKIP_PARTS for part in path.parts)]


def non_ascii(value: str) -> bool:
    return any(ord(char) > 127 for char in value)


def scan(root: pathlib.Path) -> list[str]:
    violations: list[str] = []
    for path in scoped_files(root):
        relative = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"{relative}: cannot read: {exc}")
            continue
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT and non_ascii(token.string):
                violations.append(f"{relative}:{token.start[0]}: comment is not ASCII")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            violations.append(f"{relative}: cannot parse: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", [])
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
                and non_ascii(first.value.value)
            ):
                violations.append(f"{relative}:{first.lineno}: docstring is not ASCII")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Python comments/docstrings are ASCII")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    args = parser.parse_args(argv)
    violations = scan(args.root.resolve())
    if violations:
        for item in violations:
            print(f"FAIL {item}")
        print(f"{len(violations)} violation(s)")
        return 1
    print("PASS 0 non-ASCII comments/docstrings in app/tools/tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
