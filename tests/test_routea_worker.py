from __future__ import annotations

from pathlib import Path

import pytest
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

from app.domain.contracts import SecurityContract
from routea.worker import (
    ScopeNotAuthorized,
    audit_layout,
    build_patched_elf,
    decode_function,
    plan_route_a,
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


def test_decode_target_function() -> None:
    fn = decode_function(ELF, FUNC)
    assert fn.name == FUNC
    assert fn.entry == 0x4012B0
    assert fn.size == 104
    assert len(fn.instructions) == 23
    assert fn.instructions[0].mnemonic == "endbr64"
    assert fn.instructions[-1].mnemonic == "ret"
    assert any(i.mnemonic == "call" and i.op_str == "0x4010d0" for i in fn.instructions)


def test_plan_detour_and_trampoline() -> None:
    plan = plan_route_a(ELF, contract())
    assert plan.patch_at == 0x4012C0
    assert plan.detour == bytes.fromhex("e93b4d0000909090")
    assert plan.message_vaddr == 0x405000
    assert plan.code_vaddr == 0x406000
    assert plan.stderr_vaddr == 0x404080
    assert plan.fprintf_plt == 0x401120
    assert plan.message == b"ERROR: input exceeds destination capacity\n"
    assert b"\x0f\x05" in plan.code  # exit_group syscall
    insns = list(Cs(CS_ARCH_X86, CS_MODE_64).disasm(plan.code, plan.code_vaddr))
    jbe = next(i for i in insns if i.mnemonic == "jbe")
    jmp = next(i for i in insns if i.mnemonic == "jmp" and i.address > jbe.address)
    assert int(jbe.op_str, 16) == plan.code_vaddr + 0x40
    assert int(jmp.op_str, 16) == plan.resume_at


def test_build_requires_scope_authorization() -> None:
    plan = plan_route_a(ELF, contract())
    with pytest.raises(ScopeNotAuthorized):
        build_patched_elf(ELF, plan, Path("/tmp/never.elf"))


def test_audit_rejects_unpatched_binary() -> None:
    plan = plan_route_a(ELF, contract())
    result = audit_layout(ELF, ELF, plan)
    assert result["pass"] is False
    assert result["checks"]["patch_bytes"] is False
    assert result["checks"]["message_bytes"] is False
