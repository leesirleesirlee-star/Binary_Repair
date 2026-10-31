# MVP acceptance — 2026-10-31

**Decision:** accepted with recorded limitations.
**Final source snapshot:** `753c0973c4eeac614fcc457dd327fd93ecf6a119`
**Final tag (original repo):** `mvp-2026-10-31` (annotated object
`9d6dba82a92681990fb0cff18c17cdb021805aab`).

## Gate status

| Gate | Result | Scope |
|---|---|---|
| Gate 3 — Route A feasibility | CLOSED / APPROVED | signed record, unchanged |
| Gate 4 — Route B formal | PASS | unchanged round-trip only |
| Gate 5 Route A — placement | PASS | probe + isolation, record 51 |
| Gate 5 Route B — placement | PASS | probe + isolation, record 80 |
| V4 — bounded symbolic | PASS (model-level, bound 64) | hand-written z3 model; not binary-level |
| Gate 6 — violation detection | accepted via V4 model-level | concrete crash + model |

The fixture is **FROZEN for the candidate scope**.

## Gate 4 evidence

- P4 package: checker PASS 26/26; own SHA-256SUMS 26/26.
- `gate4=pass`; native six cases `same_count=6`, `total=6`.
- original `aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393`
- round trip `decdcdb821bfe4c41a070b0a23a2f6497f46a92f439cb02a1e4139a82cde954b`
- isolation: uid 1003, CapEff 0, NoNewPrivs 1, egress blocked.
- Gate 4 record hash: `1caec4848e6266a7b6a74381ec9fa3166ec76c052fd829134b9b109825a21c6d`.

## Gate 5 evidence

Route A:

- probe `f39a0302dd1aa42635da80fa4e9160d37a221f5ac383270add3eaedcffd146cb`
- successor `90af91ef6b238428d202f891e5829ed81733b76dd9e7f184acd91f4cd769ff20`
- candidate `c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82`
- six cases 6/6; mapping `r-xp`; memory=file 1815 B; fail-closed mismatch.

Route B:

- probe `05d9a2cd417880c9be51e1a4cd4608158d94b50da233b223c2f4a589850edb82`
- successor `0066973d5304e06024b56fbc1e9d969d5007eac290314935129517f931209eee`
- candidate `208574ab5b7753fab6f28860830069770622fb7b1fcb8d181fa50453f8656717`
- six cases 6/6; entry `0x4012b0 -> 0x4018ec`; tail memory=file 1815 B.
- evidence root manifest `43/43`, evidence manifest `30/30`.

Split-chain decision: Route A evidence remains bound to `90af91ef`; Route B to
`0066973d`; Route A was **not** rerun under the new successor.

## V4 evidence

- Hand-written z3 model of the guard/OOB behaviour.
- bound 64; exact solver accounting.
- original: SAT; Route A/B candidates: UNSAT; deliberately defective candidate: SAT.
- This is **model-level** evidence, not binary-level or whole-program symbolic
  execution.

## Known limitations

See [`LIMITATIONS.md`](LIMITATIONS.md).
