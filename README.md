# BinaryRepair

BinaryRepair is a local analyst tool for repairing one known vulnerability in a
Linux x86-64 ELF **without original source**. Two independent repair routes
produce candidate bytes, and one deterministic acceptance engine decides whether
the final bytes are `success_within_scope`. A model explanation is never
evidence.

This is a curated public snapshot of the accepted project. The development
history and historical evidence remain in the source repository
[`chenfanaichifan/binary-repair`](https://github.com/chenfanaichifan/binary-repair).

> **给组员 / 第一次使用？** 先看
> [`START-HERE.md`](START-HERE.md)；界面与当前功能截图在
> [`docs/showcase/`](docs/showcase/)。Windows 双击 `start-gui.bat`，macOS/Linux
> 运行 `./start-gui.sh`。

```text
Repository  https://github.com/leesirleesirlee-star/Binary_Repair
Version     v1.2.2
Source      chenfanaichifan/binary-repair
            release/v1.2.1 @ 3418491 (source and corrected documentation)
```

## Current status

| Item | State |
|---|---|
| T8.3 | **COMPLETE** |
| P1 | **success_within_scope** |
| P2 | **ACCEPTANCE PASSED** |
| Test suite | **827 passed / 2 skipped / 1 known macOS `readelf` environment failure** (2026-11-12) |
| Browser e2e | **12 passed** (Chrome CDP, included in the suite) |
| Upload gate | **64 MiB client check + HTTP-only controls** |
| V4 | **passed at model scope** (not binary-level proof) |
| 118 | **VERIFIED** (independent) |

See [`docs/status/final-status.md`](docs/status/final-status.md) and
[`docs/acceptance/LIMITATIONS.md`](docs/acceptance/LIMITATIONS.md) before citing
this project as proof.

## What it does

- **Route A** redirects the vulnerable call into a reviewed code cave.
- **Route B** recovers C, recompiles it, and re-integrates the function.
- A shared engine checks V0–V5 on the final candidate and returns
  `success_within_scope`, `inconclusive`, `rejected`, `unsupported`, `error`, or
  `cancelled`.
- The GUI/API only render verdicts; they never decide them.
- The host never executes uploaded or repaired binaries. Parsing, compilation,
  execution and symbolic analysis belong to the disposable guest under the
  broker.
- The workbench also accepts pasted source for static code repair (CWE-tagged
  findings, patch/diff, no execution), keeps task conversations server-side,
  and exposes a connector/tool inventory with install guides. See
  [`docs/architecture/code-repair.md`](docs/architecture/code-repair.md),
  [`docs/architecture/history.md`](docs/architecture/history.md) and
  [`docs/reference/binary-repair-landscape.md`](docs/reference/binary-repair-landscape.md).
  The workbench also ships a `/settings` preferences page, a complete `/guide`
  operator manual, task search/pin/rename, a composer `+` menu for local
  code/text files and vulnerable snippets, a `Ctrl/Cmd+K` command palette,
  nine UI languages, a responsive drawer sidebar and restrained page-transition
  effects. Language coverage and precision boundaries are documented in
  [`docs/reference/i18n.md`](docs/reference/i18n.md).

## Repository layout

```text
app/                  FastAPI controller, services, domain, Jinja UI, static assets
binaryrepair_ai/      provider adapter and typed AI boundary
routea/               Route A components
routeb/               Route B components
workers/              guest executor and Route A worker
tools/                ingestion, P2 acceptance, MCP adapters
schemas/              protocol and evidence schemas
scripts/              operator scripts
tests/                unit, integration and browser e2e tests
fixtures/             public fixtures
artifacts/            canonical acceptance evidence
docs/                 product, architecture, acceptance, operations, reviews
```

See [`docs/README.md`](docs/README.md) for the documentation map and
[`docs/development/repository-layout.md`](docs/development/repository-layout.md)
for a file-by-file guide.

Binary overflow surface limits: [`docs/reference/binary-overflow-surface.md`](docs/reference/binary-overflow-surface.md).

CGC subset (static inventory only): [`docs/reference/cgc-access.md`](docs/reference/cgc-access.md).

Fixed LAN access: [`docs/reference/lan-fixed-access.md`](docs/reference/lan-fixed-access.md).

Bounded AI adjudication: [`docs/architecture/ai-adjudication.md`](docs/architecture/ai-adjudication.md).

Binary dataset/tool selection: [`docs/reference/binary-vulnerability-datasets.md`](docs/reference/binary-vulnerability-datasets.md).

Overflow AI upgrade report: [`docs/acceptance/overflow-ai-2026-11-12.md`](docs/acceptance/overflow-ai-2026-11-12.md).

Overflow candidate memory and honest metrics: [`knowledge/README.md`](knowledge/README.md).

Patch validation gates and scope model: [`docs/reference/patch-validation.md`](docs/reference/patch-validation.md).

C overflow patterns and repair principles: [`docs/reference/buffer-overflow-patterns.md`](docs/reference/buffer-overflow-patterns.md).

Repair quality/efficiency re-evaluation: [`docs/acceptance/repair-evaluation-2026-11-12.md`](docs/acceptance/repair-evaluation-2026-11-12.md).

Final delivery notes: [`DELIVERY.md`](DELIVERY.md).

## Quick start

The launcher opens the welcome page at `/welcome`. The main workspace is the
conversation at `/ui/chat/` (attach the binary, review and approve the contract,
start and watch the run); the archive at `/ui/` holds task state, checks,
evidence and export. Both pages share the same local session and contract-v1
API. The [UI guide](app/static/ui/README.md) covers mock, controller and worker
commands, and the [frontend handoff](docs/handoff/2026-11-17-frontend-delivery.md)
records what is verified. A synthetic design preview remains at
`/ui/chat/?preview=1` and never contacts the job API.

### macOS / Linux

```bash
./start-gui.sh
```

### Windows

```powershell
.\start-gui.ps1
```

The launcher creates `.venv`, installs `requirements.txt`, generates a local
session token, and prints the authenticated URL
`http://127.0.0.1:8123/welcome?token=...`. Open that URL in a browser. No controller or
worker process is required for the demo backend.

The current product surfaces are `/welcome`, `/ui/chat/` and `/ui/`. The older
`/agent`, `/settings`, `/guide`, `/methods` and `/connectors` pages remain
available for historical workflows. For a quick tour, read `START-HERE.md`; for
screenshots, see `docs/showcase/`.

Run the test suite:

```bash
python -m pytest -q
```

The recorded acceptance environment is Linux with Chrome CDP for browser e2e.
Windows hosts skip Linux-only cases and POSIX-fixture tests; use Linux for the
full acceptance record.

## Canonical evidence

```text
artifacts/p1-real-window-2026-11-07/           P1 job, snapshot, P2, V5 replay
artifacts/p1-routea-v4-symbolic-2026-11-07/    Route A V4 marker
artifacts/p1-routea-real-v4-2026-11-07/        Route A real V4 artifacts
artifacts/p1-routea-v4-input-2026-11-07/       Route A V4 input package
artifacts/v4-harness-2026-11-07/               V4 harness package
artifacts/cwe416-124-v4-rerun-2026-11-06/      124 V0–V5 rerun evidence
artifacts/m4/v4-2026-11-07/                    M4 V4 marker and five criteria
artifacts/m4-revalidation-ebc92a8/             M4 revalidation
artifacts/p1-v5-logs-2026-11-07/               original V5 replay logs
```

## Sharing with a team (WeChat)

For same-Wi-Fi teammates, run a dedicated demo instance on `0.0.0.0` with a
share-only token and send the LAN URL. For remote teammates, send the ZIP plus
`START-HERE.md`; or expose the demo instance through a temporary HTTPS tunnel.
Do not expose the local instance with a real provider key or real task history.
See [`SHARE-WECHAT.md`](SHARE-WECHAT.md) for the exact message template and
safety checklist.

## Verification and limitations

- **V4 is model-level.** It is a typed symbolic claim over the target function,
  not a binary-level whole-program proof.
- **M4 verifier and Lead approver are the same operator.** This is
  role-separated, not independent two-person review.
- **Review 9–12 original text is not recovered.** The verdict is against a
  canonical reconstruction.
- **Coverage is one public fixture family.** Other binaries, architectures,
  compilers and stripped production binaries are not covered.
- **No client binary, credential, private key or private log is included.**
- **Not a production product.** This is an accepted course/MVP deliverable.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
and [`SECURITY.md`](SECURITY.md). Changes that touch acceptance evidence must
keep `artifacts/**/SHA256SUMS` verifiable.

## License and contributors

See [`LICENSE`](LICENSE), [`CONTRIBUTORS.md`](CONTRIBUTORS.md) and
[`CHANGELOG.md`](CHANGELOG.md).
