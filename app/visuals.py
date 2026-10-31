"""Synthetic disassembly / recovered-C visualization data for the GUI demo.

The data is intentionally synthetic and clearly labelled in the UI: it exists to
demonstrate the interactive code view (vulnerability highlighting, patched code,
diff and AI-candidate inspection). Real content will be supplied by the analysis
workers; the data model below is what the GUI expects.

All data strings are English ASCII (language policy). Chinese labels live in the
template only.
"""

from __future__ import annotations

from typing import Any

VULN_ADDR = "0x0000000000401136"

DISASSEMBLY_ORIGINAL: list[dict[str, Any]] = [
    {"addr": "0x0000000000401120", "bytes": "55", "text": "push rbp"},
    {"addr": "0x0000000000401121", "bytes": "48 89 e5", "text": "mov rbp, rsp"},
    {"addr": "0x0000000000401124", "bytes": "48 83 ec 20", "text": "sub rsp, 0x20"},
    {"addr": "0x0000000000401128", "bytes": "48 8d 45 e0", "text": "lea rax, [rbp-0x20]"},
    {"addr": "0x000000000040112c", "bytes": "48 89 c7", "text": "mov rdi, rax"},
    {"addr": "0x000000000040112f", "bytes": "48 8b 45 f8", "text": "mov rax, [rbp-0x8]"},
    {"addr": "0x0000000000401133", "bytes": "48 89 c6", "text": "mov rsi, rax"},
    {
        "addr": VULN_ADDR,
        "bytes": "e8 75 fe ff ff",
        "text": "call strcpy@plt",
        "tag": "vulnerable",
        "detail": "Unbounded strcpy into char buf[8]; input length is not checked before the call.",
    },
    {"addr": "0x000000000040113b", "bytes": "b8 00 00 00 00", "text": "mov eax, 0"},
    {"addr": "0x0000000000401140", "bytes": "c9", "text": "leave"},
    {"addr": "0x0000000000401141", "bytes": "c3", "text": "ret"},
]

DISASSEMBLY_REPAIRED: list[dict[str, Any]] = [
    {"addr": "0x0000000000401120", "bytes": "55", "text": "push rbp"},
    {"addr": "0x0000000000401121", "bytes": "48 89 e5", "text": "mov rbp, rsp"},
    {"addr": "0x0000000000401124", "bytes": "48 83 ec 20", "text": "sub rsp, 0x20"},
    {"addr": "0x0000000000401128", "bytes": "48 8d 45 e0", "text": "lea rax, [rbp-0x20]"},
    {"addr": "0x000000000040112c", "bytes": "48 89 c7", "text": "mov rdi, rax"},
    {"addr": "0x000000000040112f", "bytes": "48 8b 45 f8", "text": "mov rax, [rbp-0x8]"},
    {"addr": "0x0000000000401133", "bytes": "48 89 c6", "text": "mov rsi, rax"},
    {
        "addr": "0x0000000000401136",
        "bytes": "48 83 fe 08",
        "text": "cmp rsi, 0x8",
        "tag": "added",
        "detail": "Added length guard: reject inputs that do not fit the 8-byte buffer.",
    },
    {"addr": "0x000000000040113a", "bytes": "77 04", "text": "ja 0x401140", "tag": "added"},
    {
        "addr": "0x000000000040113c",
        "bytes": "e8 6f fe ff ff",
        "text": "call strcpy@plt",
        "tag": "patched",
        "detail": "Reached only when the length check passes; displaced 4 bytes are re-encoded.",
    },
    {"addr": "0x0000000000401141", "bytes": "b8 00 00 00 00", "text": "mov eax, 0"},
    {"addr": "0x0000000000401146", "bytes": "c9", "text": "leave"},
    {"addr": "0x0000000000401147", "bytes": "c3", "text": "ret"},
]

DISASSEMBLY_DIFF: list[dict[str, Any]] = [
    {"addr": "0x0000000000401120", "text": "push rbp", "status": "same"},
    {"addr": "0x0000000000401133", "text": "mov rsi, rax", "status": "same"},
    {"addr": "0x0000000000401136", "text": "cmp rsi, 0x8", "status": "added"},
    {"addr": "0x000000000040113a", "text": "ja 0x401140", "status": "added"},
    {
        "addr": VULN_ADDR,
        "text": "call strcpy@plt",
        "status": "changed",
        "detail": "The vulnerable call is now guarded; the branch skips it for oversized inputs.",
    },
    {"addr": "0x0000000000401141", "text": "mov eax, 0", "status": "same"},
]

C_RECOVERED: list[dict[str, Any]] = [
    {"line": 1, "text": "void stack_buf(char *input) {"},
    {"line": 2, "text": "  char buf[8];"},
    {"line": 3, "text": "  strcpy(buf, input);", "tag": "vulnerable", "detail": "strcpy has no bound; a 64-byte input overflows buf."},
    {"line": 4, "text": "  return;"},
    {"line": 5, "text": "}"},
]

C_REPAIRED: list[dict[str, Any]] = [
    {"line": 1, "text": "void stack_buf(char *input) {"},
    {"line": 2, "text": "  char buf[8];"},
    {"line": 3, "text": "  if (strlen(input) >= sizeof(buf)) {", "tag": "added", "detail": "Guard added during the recovered-C repair."},
    {"line": 4, "text": "    return;", "tag": "added"},
    {"line": 5, "text": "  }", "tag": "added"},
    {"line": 6, "text": "  strcpy(buf, input);", "tag": "patched", "detail": "Original call now executes only within the buffer bound."},
    {"line": 7, "text": "  return;"},
    {"line": 8, "text": "}"},
]

C_DIFF: list[dict[str, Any]] = [
    {"line": 1, "text": "void stack_buf(char *input) {", "status": "same"},
    {"line": 2, "text": "  char buf[8];", "status": "same"},
    {"line": 3, "text": "  if (strlen(input) >= sizeof(buf)) {", "status": "added"},
    {"line": 4, "text": "    return;", "status": "added"},
    {"line": 5, "text": "  }", "status": "added"},
    {"line": 6, "text": "  strcpy(buf, input);", "status": "patched"},
    {"line": 7, "text": "  return;", "status": "same"},
    {"line": 8, "text": "}", "status": "same"},
]

CANDIDATE_A: dict[str, Any] = {
    "template_id": "guard-strcpy-v1",
    "model": "deepseek-flash",
    "rationale": "Insert a length guard before the strcpy call so writes stay within the 8-byte buffer.",
    "assumptions": [
        "input is NUL-terminated",
        "single-threaded process",
        "stack layout unchanged",
    ],
    "uncertainties": ["guard placement assumes no incoming jumps into the call"],
    "attempts": 2,
    "ai_usage": "12.4k input / 1.6k output tokens",
}

CANDIDATE_B: dict[str, Any] = {
    "template_id": "recovered-function-repair-v1",
    "model": "deepseek-flash",
    "rationale": "Add a bounds check in the recovered function, recompile and reintegrate the whole function.",
    "assumptions": [
        "Ghidra recovery is semantically close to the original",
        "the function interface is stable",
        "layout policy allows the added code region",
    ],
    "uncertainties": ["unresolved symbolic obligation for the memory-safety property"],
    "attempts": 1,
    "ai_usage": "8.2k input / 1.4k output tokens",
}


def build(job: dict[str, Any]) -> dict[str, Any]:
    """Return the interactive code-view payload for one job (synthetic demo data)."""

    route_a = {
        "name": "Route A - Direct instruction repair",
        "mode": "asm",
        "function": "stack_buf",
        "range": "0x401120-0x401147",
        "vulnerable": {
            "addr": VULN_ADDR,
            "reason": "Unbounded strcpy into char buf[8]; no length check before the call.",
            "contract": "forbidden: out-of-bounds write to the target buffer",
        },
        "original": DISASSEMBLY_ORIGINAL,
        "repaired": DISASSEMBLY_REPAIRED,
        "diff": DISASSEMBLY_DIFF,
        "candidate": CANDIDATE_A,
    }
    route_b = {
        "name": "Route B - Recovered C reintegration",
        "mode": "c",
        "function": "stack_buf",
        "range": "recovered function",
        "vulnerable": {
            "addr": VULN_ADDR,
            "reason": "Recovered C calls strcpy with no bound on the 8-byte buffer.",
            "contract": "forbidden: out-of-bounds write to the target buffer",
        },
        "original": C_RECOVERED,
        "repaired": C_REPAIRED,
        "diff": C_DIFF,
        "candidate": CANDIDATE_B,
    }
    return {
        "available": True,
        "demo": bool(job.get("demo")),
        "note": (
            "Synthetic visualization for the demo: disassembly and recovered C are sample data, "
            "not output from an execution run."
        ),
        "job_id": job.get("id"),
        "routes": {"A": route_a, "B": route_b},
    }
