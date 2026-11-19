# Integration follow-up

## Stage 3 real isolated execution (2026-11-19)

The isolated execution protocol is now connected end to end. `tools/guest_bridge.py` packages a run's stored original, its run-bound canonical contract bytes, the staged tests and the per-route candidate inputs; uploads the package over the approved SSH channel; stages it root-owned on the guest; runs the installed executor as uid 1003 under `/usr/local/sbin/jobwrap-executor`; fetches `bundle.json`/`result-bundle.json`/`isolation.json`/`run.log`; and hands the bundle to `tools.route_bundle_ingest.ingest(trigger=False)`, which re-verifies `SHA256SUMS`, bindings and V0 candidate bytes, recomputes V2/V4 on the host, registers artifacts and lets the acceptance engine decide. The worker gained `BINARYREPAIR_EXECUTOR_PROBE_CMD` gating: an unavailable guest records a preflight observation and leaves the run queued instead of claiming it. Exit codes: 0 ingested, 2 environment, 3 package inputs, 4 no bundle, 5 ingest rejected, 75 guest unavailable.

Real guest acceptance on the frozen CWE-416 no-input fixture (real execution, synthetic/parked bytes only on the host): run `57445a29…` timeline `queued → running → completed`; both attempts finished with candidate hashes `2a96b108…` (route_a) and `46d16e99…` (route_b); wrapper rc 0 and attested isolation on both (`uid 1003`, `CapEff=0`, `NoNewPrivs=1`, `seccomp=2`, separate netns); 21/21 and 19/19 bundle sums verified; 20 artifacts and 12 obligations persisted; verdict `inconclusive` / `required_check_not_run` because V4/V5 stay `not_run` — never reported as success. The guest-unavailable case produced `waiting_for_executor`/`executor_unavailable` with the run unclaimed and no bundle or obligation.

Windows verification: 9 new `test_guest_bridge` tests and 43 selected regression tests passed (1 skipped symlink limitation; the sparse checkout needs the `scripts/check_handoff.py` reference copy on PYTHONPATH for the legacy ingest tests). `tests/integration/test_worker.py` has 7 failures from a missing `fixtures/public/CWE121…/original.elf` in the sparse checkout — a pre-existing content gap, not a Stage 3 regression. Package `dist/stage3-isolated-execution-2026-11-19.zip`.

Boundaries: V4/V5 have no producers in this build; candidate generation/provider calls are not wired in this stage (frozen fixture and P1 candidate were staged inputs); the GUI state was verified through the v1 HTTP snapshot and sampled timeline (a rendered browser check is impossible in this sandbox because Edge cannot start — mojo IPC denied). A concurrent V5 replay session restored the VM to `p1-prewindow-20261107` mid-run (receipt in `.tmp/guest-window-2026-11-19/restore-receipt.json`); the clean snapshot predates the P0 Route A landing, so the four executor files were reinstalled afterwards with pre/post hashes and backups at `/var/backups/binrepair-stage3-20261119/` (`evidence/deploy-record.json`). No snapshot was reverted and no VM boundary changed by this work.

## Repair progress (2026-11-19)

The conversation sidebar displays contract review, queue, repair and verdict milestones. It uses the shared theme and respects reduced motion. The verdict comes from the current run's finalized evaluation. Ten synthetic state assertions passed with Node, including stale-run and pending-verdict cases. The four preview asset tests passed. Headless Edge timed out during browser verification, so the rendered appearance and animation still need a visual check.

## Local interface update (2026-11-18)

The welcome page hands an unsent draft to the conversation in the same tab. The desktop conversation gives more space to input before a run, with a compact execution summary and an explicit attachment button. The conversation and archive share navigation order and language selection. The empty archive places the new repair action in the empty state.

The welcome textarea uses the card's focus indicator. The chat composer shares one submission path for Enter and the send button, preserves drafts when first-message validation fails, and restores focus after file selection. Browser checks on port 8125 showed the welcome focus state, the missing-file message in live mode, and both Enter submission and Shift+Enter line breaks in synthetic preview.

The welcome, conversation, and archive pages now share one appearance preference and default to the system mode. New conversations offer the attachment picker as the primary action in the center. Browser checks on port 8125 confirmed that all three pages adopt light and dark settings, and that selecting a harmless test file returns focus to the composer. The file was removed before submission. Four preview asset tests and JavaScript syntax checks passed.

On Windows, `python -m unittest discover -s tests -p test_ui_preview_assets.py` passed four tests. Every UI module passed `node --check`. Browser checks on port 8125 covered draft transfer, disabled send without an attachment, execution detail expansion, archive navigation, the empty-state action, and language persistence. No repair run was started.

Base commit: `85dddac34992dccd9130dc55552e972faf196c0a`.

- The HTTP controller records each v1 start key's review digest in the run transaction. The same key and digest replay the original acknowledgement before current review and lifecycle checks. The browser reuses its key after an uncertain request.
- V1 cancellation resolves the requested run in the controller transaction. An unclaimed queued run can be confirmed cancelled. A claimed run remains cancelling while termination is unconfirmed. Worker claim and verdict writes respect the persisted run state.
- The v1 multipart request limit counts bytes before they reach the parser, including requests without Content-Length. The resulting 413 response retains the v1 headers.

On Windows, 58 selected tests and 42 subtests passed using an isolated test data directory. The selection covered the v1 controller lifecycle, browser API, preview assets, backend contract, security, controller API, synthetic smoke, and orchestration. The JavaScript syntax and browser client retry checks passed. The local GUI on port 8125 and its HTTP controller were restarted, and the versioned task list responded successfully.

## Review transactions (2026-11-19)

Controller-owned review metadata now persists in SQLite. The v1 controller reads and updates the review revision, approval binding, and current run under the same write transaction. History from legacy sidecars stays readable. V1 status and starts require an approval of the full binding. Legacy form shapes remain in use for legacy jobs. Legacy review changes respect the same active run guard, and controller routes cannot start a managed v1 job with a different manifest or revision.

On Windows, the selected pytest run covered `test_v1_controller_lifecycle.py`, `test_ui_v1_api.py`, `test_security.py`, `test_ui_preview_assets.py`, `test_backend_contract.py`, `test_controller_api.py`, `test_e2e_smoke.py`, `test_orchestration.py`, `test_flow_endpoints.py`, and `test_db_v2.py`. It passed 85 tests and 51 subtests. A separate live GUI and controller smoke test with a fresh workspace passed draft creation, edit, approval, start acknowledgement replay, and cancellation before work begins. No repair executor ran during these checks.

The live smoke used its own database. The existing GUI process and its data directory were kept outside the migration test.

## Executor status evidence (2026-11-19)

Migration 011 stores a worker observation for an unclaimed run only when the worker has neither an executor configured nor a staged result. A fresh observation projects `waiting_for_executor` with `executor_unavailable` in the v1 snapshot. Stale observations leave the run queued. The legacy snapshot stays queued and claiming the run clears the observation.

The selected Windows suite passed 87 tests and 51 subtests. An isolated live HTTP check observed the queued-to-waiting transition and successful cancellation before claim. Synthetic regression checks kept an already claimed run in `cancelling` with no finalized outcome after a late completion.

Readiness of a configured external executor and real guest termination still need a trusted broker signal and owner approval. The handoff package includes status observations, a review patch, and the checks for the broker and VM owner. No guest execution or boundary change was performed.

## Stage 2 browser acceptance (2026-11-19)

The stale contract edit follow-up now preserves the local scope draft, refreshes the saved contract after `409 stale_revision`, and shows the new revision and draft together without automatically saving. A synthetic Edge test showed revision 2, the concurrent saved scope, and the local draft together. A Node engine check verified one edit request and the refreshed state. The previously-unexercised explicit re-save is now engine-verified too (`D:/BinRepair/.tmp/stage2-resave-check.mjs`: one stale request then one explicit save against revision 2, draft cleared, revision 3). A later sandboxed session could not re-run Edge (mojo IPC access denied), so the re-save stays engine-verified, not browser-verified. An independent verifier re-ran the recorded checks and its own 4-case falsification script (stale draft preserved with no auto-resubmission, explicit re-save at revision 2, non-`stale_revision` codes on the normal error path, failed-refresh path) and found no defect. The three-module delta is delivered as `dist/stage2-contract-conflict-fix-2026-11-19.zip` (patch + handoff + both checks). See `D:/BinRepair/dist/stage2-contract-conflict-handoff-2026-11-19.md` for the verification boundary.

The Codex in-app browser displayed `Waiting for executor` with repair and verdict pending, and `Cancellation requested` after cancelling a synthetic claimed run without termination evidence. Dropping the first start response after the controller committed the run produced two browser requests with one idempotency key, one run, and two route attempts.

A concurrent contract edit returned `409 stale_revision` and preserved the local draft. The conversation card stayed on revision 1 and showed a generic error while the controller held revision 2. The v1 requirement to refresh saved state while retaining the unsaved edit is still open. The isolated browser run used synthetic jobs and did not run a guest or executor. The report and screenshots are under `dist/browser-acceptance-2026-11-19/` in the enclosing workspace.

## Canonical contract binding (2026-11-19)

The independent handoff finding reproduced in the current integration checkout with a synthetic controller probe. Revision 2 preserved the edited scope in v1 metadata and bound the legacy `unspecified` contract to the run. The start acknowledgement matched the full v1 digest and the run scope matched the stored contract bytes. The two matching digests did not establish that the same scope had been reviewed. The worker and VM were not started.

The follow-up package under `dist/review-binding-followup-2026-11-19/` in the enclosing workspace records the probe, the observed values, and the checks for binding the complete canonical contract. The source of the typed contract and its presentation for approval still need a joint decision. Application code was unchanged during this investigation.

## Stage 3 synthetic result receipt (2026-11-19)

The inbox and directory result importers check the persisted job, run and attempt association before publishing evidence. The receipt uses the same SQLite write lock as cancellation. New results for cancelling, cancelled or finished runs are rejected. Declared binding digests and referenced evidence bytes are checked, and V0 requires registered candidate bytes matching the declared digest. A rejected directory bundle rolls back its new artifact rows and files. A conflicting resubmission preserves existing evidence bytes.

The versioned lifecycle tests passed synthetic late broker messages through the existing GUI and controller HTTP handlers. The corresponding execution snapshots remained cancelling for a claimed run and cancelled for an unclaimed run. Both evaluations remained unfinalized. Node ran the existing chat progress projection with those snapshots and confirmed the corresponding cancellation labels and a pending verdict.

Windows verification used `python -m pytest -o addopts='' -q --basetemp=D:/BinRepair/.tmp/s3bridge-20261119b tests/integration/test_host_broker.py tests/integration/test_route_bundle_ingest.py tests/test_v1_controller_lifecycle.py`. The result was 37 passed and one skipped because this host could not create the test symlink. The sparse checkout omits `scripts/check_handoff.py`, so PYTHONPATH included an isolated copy of the file with the same Git blob hash as HEAD. The executor command was unset. This check used synthetic bytes and no guest. Real configured broker readiness and guest termination remain unverified.

Stage 2 stale-contract browser recovery and canonical worker contract binding remain open under the existing records above.

## Stage 4 run-scoped evidence and reports (2026-11-19)

V1 comparison and export select evidence by the registered job and run. A missing comparison is reported as `not_provided`. Unavailable bytes, a hash mismatch or an invalid comparison document produce `invalid` with no comparison summary. Exports reuse the same scoped artifact selection for their nested comparison and artifact list. Original input and operator deliverables remain job-level records. Unbound evidence receives no inferred run identity.

Windows verification used `python -m pytest -o addopts='' -q --basetemp=D:/BinRepair/.tmp/s4evidence-20261119b tests/test_v1_controller_lifecycle.py tests/test_ui_v1_api.py tests/test_gui_comparison_route.py tests/test_export_bundle.py tests/integration/test_controller_api.py`. All 66 selected tests passed. The new cases used two synthetic runs of one job, a run belonging to a different job, and comparison records with tampered bytes or an invalid format. The checks covered selected-run identity, missing versus invalid evidence, and distinct finalized and pending evaluations. The executor command was unset. A rendered archive-page check and real evidence acceptance are pending.

### V1 artifact download acceptance

The archive artifact button calls the job-scoped v1 download with the version header, verifies the echoed version and attachment filename, and requests a Blob download through the shared helper. A Node client check passed for the request headers, filename and bytes, 409 and 404 handling, missing version echo and unsafe filenames.

An Edge click-through with synthetic text artifacts verified that a same-size integrity mismatch shows the specific artifact error after correcting the workbench mapping. Local HTTP checks returned a 200 attachment with the expected filename and a 409 `artifact_unavailable` for the damaged file. The user confirmed the normal download and provided the saved file. PowerShell `Get-FileHash -Algorithm SHA256` returned `cbc0c586c78edbb037838dd9a07a2256e92b427f37a6f1d21eb0d06449c988fb`, matching the registered fixture digest. Browser download acceptance is approved by the user. Both changed modules passed `node --check`, and all four tests in `test_ui_preview_assets.py` passed after the mapping fix.

Browser API download checks covered valid attachment bytes and a safe filename, missing sessions, unknown artifact IDs, the wrong owning job, missing files and same-size hash mismatches. The job-scoped v1 route delegates ownership checks to the controller. The GUI checks the exact received bytes against the controller's registered digest before producing an attachment. Registered artifacts with missing or mismatched bytes now return `409 artifact_unavailable` under v1. Unknown and wrong-job artifacts return `404 not_found` with the same public detail.

Windows verification used `python -m pytest -o addopts='' -q --basetemp=D:/BinRepair/.tmp/s4download-auth-20261119 tests/test_v1_controller_lifecycle.py tests/test_report_sync.py tests/test_ui_v1_api.py tests/test_gui_comparison_route.py tests/test_export_bundle.py tests/integration/test_controller_api.py tests/test_security.py`. All 89 selected backend tests passed with synthetic files before the archive button was switched to v1.

## Reviewed contract binding fix delivered (2026-11-19, Lead)

Lead decision on the follow-up: a job carries an explicit typed `SecurityContract` (multipart `contract` on v1 create, or `POST /api/jobs/{id}/typed-contract` before review edits); approval rebuilds the canonical contract from the typed source with the four reviewed texts overlaid and persists it in the same transaction. `contracts.sha256`, `runs.reviewed_scope_sha256`, and the SHA-256 of the persisted body bytes are the same value; the review binding digest covers the typed and canonical digests. Approval, `reviewed-start`, legacy `/start`, and `/runs` return `409 invalid_transition` when the scope is not bound; the worker refuses a placeholder or unbound run before any executor spawn with outcome `error`, reason `unbound_review_scope`, and no verdict file. `tools/audit_review_binding.py` reports affected approvals and runs read-only.

The implementation is delivered as `dist/review-binding-fix-2026-11-19/` in the enclosing workspace (patch applies on top of this checkout's executor-status handoff). Verified on Windows with synthetic data: the recorded selection plus six new `tests/test_review_binding.py` regressions passed 93 tests and 51 subtests (87 and 51 before); the positive binding probe reports persisted == reviewed text and `typed_contract_sha256 f3b0d446…34d2`, `canonical/run scope bc9ce6a4…89b6`; the 2026-11-19 defect probe now fails closed at approval. The nine `test_worker`/`test_attempt_writer_gates` failures reproduce on the unmodified baseline (sparse clone lacks `artifacts/`). Guest termination and configured-executor readiness remain not_run; the approval UI still needs a typed-contract picker. Consume the package rather than re-implementing the binding.

## Reviewed contract binding integrated locally (2026-11-19)

The review-binding patch was merged into this checkout while retaining the local comparison and lifecycle changes. Typed input is now validated before job creation and retains the v1 multipart text limit. Job, original artifact registration, and typed metadata commit together. Scope checks verify the canonical bytes and the stored run binding before the worker can claim a run. Rejected creates leave no job or original artifact.

The selected Windows API, controller, lifecycle, and synthetic ingest regression run passed 127 tests and 51 subtests with one host symlink test skipped. The parent checkout supplied the tracked handoff validation script missing from the sparse worktree. The positive binding probe passed and both touched workbench modules passed `node --check`. The executor stayed disabled. Frontend integration for the live conversation is documented in `dist/frontend-review-binding-handoff-2026-11-19.md` in the enclosing workspace.
