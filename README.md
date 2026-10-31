# BinaryRepair — AI-Assisted Binary Vulnerability Repair MVP

BinaryRepair is a local analyst tool that repairs one known vulnerability in a
Linux x86-64 ELF **without rebuilding it from source**, through two independent
repair routes, and decides the result with one deterministic acceptance engine.
A model explanation is never evidence; only checks on the final bytes count.

This repository is a **curated snapshot** of the accepted MVP, extracted from
the final tag `mvp-2026-10-31` of the original development repository. It is not
a universal binary-repair tool and it is not a commercial product.

## MVP status

**Accepted with recorded limitations** — see [`docs/acceptance/LIMITATIONS.md`](docs/acceptance/LIMITATIONS.md).

| Gate | Status |
|---|---|
| Gate 3 — Route A feasibility | CLOSED / APPROVED |
| Gate 4 — Route B unchanged round trip | PASS (strict scope) |
| Gate 5 Route A — safe placement | PASS |
| Gate 5 Route B — safe placement | PASS |
| V4 — bounded symbolic (model-level) | PASS, bound 64 |
| Fixture | FROZEN (candidate scope) |

The source snapshot is tagged in the original repository at:

```text
main: 753c0973c4eeac614fcc457dd327fd93ecf6a119
tag:  mvp-2026-10-31
```

## What it does

The product is a GUI + controller + worker for a single public CWE-121 fixture:

- **Route A** produces a candidate by redirecting the vulnerable call into a
  reviewed code cave.
- **Route B** recovers C, recompiles it, and re-integrates the function.
- A shared acceptance engine checks V0–V5 on the final candidate and returns
  `success_within_scope`, `inconclusive`, `rejected`, `unsupported`, `error`, or
  `cancelled`. The GUI only renders those outcomes.

The host never executes uploaded or repaired binaries. Execution, compilation
and symbolic analysis belong to the disposable guest under the broker.

## Quick start

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.ps1
```

Then open the printed `http://127.0.0.1:8123/?token=...` URL.

Controller and worker (separate terminals, loopback only):

```powershell
.\start-controller.ps1
.\start-worker.ps1
```

GUI against the controller:

```powershell
$env:BINARYREPAIR_BACKEND = "http"
$env:BINARYREPAIR_API_BASE = "http://127.0.0.1:8000"
.\start.ps1
```

### Ubuntu (target runtime)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./start.sh
```

`start.sh` is marked unverified on Ubuntu until it is actually run on the target
host; see `docs/acceptance/LIMITATIONS.md`.

### Tests

```bash
python -m pytest tests/unit/test_acceptance.py -q
python -m pytest tests/integration/test_p2_acceptance.py -q
python -m pytest tests/integration/test_attempt_writer.py -q
```

The full suite needs `pyelftools`, `capstone`, binutils and a browser for the
e2e tests; missing tools are recorded as environment gaps, never as passes.

## Repository layout

```text
app/                  GUI, controller, domain, services, SQLite layer
binaryrepair_ai/      DeepSeek provider adapter
routea/               Route A candidate builder and audit
routeb/               Route B recovered-C / re-integration tooling
tools/                P0/P2 acceptance and controller helpers
scripts/              handoff checker, demo, verification helpers
tests/                unit / integration / contract / e2e tests
fixtures/public/      public CWE-121 fixture, tests and frozen manifests
schemas/              candidate/context JSON schemas
workers/              worker entry points
docs/product/         PRD, technical design, research, verification design
docs/acceptance/      final gate status, evidence hashes, limitations
docs/demo/            setup → compare → evidence → export walkthrough
docs/interfaces/      route/worker interface notes
docs/evidence/        evidence manifest index
```

## Verification and evidence

The final acceptance records live in [`docs/acceptance/`](docs/acceptance/).

- Gate 4: strict unchanged round-trip, P4 §B/C, `gate4_pass=true`, six cases 6/6.
- Gate 5 Route A: probe, six cases, mapping, memory=file, breakpoints, isolation.
- Gate 5 Route B: `208574ab…6717`, 43/43 evidence re-hash, isolation/fail-closed.
- V4: hand-written z3 model, bound 64; **model-level**, not binary-level.

Key evidence hashes and the final tag are listed in
[`docs/evidence/README.md`](docs/evidence/README.md).

## Known limitations

Read [`docs/acceptance/LIMITATIONS.md`](docs/acceptance/LIMITATIONS.md) before
citing this repository. In short:

- V4 is a model-level bounded result, not a binary-level proof.
- The full test suite is not green on every environment; missing tools are
  recorded as `not_run`.
- The M4 verifier and Lead approver were the same operator; this is **not**
  independent two-person review.
- Gate 4 F2 independent p2 rerun was waived.
- Gate 5 uses split successor binaries for Route A and Route B; Route A was not
  rerun under the Route B successor.
- The repository is a curated snapshot of one MVP, not a general-purpose repair
  product.

## License

MIT — see [`LICENSE`](LICENSE).

## Contributors

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the five-member team and GitHub handles.
