# Limitations of this MVP

This repository is an accepted MVP **with recorded limitations**. Do not cite it
as a fully independent, binary-level-proven, universally green product.

## 1. V4 is model-level

V4 uses a hand-written z3 model of the guard / out-of-bounds behaviour, bounded
to 64 bytes. It is not binary-level symbolic execution and not a whole-program
proof. The model's guard logic is declared from the reviewed function semantics;
it is not recovered automatically from the binary.

## 2. Coverage is one fixture

The MVP targets one public CWE-121 fixture
(`CWE121_src_char_declare_cpy_51b`). It is not evidence that arbitrary binaries,
other architectures, other compilers, or stripped production binaries work.

## 3. Full test suite is not green everywhere

- The host used for final integration lacked `pyelftools` / `capstone` for some
  ELF-dependent modules and could not run the browser e2e suite.
- Measured final integration suite: `265 collected / 262 passed / 2 skipped /
  1 failed`; the failure is a Windows `readelf`/`objdump` environment gap.
- Missing tools are recorded as `not_run` / environment gaps, never as passes.

## 4. M4 verifier and Lead approver were the same operator

The verification row and approval row were signed by the same operator. This is
**not independent two-person review**. If a client requires genuinely independent
approval, that requirement is not satisfied.

## 5. Gate 4 F2 waiver

The independent p2 rerun (checklist item F2) was waived. Gate 4 accepted the
recorded verification-only JSON and the recorder's exit code; it did not
re-execute the pipeline independently.

## 6. Gate 5 split successor chain

- Route A evidence is bound to successor `90af91ef…ff20`.
- Route B evidence is bound to successor `0066973d…9eee`.
- Route A was not rerun under the Route B successor. A future single-binary
  requirement would need one Route A re-run.

## 7. Gate 5 Route B evidence details

- The Route B zip did not duplicate `original.elf`; the original hash was verified
  against the frozen fixture.
- R2 used runtime maps plus an independent ELF parse rather than a separate fresh
  `readelf` dump.

## 8. Not a production product

This is a course/MVP deliverable, not a supported commercial vulnerability
remediation service. No client binaries, credentials or private logs are included.
