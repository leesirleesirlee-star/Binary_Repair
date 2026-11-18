# Frontend delivery

The bilingual conversation scaffold is packaged for review against release baseline `v1.2.2` (`b885d21`). It runs on synthetic events. The current application routes and server implementation are outside this delivery.

| Artifact | Location |
| --- | --- |
| Preview start and smoke journey | [Preview guide](../../app/static/ui/README.md) |
| Conversation walkthrough | [Conversation entry page](../../app/static/ui/index.html) |
| Previous task preview | [Task entry page](../../app/static/ui/workbench.html) |
| Chat shell, event rendering, and mock stream | [Chat modules](../../app/static/ui/chat/) |
| Earlier task screens and read adapter | [Workbench modules](../../app/static/ui/workbench/) |
| Common DOM and icon helpers | [Shared modules](../../app/static/ui/shared/) |
| Light and dark marks | [Icon assets](../../app/static/ui/assets/) |
| Proposed SSE event contract | [Conversation design](2026-11-17-conversation-ui-design.md) |
| Seven interaction states | [Conversation wireframes](2026-11-17-conversation-ui-wireframes.md) |

The implementation adds an execution pane beside the conversation on wide screens, with a pane switch on narrow screens. The execution pane uses a spanning scope tile, expandable Route A and Route B cards, and compact activity grouped by phase. Approvals remain in the conversation. The sidebar and step cards respect reduced motion. Model configuration is a preview preference, and live credentials belong to the trusted provider process.

The static preview has no build step. Test asset paths with `python -m unittest discover -s tests -p test_ui_preview_assets.py` from the release checkout. Node syntax and synthetic behavior checks cover the mock stream, approval, cancellation, route notes, and disclosure behavior. Browser rendering remains unverified. The live milestone still requires the agreed SSE and action endpoints, replay and cursor handling, and backend cancellation checks.

## Integration update, 2026-11-18

The product entry is now `/welcome`, served by `app/main.py` behind the existing
session bootstrap. It links to the two workspaces:

* `/ui/chat/` is the main conversation: attach the binary, describe the defect,
  review and approve the contract, start and watch the run. It uses the live
  contract-v1 API; the synthetic design preview remains at `?preview=1`.
* `/ui/` is the archive: task state, checks, verdict, artifacts, export and a
  link back to the same conversation. Contract lifecycle controls live in the
  conversation; the archive keeps the evidence view.

The launchers open `/welcome?token=...`, so the token still bootstraps the
session once and both workspaces share the cookie.

The backend adds the contract-v1 surface in `app/api_v1.py`:

* `GET /api/session`, `GET|POST /api/jobs`, and the JSON task routes for
  contract, approve, start, cancel and plan;
* shared routes (`/api/jobs/{id}`, evaluation, attempts, comparison, artifacts,
  deliverables, export, provider status) return the v1 shape only when
  `X-BinaryRepair-Contract-Version: 1` is present, and keep the baseline shape
  otherwise;
* approval and start consume `contract.review_binding_digest`; the content hash
  alone is rejected as `stale_approval`;
* `POST /api/jobs/{id}/plan` defaults to a local deterministic plan and returns
  `403 plan_transfer_not_authorized` for `mode: "llm"` because no trusted
  transfer record exists;
* the controller gains `GET /api/jobs?limit=50`, honours an
  `Idempotency-Key` on start, and exposes
  `GET /api/jobs/{id}/runs/{run_id}/evaluation` for an authorized historical
  run; comparison and export accept the matching explicit `run_id`;
* deny is a local review decision, not a backend mutation. The v1 contract has
  no deny endpoint; the job stays an unapproved draft, start stays blocked, and
  `Review again` reopens the same approval card. There is no separate backend
  rejection record.

The workbench now creates jobs, saves and approves contracts, starts, polls,
cancels, reads finalized or pending evaluation, downloads artifacts and exports,
and uploads a manual deliverable. Reads use `execution.terminal` to stop polling
and show `not_run` / unknown as distinct non-success states.

Measured on this Windows host on 2026-11-18:

* `tests/test_ui_v1_api.py`: 12 passed (session/version/CSRF, review binding,
  stale revisions, start, cancel, pending evaluation, denied plan transfer,
  legacy no-header shape).
* targeted backend/UI suite: 36 passed
  (`test_ui_v1_api`, `test_ui_preview_assets`, `test_controller_service`,
  `test_security`, `test_backend_contract`, `test_smoke`).
* full suite excluding the three Route A modules that need the missing
  `capstone` package: 826 passed, 13 skipped, 42 subtests passed.
* live controller + GUI + worker run: create -> contract -> approve -> start ->
  two synthetic `not_run` bundles -> worker persisted obligations -> evaluation
  finalized as `inconclusive`; deliverable upload and export returned 200/201.
* headless Edge drove the real UI through create, approve and start with no
  console errors; screenshots captured for review.
* headless Edge also drove `/ui/chat/` through attach -> send -> approve ->
  start (queued) and a second conversation through start -> stop (cancelled),
  with no console errors; the archived run opened from
  `/ui/chat/?job=<id>` and rendered the finalized `inconclusive` summary and the
  `Open archive` link.

Not verified: a real guest/executor run, browser keyboard and screen-reader
walkthroughs at all viewports, SSE conversation streaming, historical-run
selection, and the contract's `waiting_for_executor` projection (the controller
currently reports `queued` until the worker starts).
