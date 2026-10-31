from __future__ import annotations

import json
from pathlib import Path

import pytest
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

from app.domain.contracts import SecurityContract
from routea.cave import (
    ScopeNotAuthorized,
    audit_cave_layout,
    build_cave_patched,
    plan_route_a_cave,
)

ROOT = Path(__file__).resolve().parents[1]
ELF = ROOT / "fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf"
FUNC = "CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_51b_badSink"


def contract() -> SecurityContract:
    return SecurityContract(
        target_entry=FUNC,
        target_signature="void badSink(char *data)",
        valid_domain="C string inputs of length 0..49 bytes; the terminating NUL is copied",
        valid_behavior="strcpy into dest[50] and printLine(data); exit 0",
        forbidden_behavior="write past dest[50] for inputs of length 50 or more",
        invalid_input_response="print the exact stderr line and exit 1 without printing stdout",
        dest_capacity_bytes=50,
        max_valid_input_bytes=49,
        capacity_includes_terminator=True,
        invalid_exit_code=1,
        invalid_stdout="",
        invalid_stderr="ERROR: input exceeds destination capacity\n",
        allowed_calls=("strcpy", "printLine"),
        environment_assumptions=("Linux x86-64", "non-PIE", "CET enabled"),
    )


def test_cave_plan_redirects_call_and_builds_guard() -> None:
    plan = plan_route_a_cave(ELF, contract(), cave_addr=0x401700, cave_size=0x100)
    assert plan.call_site == 0x401304
    assert plan.redirect == bytes.fromhex("e8f7030000")
    assert plan.cave_addr == 0x401700
    assert plan.message_addr == 0x401747
    insns = list(Cs(CS_ARCH_X86, CS_MODE_64).disasm(plan.code, plan.cave_addr))
    assert sum(1 for i in insns if i.mnemonic == "syscall") == 2
    assert not any(i.mnemonic == "call" for i in insns)
    valid_jmp = next(i for i in insns if i.mnemonic == "jmp" and i.op_str == "0x4010d0")
    assert valid_jmp is not None
    assert b"\x0f\x05" in plan.code


def test_cave_too_small_is_rejected() -> None:
    with pytest.raises(Exception):
        plan_route_a_cave(ELF, contract(), cave_addr=0x401700, cave_size=8)


def test_cave_build_requires_scope() -> None:
    plan = plan_route_a_cave(ELF, contract(), cave_addr=0x401700, cave_size=0x100)
    with pytest.raises(ScopeNotAuthorized):
        build_cave_patched(ELF, plan, Path("/tmp/never-cave.elf"))


def test_cave_audit_rejects_unpatched() -> None:
    plan = plan_route_a_cave(ELF, contract(), cave_addr=0x401700, cave_size=0x100)
    result = audit_cave_layout(ELF, ELF, plan)
    assert result["pass"] is False
    assert result["checks"]["redirect_bytes"] is False


def test_cave_extends_rx_segment_and_audits(tmp_path) -> None:
    plan = plan_route_a_cave(ELF, contract(), cave_addr=0x4018E9, cave_size=1815)
    assert plan.rx_vaddr == 0x401000
    assert plan.rx_new_size == 0x1000
    assert plan.redirect == bytes.fromhex("e8e0050000")
    assert plan.message_addr == 0x401930
    out = tmp_path / "cave_probe.elf"
    manifest = build_cave_patched(ELF, plan, out, scope_authorized=True)
    assert manifest.audit["pass"] is True
    assert manifest.audit["checks"]["rx_extended"] is True
    assert manifest.audit["checks"]["no_new_segments"] is True
    assert manifest.audit["checks"]["redirect_bytes"] is True
    assert out.stat().st_size == ELF.stat().st_size
