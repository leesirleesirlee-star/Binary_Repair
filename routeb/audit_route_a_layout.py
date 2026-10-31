#!/usr/bin/env python3
"""Plan/audit the Route A trampoline layout (static, no execution)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile

PATCH_AT = 0x4012C0
TRAMP = 0x406000
MSG = 0x405000
STDERR = 0x404080
FPRINTF = 0x401120
VALID = 0x4012F6
MESSAGE = b"ERROR: input exceeds destination capacity\n"
CODE = bytes.fromhex(
    "4c8b45b831c941803c0800740348ffc1ebf44883f9310f8621000000488b3d5de0ffff"
    "488d35d6efffff31c0e8efb0ffffb8e7000000bf010000000f05e9b4b2ffff"
)
DETOUR = bytes.fromhex("e93b4d0000909090")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loads(path: Path):
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        out = []
        for seg in elf.iter_segments():
            if seg["p_type"] != "PT_LOAD":
                continue
            out.append({
                "vaddr": seg["p_vaddr"],
                "offset": seg["p_offset"],
                "filesz": seg["p_filesz"],
                "memsz": seg["p_memsz"],
                "flags": seg["p_flags"],
            })
        return out


def bytes_at(path: Path, vaddr: int, size: int) -> bytes:
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        for seg in elf.iter_segments():
            if seg["p_type"] != "PT_LOAD":
                continue
            start, end = seg["p_vaddr"], seg["p_vaddr"] + seg["p_filesz"]
            if start <= vaddr and vaddr + size <= end:
                fh.seek(seg["p_offset"] + vaddr - start)
                return fh.read(size)
    return b""


def reloc_counts(path: Path):
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        out = {}
        for name in (".rela.plt", ".rela.dyn", ".rel.plt", ".rel.dyn"):
            sec = elf.get_section_by_name(name)
            out[name] = sec.num_relocations() if sec else 0
        return out


def plan() -> dict:
    return {
        "status": "conditional_on_scope_ruling",
        "patch_at": hex(PATCH_AT),
        "detour": DETOUR.hex(),
        "message_addr": hex(MSG),
        "message_len": len(MESSAGE),
        "message_hex": MESSAGE.hex(),
        "trampoline_addr": hex(TRAMP),
        "trampoline_len": len(CODE),
        "trampoline_hex": CODE.hex(),
        "segments": [
            {"vaddr": hex(MSG), "flags": "R", "purpose": "error message"},
            {"vaddr": hex(TRAMP), "flags": "R|X", "purpose": "guard/exit trampoline"},
        ],
    }


def audit(original: Path, patched: Path) -> dict:
    before = loads(original)
    after = loads(patched)
    checks = {}
    checks["patch_bytes"] = bytes_at(patched, PATCH_AT, len(DETOUR)) == DETOUR
    checks["message_bytes"] = bytes_at(patched, MSG, len(MESSAGE)) == MESSAGE
    checks["trampoline_prefix"] = bytes_at(patched, TRAMP, len(CODE)) == CODE
    checks["original_loads_preserved"] = all(seg in after for seg in before)
    checks["new_loads"] = [seg for seg in after if seg not in before]
    checks["relocations_unchanged"] = reloc_counts(original) == reloc_counts(patched)
    checks["harness_unchanged"] = bytes_at(original, 0x401256, 90) == bytes_at(patched, 0x401256, 90)
    checks["strcpy_call_unchanged"] = bytes_at(original, 0x401304, 5) == bytes_at(patched, 0x401304, 5)
    return {
        "original": {"path": str(original), "sha256": sha(original), "size": original.stat().st_size},
        "patched": {"path": str(patched), "sha256": sha(patched), "size": patched.stat().st_size},
        "checks": checks,
        "pass": all(v is True for k, v in checks.items() if k != "new_loads")
        and len(checks["new_loads"]) == 2,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--original", type=Path)
    ap.add_argument("--patched", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.plan:
        data = plan()
    else:
        if not args.original or not args.patched:
            ap.error("--original and --patched are required for audit")
        data = audit(args.original, args.patched)
    text = json.dumps(data, indent=2)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0 if data.get("pass", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
