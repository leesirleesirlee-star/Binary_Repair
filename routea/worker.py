"""Route A worker: decode a target function, plan a trampoline, build and audit.

The worker intentionally separates *planning* from *applying*:

* ``decode`` and ``plan`` never write a binary;
* ``build`` requires ``--scope-authorized`` because adding an R-- string segment,
  an R-X code segment and changing the file layout is still a Gate 3 scope
  decision. Until that ruling exists, the candidate binary must not be patched
  with the exact stderr message.

No relocation engine is written here: the trampoline reuses the existing
``fprintf`` PLT entry and a RIP-relative load of ``stderr``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from app.domain.contracts import SecurityContract

MD = Cs(CS_ARCH_X86, CS_MODE_64)


class RouteAError(RuntimeError):
    pass


class ScopeNotAuthorized(RouteAError):
    pass


@dataclass(frozen=True)
class Instruction:
    address: int
    size: int
    bytes_hex: str
    mnemonic: str
    op_str: str

    def to_dict(self) -> dict:
        return {
            "address": hex(self.address),
            "size": self.size,
            "bytes": self.bytes_hex,
            "mnemonic": self.mnemonic,
            "op_str": self.op_str,
        }


@dataclass(frozen=True)
class FunctionInfo:
    name: str
    entry: int
    size: int
    file_offset: int
    instructions: tuple[Instruction, ...]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entry": hex(self.entry),
            "size": self.size,
            "file_offset": hex(self.file_offset),
            "instructions": [i.to_dict() for i in self.instructions],
        }


@dataclass(frozen=True)
class PatchPlan:
    target: FunctionInfo
    patch_at: int
    resume_at: int
    message: bytes
    message_vaddr: int
    code_vaddr: int
    detour: bytes
    code: bytes
    stderr_vaddr: int
    fprintf_plt: int
    max_valid_input: int

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "target": self.target.to_dict(),
            "patch_at": hex(self.patch_at),
            "resume_at": hex(self.resume_at),
            "detour": self.detour.hex(),
            "message": self.message.decode("ascii"),
            "message_hex": self.message.hex(),
            "message_vaddr": hex(self.message_vaddr),
            "code_vaddr": hex(self.code_vaddr),
            "code_hex": self.code.hex(),
            "stderr_vaddr": hex(self.stderr_vaddr),
            "fprintf_plt": hex(self.fprintf_plt),
            "max_valid_input": self.max_valid_input,
        }


@dataclass
class EditManifest:
    original_sha256: str
    patched_sha256: str
    function: dict
    edits: list[dict] = field(default_factory=list)
    mappings: list[dict] = field(default_factory=list)
    relocations_added: list[dict] = field(default_factory=list)
    header_changes: dict = field(default_factory=dict)
    scope_authorized: bool = False
    audit: dict = field(default_factory=dict)
    displaced: list[dict] = field(default_factory=list)
    added: list[dict] = field(default_factory=list)
    intervals: dict = field(default_factory=dict)
    writer: str = ""
    termination: str = ""
    tool_versions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": 2,
            "original_sha256": self.original_sha256,
            "patched_sha256": self.patched_sha256,
            "function": self.function,
            "displaced": self.displaced,
            "added": self.added,
            "intervals": self.intervals,
            "header_changes": self.header_changes,
            "writer": self.writer,
            "termination": self.termination,
            "tool_versions": self.tool_versions,
            "edits": self.edits,
            "mappings": self.mappings,
            "relocations_added": self.relocations_added,
            "scope_authorized": self.scope_authorized,
            "audit": self.audit,
        }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _symbols(elf: ELFFile) -> dict[str, tuple[int, int, int]]:
    out: dict[str, tuple[int, int, int]] = {}
    for sec in elf.iter_sections():
        if not isinstance(sec, SymbolTableSection):
            continue
        for sym in sec.iter_symbols():
            if sym.name and sym["st_value"]:
                out.setdefault(sym.name, (sym["st_value"], sym["st_size"], sec["sh_offset"]))
    return out


def _section_for(elf: ELFFile, addr: int):
    for sec in elf.iter_sections():
        start = sec["sh_addr"]
        if start and start <= addr < start + sec["sh_size"]:
            return sec
    return None


def _file_offset(elf: ELFFile, addr: int) -> int:
    for seg in elf.iter_segments():
        if seg["p_type"] != "PT_LOAD":
            continue
        start = seg["p_vaddr"]
        if start <= addr < start + seg["p_filesz"]:
            return seg["p_offset"] + addr - start
    raise RouteAError(f"address {addr:#x} is not in a file-backed PT_LOAD")


def decode_function(elf_path: Path, target: str) -> FunctionInfo:
    with elf_path.open("rb") as fh:
        elf = ELFFile(fh)
        syms = _symbols(elf)
        entry: int
        size: int
        name = target
        if target.lower().startswith("0x"):
            entry = int(target, 16)
            matches = [(n, v) for n, (v, _s, _o) in syms.items() if v == entry]
            name = matches[0][0] if matches else f"0x{entry:x}"
            size = dict((n, (v, s)) for n, (v, s, _o) in syms.items()).get(name, (entry, 0))[1]
        else:
            if target not in syms:
                raise RouteAError(f"symbol not found: {target}")
            entry, size, _ = syms[target]
        if not size:
            raise RouteAError(f"symbol has no size: {name}")
        sec = _section_for(elf, entry)
        if sec is None:
            raise RouteAError("function is not in a section")
        start = entry - sec["sh_addr"]
        data = sec.data()[start:start + size]
        insns = []
        total = 0
        for insn in MD.disasm(data, entry):
            insns.append(Instruction(insn.address, insn.size, insn.bytes.hex(),
                                     insn.mnemonic, insn.op_str))
            total += insn.size
        if total != size:
            raise RouteAError(f"decoded {total} bytes, symbol says {size}")
        return FunctionInfo(name, entry, size, _file_offset(elf, entry), tuple(insns))


def _plt_map(elf: ELFFile) -> dict[int, str]:
    relocs: dict[int, str] = {}
    dyn = elf.get_section_by_name(".dynsym")
    for name in (".rela.plt", ".rel.plt"):
        sec = elf.get_section_by_name(name)
        if sec is None:
            continue
        for rel in sec.iter_relocations():
            sym = dyn.get_symbol(rel["r_info_sym"]) if dyn else None
            if sym and sym.name:
                relocs[rel["r_offset"]] = sym.name
    out: dict[int, str] = {}
    for name in (".plt", ".plt.sec"):
        sec = elf.get_section_by_name(name)
        if sec is None:
            continue
        last_endbr = None
        for insn in MD.disasm(sec.data(), sec["sh_addr"]):
            if insn.mnemonic == "endbr64":
                last_endbr = insn.address
            if insn.mnemonic == "jmp" and "rip" in insn.op_str:
                m = re.search(r"\[rip \+ (0x[0-9a-f]+)\]", insn.op_str)
                if not m:
                    continue
                got = insn.address + insn.size + int(m.group(1), 16)
                sym = relocs.get(got)
                if sym:
                    out[last_endbr if last_endbr is not None else insn.address] = sym
    return out


def _sym(elf: ELFFile, name: str) -> int:
    for tab in (".symtab", ".dynsym"):
        sec = elf.get_section_by_name(tab)
        if sec is None:
            continue
        for sym in sec.iter_symbols():
            if sym.name == name and sym["st_value"]:
                return sym["st_value"]
    raise RouteAError(f"symbol not found: {name}")


def _encode_code(
    *,
    code_vaddr: int,
    message_vaddr: int,
    stderr_vaddr: int,
    fprintf_plt: int,
    max_valid: int,
    resume_at: int,
) -> bytes:
    # mov r8, [rbp-0x48]; inline strlen in r8/rcx
    code = bytearray(b"\x4c\x8b\x45\xb8")          # mov r8, [rbp-0x48]
    code += b"\x31\xc9"                            # xor ecx, ecx
    code += b"\x41\x80\x3c\x08\x00"                # cmp byte [r8+rcx], 0
    code += b"\x74\x05"                            # je after inc+jmp
    code += b"\x48\xff\xc1"                        # inc rcx
    code += b"\xeb\xf4"                            # jmp back to cmp
    code += b"\x48\x81\xf9" + struct.pack("<I", max_valid)  # cmp rcx, imm32
    jbe_at = len(code)
    code += b"\x0f\x86\x00\x00\x00\x00"            # jbe valid
    mov_rdi_at = len(code)
    code += b"\x48\x8b\x3d\x00\x00\x00\x00"        # mov rdi, [rip+stderr]
    lea_rsi_at = len(code)
    code += b"\x48\x8d\x35\x00\x00\x00\x00"        # lea rsi, [rip+msg]
    code += b"\x31\xc0"                            # xor eax, eax
    call_at = len(code)
    code += b"\xe8\x00\x00\x00\x00"                # call fprintf@plt
    code += b"\xb8\xe7\x00\x00\x00"                # mov eax, 231
    code += b"\xbf\x01\x00\x00\x00"                # mov edi, 1
    code += b"\x0f\x05"                            # syscall
    valid_at = len(code)
    jmp_at = len(code)
    code += b"\xe9\x00\x00\x00\x00"                # jmp resume

    def rel32(at: int, target: int, end: int) -> None:
        code[at:at + 4] = struct.pack("<i", target - end)

    rel32(jbe_at + 2, code_vaddr + valid_at, code_vaddr + jbe_at + 6)
    code[mov_rdi_at + 3:mov_rdi_at + 7] = struct.pack("<i", stderr_vaddr - (code_vaddr + mov_rdi_at + 7))
    code[lea_rsi_at + 3:lea_rsi_at + 7] = struct.pack("<i", message_vaddr - (code_vaddr + lea_rsi_at + 7))
    rel32(call_at + 1, fprintf_plt, code_vaddr + call_at + 5)
    rel32(jmp_at + 1, resume_at, code_vaddr + jmp_at + 5)
    return bytes(code)


def plan_route_a(
    elf_path: Path,
    contract: SecurityContract,
    *,
    patch_at: int = 0x4012C0,
    resume_at: int = 0x4012F6,
    message_vaddr: int = 0x405000,
    code_vaddr: int = 0x406000,
) -> PatchPlan:
    target = decode_function(elf_path, contract.target_entry)
    if not (target.entry <= patch_at < target.entry + target.size):
        raise RouteAError("patch point is outside the target function")
    if not contract.invalid_stderr:
        raise RouteAError("contract has no invalid_stderr")
    message = contract.invalid_stderr.encode("ascii")
    if len(message) > 512:
        raise RouteAError("invalid_stderr is longer than 512 bytes")
    with elf_path.open("rb") as fh:
        elf = ELFFile(fh)
        stderr_vaddr = _sym(elf, "stderr")
        plts = _plt_map(elf)
        fprintf = None
        for addr, name in plts.items():
            if name == "fprintf":
                fprintf = addr
                break
        if fprintf is None:
            raise RouteAError("fprintf PLT entry not found")
        detour = b"\xe9" + struct.pack("<i", code_vaddr - (patch_at + 5)) + b"\x90\x90\x90"
    code = _encode_code(
        code_vaddr=code_vaddr,
        message_vaddr=message_vaddr,
        stderr_vaddr=stderr_vaddr,
        fprintf_plt=fprintf,
        max_valid=contract.max_valid_input_bytes,
        resume_at=resume_at,
    )
    return PatchPlan(target, patch_at, resume_at, message, message_vaddr, code_vaddr,
                     detour, code, stderr_vaddr, fprintf, contract.max_valid_input_bytes)


def _loads(path: Path) -> list[dict]:
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        return [{
            "vaddr": seg["p_vaddr"],
            "offset": seg["p_offset"],
            "filesz": seg["p_filesz"],
            "memsz": seg["p_memsz"],
            "flags": seg["p_flags"],
        } for seg in elf.iter_segments() if seg["p_type"] == "PT_LOAD"]


def _bytes_at(path: Path, vaddr: int, size: int) -> bytes:
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        for seg in elf.iter_segments():
            if seg["p_type"] != "PT_LOAD":
                continue
            if seg["p_vaddr"] <= vaddr and vaddr + size <= seg["p_vaddr"] + seg["p_filesz"]:
                fh.seek(seg["p_offset"] + vaddr - seg["p_vaddr"])
                return fh.read(size)
    return b""


def _reloc_counts(path: Path) -> dict[str, int]:
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        out = {}
        for name in (".rela.plt", ".rela.dyn", ".rel.plt", ".rel.dyn"):
            sec = elf.get_section_by_name(name)
            out[name] = sec.num_relocations() if sec else 0
        return out


def audit_layout(original: Path, patched: Path, plan: PatchPlan) -> dict:
    before = _loads(original)
    after = _loads(patched)
    checks = {
        "patch_bytes": _bytes_at(patched, plan.patch_at, len(plan.detour)) == plan.detour,
        "message_bytes": _bytes_at(patched, plan.message_vaddr, len(plan.message)) == plan.message,
        "code_bytes": _bytes_at(patched, plan.code_vaddr, len(plan.code)) == plan.code,
        "original_loads_preserved": all(seg in after for seg in before),
        "harness_unchanged": _bytes_at(original, 0x401256, 90) == _bytes_at(patched, 0x401256, 90),
        "strcpy_call_unchanged": _bytes_at(original, 0x401304, 5) == _bytes_at(patched, 0x401304, 5),
        "relocations_unchanged": _reloc_counts(original) == _reloc_counts(patched),
    }
    new = [seg for seg in after if seg not in before]
    msg_seg = next((s for s in new if s["vaddr"] == plan.message_vaddr), None)
    code_seg = next((s for s in new if s["vaddr"] == plan.code_vaddr), None)
    checks["message_segment"] = bool(msg_seg and msg_seg["flags"] == 4)
    checks["code_segment"] = bool(code_seg and code_seg["flags"] == 5)
    checks["no_wx"] = all(not (s["flags"] & 2 and s["flags"] & 1) for s in after)
    return {
        "checks": checks,
        "new_segments": new,
        "pass": all(v for k, v in checks.items() if k not in ("message_segment", "code_segment"))
        and checks["message_segment"] and checks["code_segment"],
        "original_size": original.stat().st_size,
        "patched_size": patched.stat().st_size,
    }


def build_patched_elf(
    original: Path,
    plan: PatchPlan,
    out: Path,
    *,
    scope_authorized: bool = False,
) -> EditManifest:
    if not scope_authorized:
        raise ScopeNotAuthorized(
            "adding R--/R-X segments and changing layout is a Gate 3 scope decision; "
            "pass scope_authorized=True only after the ruling"
        )
    try:
        import lief
    except ImportError as exc:
        raise RouteAError("lief is required for build") from exc

    binary = lief.ELF.parse(str(original))
    for addr, data, flags in (
        (plan.message_vaddr, plan.message, lief.ELF.SEGMENT_FLAGS.R),
        (plan.code_vaddr, plan.code, lief.ELF.SEGMENT_FLAGS(5)),
    ):
        seg = lief.ELF.Segment()
        seg.type = lief.ELF.SEGMENT_TYPES.LOAD
        seg.flags = flags
        seg.content = list(data)
        seg.virtual_address = addr
        seg.physical_address = addr
        seg.alignment = 0x1000
        binary.add(seg)
    binary.patch_address(plan.patch_at, list(plan.detour))
    binary.write(str(out))

    audit = audit_layout(original, out, plan)
    manifest = EditManifest(
        original_sha256=_sha(original),
        patched_sha256=_sha(out),
        function={"name": plan.target.name, "entry": hex(plan.target.entry),
                  "size": plan.target.size},
        edits=[{
            "kind": "overwrite",
            "vaddr": hex(plan.patch_at),
            "size": len(plan.detour),
            "new_hex": plan.detour.hex(),
            "purpose": "detour to trampoline",
        }],
        mappings=[
            {"vaddr": hex(plan.message_vaddr), "perm": "R",
             "size": len(plan.message), "purpose": "exact invalid stderr"},
            {"vaddr": hex(plan.code_vaddr), "perm": "R-X",
             "size": len(plan.code), "purpose": "guard/exit trampoline"},
        ],
        relocations_added=[],
        header_changes={"original_size": original.stat().st_size,
                        "patched_size": out.stat().st_size},
        scope_authorized=True,
        audit=audit,
    )
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m routea.worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dec = sub.add_parser("decode")
    dec.add_argument("--elf", type=Path, required=True)
    dec.add_argument("--target", required=True)
    dec.add_argument("--out", type=Path)

    pl = sub.add_parser("plan")
    pl.add_argument("--elf", type=Path, required=True)
    pl.add_argument("--contract", type=Path, required=True)
    pl.add_argument("--out", type=Path)

    bd = sub.add_parser("build")
    bd.add_argument("--elf", type=Path, required=True)
    bd.add_argument("--contract", type=Path, required=True)
    bd.add_argument("--out", type=Path, required=True)
    bd.add_argument("--manifest", type=Path, required=True)
    bd.add_argument("--scope-authorized", action="store_true")

    au = sub.add_parser("audit")
    au.add_argument("--original", type=Path, required=True)
    au.add_argument("--patched", type=Path, required=True)
    au.add_argument("--contract", type=Path, required=True)

    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "decode":
        data = decode_function(args.elf, args.target).to_dict()
    else:
        contract = SecurityContract.from_dict(json.loads(args.contract.read_text()))
        plan = plan_route_a(args.elf, contract)
        if args.cmd == "plan":
            data = plan.to_dict()
        elif args.cmd == "build":
            manifest = build_patched_elf(args.elf, plan, args.out,
                                         scope_authorized=args.scope_authorized)
            args.manifest.write_text(json.dumps(manifest.to_dict(), indent=2))
            data = manifest.to_dict()
        else:
            data = audit_layout(args.original, args.patched, plan)
    text = json.dumps(data, indent=2)
    if getattr(args, "out", None):
        args.out.write_text(text)
    print(text)
    return 0 if data.get("pass", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
