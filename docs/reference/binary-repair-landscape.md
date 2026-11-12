# Binary repair: tool and method landscape

This note records the external tooling the workbench exposes as connectors and
the reasoning behind the "static → symbolic → dynamic → differential → fuzz"
ordering. It is a map, not a claim that one tool proves a repair.

## Static recovery

| Tool | Use | Link |
|---|---|---|
| Ghidra | headless decompilation, symbol/type recovery | <https://ghidra-sre.org/> |
| Rizin / radare2 | disassembly, function recovery, binary diffing | <https://rizin.re/> |
| GNU binutils | `objdump` / `readelf` evidence for the Route B static audit | <https://www.gnu.org/software/binutils/> |
| Diaphora / BinDiff | compare pre/post-patch binaries; locate changed functions | <https://github.com/GNOME/diaphora> · <https://www.zynamics.com/bindiff.html> |

## Symbolic exploration

| Tool | Use | Link |
|---|---|---|
| angr | CFG recovery, path exploration and constraint solving | <https://angr.io/> |
| Triton | dynamic symbolic execution, taint and constraint reasoning | <https://triton-library.github.io/> |

Symbolic success means “a path exists under the model”, not “the patched
binary is correct”. It is a candidate generator, not a verdict.

## Dynamic observation and emulation

| Tool | Use | Link |
|---|---|---|
| Frida | runtime instrumentation, API tracing | <https://frida.re/docs/home/> |
| GDB | recorded-session debugging, crash inspection | <https://sourceware.org/gdb/documentation/> |
| QEMU | user/system emulation for cross-architecture replays | <https://www.qemu.org/docs/master/> |
| Docker | container boundary for untrusted samples/tools | <https://docs.docker.com/> |

## Regression and adversarial input

| Tool | Use | Link |
|---|---|---|
| AFL++ | coverage-guided fuzzing of the reproducer and neighbours | <https://aflplus.plus/> |
| syzkaller | kernel/syscall fuzzing (when the target is a syscall surface) | <https://github.com/google/syzkaller> |
| OSS-Fuzz | continuous fuzzing infrastructure patterns | <https://github.com/google/oss-fuzz> |

## Why the ordering

1. **Static** narrows the changed region and produces auditable evidence.
2. **Symbolic** asks which inputs reach the defect; it scales to small,
   well-bounded functions.
3. **Dynamic** confirms the path in a real or emulated environment and records
   concrete registers/memory.
4. **Differential** compares the original and candidate; an unchanged
   behaviour outside the target is itself evidence.
5. **Fuzz** looks for regressions and adjacent paths; absence of a crash is not
   proof of safety.

The workbench keeps these as separate evidence domains. A tool being installed
(connector “已就绪”) does not mean it ran; only an execution receipt counts.

## Verification references

- CWE-78 OS Command Injection: <https://cwe.mitre.org/data/definitions/78.html>
- CWE-121 Stack-based Buffer Overflow: <https://cwe.mitre.org/data/definitions/121.html>
- CWE-787 Out-of-bounds Write: <https://cwe.mitre.org/data/definitions/787.html>
- CWE-95 Eval Injection: <https://cwe.mitre.org/data/definitions/95.html>
- CWE-502 Deserialization of Untrusted Data: <https://cwe.mitre.org/data/definitions/502.html>
- OWASP Top 10: <https://owasp.org/www-project-top-ten/>
- OWASP Injection: <https://owasp.org/Top10/A03_2021-Injection/>
- OWASP Buffer Overflow: <https://owasp.org/www-community/vulnerabilities/Buffer_Overflow>
