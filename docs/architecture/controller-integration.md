# Controller integration (P0 checklist for Member 2)

The GUI never invents job state. It reads a controller through one adapter
interface and renders whatever the controller reports.

## Ready-to-run helpers

- `.\start-controller.ps1` — serves the canonical controller `app.controller_api:app`
  from this checkout on `127.0.0.1:8000`, with
  `BINARYREPAIR_DATA_DIR=<repo>\var` (the same default as `.\start-worker.ps1`;
  set the variable explicitly to share a different store). Before the
  2026-10-29 merge this script created a separate worktree of `origin/main`; the
  canonical controller now lives in the same tree as the GUI, so both run from
  one commit.
- `tools\p0_acceptance.py` — one command checks session bootstrap, controller
  health, real job creation, `demo:false`, no demo banner, contract edit,
  approval, start → `queued`, cancel → `已取消`, and that cancel is not labelled
  success. Exit code is non-zero on failure.

```powershell
.\start-controller.ps1          # terminal 1
.\start.ps1                     # terminal 2 (http mode points at :8000)
.\.venv\Scripts\python.exe tools\p0_acceptance.py
```

## Data-source modes

| `BINARYREPAIR_BACKEND` | Adapter | What it proves |
|---|---|---|
| `mock` (default) | `MockBackend` | UI/contract behaviour plus the in-process planner (same seam as `local`); `demo: true`, execution never real |
| `local` | `LocalBackend` | Real Member 1 controller store (`app/services/jobs.py`, SQLite); `demo: false`, execution not implemented yet |
| `http` | `HttpBackend` | GUI talks to a controller over HTTP; `demo` comes from the controller snapshot |

Switching to the real HTTP controller:

```powershell
$env:BINARYREPAIR_BACKEND = "http"
$env:BINARYREPAIR_API_BASE = "http://127.0.0.1:8000"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123
```

`GET /healthz` reports `backend` and `demo` (`null` for `http` until a job is
fetched, because only the controller knows whether its data is synthetic).

## Endpoints the GUI consumes

```
POST /api/jobs                        multipart: binary, defect, valid_tests, environment, valid_domain,
                                      forbidden, invalid_outcome, assumptions
GET  /api/jobs/{id}                   snapshot: id, phase, routes{A,B}, contract, original_sha256, demo
POST /api/jobs/{id}/start             requires an approved current contract
POST /api/jobs/{id}/cancel            cancel
POST /api/jobs/{id}/contract          form: expected_revision, contract fields; 409 on stale revision
POST /api/jobs/{id}/approve           form: expected_sha256, reviewer_label; 409 on stale hash
GET  /api/jobs/{id}/obligations       {"A": [...], "B": [...]}
GET  /api/jobs/{id}/artifacts         {"A": [...], "B": [...]}
GET  /api/artifacts/{id}              artifact metadata by id (id/name/role/sha256/size)
GET  /api/artifacts/{id}/download     registered artifact bytes (attachment)
GET  /api/jobs/{id}/visuals           code-view payload (synthetic demo data today)
```

Snapshot requirements:

- `contract.revision`, `contract.sha256`, `contract.approved` (plus the four
  contract fields) — used for 409 conflict behaviour and the approval badge.
- `routes.A` and `routes.B`, each with `phase`, `outcome` (`null` while running),
  `stages[]`, `tests{passed,total}`, `verification`, `compatibility`,
  `changed_regions[]`, `elapsed_s`, `manual_interventions`, `ai_usage`.
- `demo: false` once the controller runs real jobs; the GUI banner disappears
  automatically and `/api/jobs/{id}` stops being labelled as a fixture.
- All data strings are English ASCII; the GUI scans snapshot/obligations/artifacts
  and rejects non-ASCII payloads with `502` instead of rendering them.

## Final controller (canonical, `app/controller_api.py`)

The canonical controller (controller API + worker + attempt writer + canonical 409
semantics) is `app/controller_api.py`, merged from `main` @4107836. The launcher
serves that module directly:

```powershell
.\start-controller.ps1   # serves app.controller_api:app on 127.0.0.1:8000
```

Do not wire P0 acceptance to the legacy bridge described at the end of this
document; it exists only as a fallback.

Run the worker (independent process, one command):

```powershell
.\start-worker.ps1                     # same default data dir as the controller
# or, explicitly:
python -m app.services.worker --data-dir <project>\var --poll 2
```

At this tag `GET /api/jobs/{id}` returns: `verdict` (run outcome/reason/failing
obligation/detail), `run` (id/phase/manifest/reviewed scope), and
`routes.A/B` with `phase/outcome/outcome_reason/verdict/obligations/stages`.
The `/obligations` endpoint itself still returns static placeholders; the GUI
therefore prefers the per-route `obligations` embedded in the snapshot.

Worker behaviour: it does not execute binaries; it needs one result bundle per
attempt at `<data>/jobs/<job>/runs/<run>/attempts/<attempt>.json` (or an
executor in `BINARYREPAIR_ATTEMPT_CMD`). With bundles it persists V0–V5
obligations and calls `orchestration.evaluate_attempt/evaluate_run`, which write
`outcome/reason/failing_obligation/detail` plus `verdict.json` /
`run-verdict.json`. Without a bundle the run stays at `screening`; no verdict is
invented.

Still needed for a real finished run:

- Route A executor / `jobwrap routea` (M3 + VMer) producing the result bundle.
- Route B/provider executor bundle adapter (M5's `routeb/executor.py` produces a
  static bundle directory; it still has to be mapped to the per-attempt result
  JSON consumed by the worker).
- Same idempotency key with a different contract should return `409`; canonical
  check still pending.

`tools/p2_acceptance.py --worker-command "<python> -m app.services.worker --data-dir <abs> --once"`
runs the worker once after start and then checks `finished`, snapshot
obligations, and the run verdict. Today it exits `2 BLOCKED` (no result bundle),
which is the honest state.
- Verdict write-back is library-only today (`orchestration.evaluate_attempt` /
  `evaluate_run` write `outcome/reason/failing_obligation/detail` and
  `verdict.json` / `run-verdict.json`), but the controller API is not wired and
  obligations have no write path, so a real run cannot produce a verdict yet.
  `tools/p2_acceptance.py` is ready for when this lands (it exits `2 BLOCKED`
  until the pipeline reaches `finished` with real obligations).

Verified P0 run (2026-10-29, merged tree, `app/controller_api.py` in this
checkout): GUI `http` mode → create job → contract edit → approve → start `202` →
snapshot `demo:false`, `phase=queued`, routes `queued`; GUI shows `排队中` and
never claims success; cancel shows `已取消` and is not labelled success.
`python tools/p0_acceptance.py` passes all 17 checks against this pair. `start`
really goes through persisted approval; the missing part is execution, not the
GUI/controller contract.

## Bridge service (legacy fallback)

If a checkout has no `app/controller_api.py`, this tree also ships a bridge backed
by the real job store. It is a development fallback, not the integration target:

```powershell
$env:BINARYREPAIR_CONTROLLER_DATA = "var"   # same store as controller/worker
.\.venv\Scripts\python.exe -m uvicorn tools.controller_service:app --host 127.0.0.1 --port 8000
```

It persists jobs with `app/services/jobs.py` and reports `demo: false`, but its
`start` returns `409` because execution is not implemented there. Use the final
controller above for P0 acceptance.

Switching the GUI (no page changes required):

```powershell
$env:BINARYREPAIR_BACKEND = "http"
$env:BINARYREPAIR_API_BASE = "http://127.0.0.1:8000"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123
```

## P0 acceptance checklist

1. `POST /jobs` with a real sample creates a job in the controller store
   (SQLite row + `var/jobs/<id>/`), not in GUI memory.
2. The comparison page polls `GET /api/jobs/{id}` every 2 seconds and updates
   phase/stage/outcome from the controller response.
3. The snapshot reports `demo: false` and the yellow demo banner is gone.
4. `start` returns `409` until the current contract revision is approved; stale
   revision/hash also returns `409` (never a silent overwrite).
5. `cancel` is visible as `已取消`, not as success.

Current status: P0 passes against the canonical controller in this tree (job
creation, persistence, polling, approval, start → `queued`, `demo: false`, no
demo banner; 17/17 checks in `tools/p0_acceptance.py`, measured 2026-10-29).
Execution itself is still pending in the worker pipeline: the snapshot keeps
placeholder routes/stages at `queued`, obligations report `not_run`, and the
visualizations/artifacts stay empty until workers write them. This is the honest
"queued, not verified" state. `tools/p2_acceptance.py` exits `2 BLOCKED` for the
same reason; it never reports a pass without a finished run and persisted
verdict/obligation evidence.

## P1 honesty rules enforced in tests

- Overall result is derived from both routes (`app/status.py` + `poll.js`):
  success only when **both** outcomes are `success_within_scope`.
- `finished` is a phase, not a verdict; unfinished/running shows `—`.
- `cancelled` shows `已取消`; `error` shows `失败（执行错误）`.
- `not_run` never counts as passed; evidence tables render `not_run` verbatim.
- Contract approval is bound to `revision + sha256`; edits clear approval.

﻿## F5 export v2 (2026-10-29, M2)

- `GET /api/jobs/{id}/export` returns schema v2 with job, contract, per-route
  candidate/outcome/verification, verbatim V0-V5 obligations, artifacts with
  download availability, limitations/not_run, and `manifest_sha256`.
- `GET /jobs/{id}/report` renders the same data as a printable English HTML
  report with a SHA256SUMS block; `?download=1` returns it as an attachment.
- Evidence screen offers JSON download, HTML view and HTML download.
- The export renders controller data only; it does not compute an outcome and
  does not use `not_run` evidence as passed.
