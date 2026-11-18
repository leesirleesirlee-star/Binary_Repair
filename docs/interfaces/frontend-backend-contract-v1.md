# Frontend–Backend Integration Contract v1

**Status:** Selected v1 design for frontend and backend integration. Implementation and conformance have not been verified against the target baseline.
**Baseline:** Repository B, `leesirleesirlee-star/Binary_Repair`, `v1.2.2` / `b885d21`.
**Provenance:** Based on the 2026-11-17 backend handoff, frontend review feedback and the agreed v1 plan. The pinned baseline was inspected statically; see section 9. This document retains the original draft path so the shared link still works.

## 1. Purpose and ownership

This contract defines the browser API for creating a job, reviewing and approving its contract, starting or cancelling a run, and reading the run's evaluation and evidence. It does not change the execution or acceptance boundaries.

The backend owns validation, durable job and execution state, access to the executor, artifact registration and the acceptance engine. The frontend owns presentation, accessible controls and user-facing messages. Both owners agree on the wire format, examples, error handling and changes to this document. The frontend must not compute an acceptance verdict.

## 2. Architecture and trust

The browser calls the same-origin GUI service. The GUI forwards authorized operations to the controller, which owns persisted state. The browser does not call the controller, worker or guest directly and cannot supply host commands or filesystem paths.

Uploaded or repaired binaries must never execute on the host, and model-generated C must never be compiled there. Parsing, compilation, execution and symbolic analysis belong in the disposable guest under the broker. A missing executor or evidence is an honest waiting or unavailable state. Untrusted worker output must be escaped and rendered as text, never active HTML.

## 3. Shared workflow

1. Establish a session and obtain the CSRF token.
2. Create a job with the binary and description; show whether the data is real or simulated.
3. Review and edit the security contract. Display its revision and approval status.
4. Approve the exact current contract and other inputs bound by the backend approval policy.
5. Start the job; observe execution and per-route evidence. Allow cancellation.
6. Display the acceptance engine's verdict, limitations and available artifacts.

Approval binds the original binary digest, saved contract revision and content digest, test manifest digest, effective execution environment, patch policy and verification scope. Approval and start are separate operations. On a change to any bound input, advance the review revision and invalidate approval atomically with the change, even if the contract text is unchanged. Preserve existing runs and the review binding each run used. On a fresh start, recheck the current approval and binding atomically before creating a run. A successful HTTP response only acknowledges the request.

## 4. Browser API

The new UI sends `X-BinaryRepair-Contract-Version: 1` on every API request in the table. The server selects the v1 wire representation only for that value and echoes the header on v1 responses. An unsupported version returns `400 unsupported_contract_version`; do not fall back silently. API responses on shared routes include `Vary: X-BinaryRepair-Contract-Version` and `Cache-Control: no-store`. The UI must not consume a response without the v1 echo as a v1 response.

Requests without the header retain the pinned baseline's wire behavior wherever that route already exists. Preserve legacy form shapes, redirects and CSRF handling, and verify both header and no-header behavior before rollout. Do not reinterpret a legacy `contract.sha256` or any legacy write field at the same URL. Legacy mutations still share the v1 state and approval invariants; if safe approval cannot be established through a legacy path, fail closed and require review through v1 rather than silently accepting a content-only approval.

| Method | Path | Request and successful response |
| --- | --- | --- |
| GET | `/api/session` | `200 {session: true, csrf_token}`. |
| GET | `/api/assistant/key` | `200 {configured, provider_id}`; read-only, with no key value or key fragment (section 7). |
| GET | `/api/jobs?limit=50` | `200 {jobs: [{id, defect, phase, created_at, outcome}]}`. |
| POST | `/api/jobs` | Multipart fields below; `201 {id, phase}`. |
| GET | `/api/jobs/{id}` | `200` job snapshot (section 4.1). |
| POST | `/api/jobs/{id}/contract` | JSON `expected_revision` and any of `valid_domain`, `forbidden`, `invalid_outcome`, `assumptions`; `200 {revision, sha256, review_binding_digest}`. Omitted fields retain their saved values. |
| POST | `/api/jobs/{id}/approve` | JSON `{expected_sha256, reviewer_label}`; `200 {approved: true, sha256, review_binding_digest, approved_at}`. |
| POST | `/api/jobs/{id}/start` | JSON `{expected_sha256}` and `Idempotency-Key`; `200 {run_id, phase}` (section 6). |
| POST | `/api/jobs/{id}/cancel` | JSON `{run_id}`; `200 {run_id, phase, execution}`. The snapshot remains the source of current status. |
| POST | `/api/jobs/{id}/plan` | JSON object with optional `mode` (`deterministic` or `llm`); `200 {plan, agent}` (section 7.2). |
| GET | `/api/jobs/{id}/attempts` | Keep the handoff attempt list, including each entry's `run_id`; the UI selects only attempts for the current run. |
| GET | `/api/jobs/{id}/evaluation?run_id=…` | `200` run evaluation (section 5). |
| GET | `/api/jobs/{id}/comparison?run_id=…` | `200 {job_id, run_id, status, detail, comparison, artifacts}`. |
| GET | `/api/jobs/{id}/artifacts` | `200 {artifacts}`; array of `Artifact` records for the authorized job only (section 7.1). |
| POST | `/api/jobs/{id}/deliverables` | Multipart `file`; `201` registered artifact metadata. |
| GET | `/api/jobs/{id}/export?run_id=…` | `200` JSON attachment for the selected run, identified by `job_id` and `run_id`. |
| GET | `/api/artifacts/{id}` | `200` authorized artifact metadata. |
| GET | `/api/artifacts/{id}/download` | `200` authorized attachment. |

Initial migration retains `/?token=…` and the HttpOnly session cookie. Where the new UI is served from `/ui`, it uses the same bootstrap middleware; `/ui` is not an existing page at the pinned baseline. Replace the URL-token bootstrap separately. All new JSON and multipart writes require the session cookie and `x-csrf-token`; legacy form routes keep their existing CSRF handling. Do not log session tokens or include them in fixtures.

Job creation requires one `binary` file and a text `defect`, with optional text fields `valid_tests`, `environment`, `valid_domain`, `forbidden`, `invalid_outcome` and `assumptions`. Deliverable upload requires one `file`. Enforce these inclusive limits while receiving an upload, including requests without `Content-Length`: **20 MiB (20,971,520 bytes)** per file, **24 MiB (25,165,824 bytes)** per multipart request body including framing, and **16 KiB (16,384 UTF-8 bytes)** per multipart text field. Oversize returns `413`; an empty file returns `422`. Reject incomplete uploads without registering partial artifacts. A successful upload does not authorize execution.

### 4.1 Reload and saved contract

`GET /api/jobs/{id}` returns `id`, `defect`, `phase`, `demo` (boolean), `data_source`, `contract` and `execution`. For the controller use `demo: false` and `data_source: "controller-http"`; shared fixtures use `demo: true` and `data_source: "synthetic"`. `contract` always contains `valid_domain`, `forbidden` and `invalid_outcome` (strings), `assumptions` (array of strings), `revision` (positive integer), `approved` (boolean), `sha256` (saved contract content digest), `review_binding_digest` (opaque digest of the full versioned binding), and `review_binding` (object below). Digests are strings of 64 lowercase hexadecimal characters. The write API accepts `assumptions` as newline-delimited text. `execution` includes `run_id` (opaque string or `null`), `status`, `terminal` (boolean) and `reason_code` (string or `null`).

`review_binding` has five required keys: `original_sha256` (digest), `test_manifest_sha256` (digest or `null`), and `environment`, `patch_policy`, and `verification_scope` (each `{id, label, summary}` of strings, or `null`). Each descriptor shows the effective selection for that revision in safe display text; it must distinguish choices that would materially change the reviewed coverage or limits. A null value means the selection is missing: show it as such and disable approval and start; the server rejects those operations with `409 invalid_transition`. The UI presents this summary next to the contract text before approval, without calculating digests or verdicts.

On v1 approval and start, send `contract.review_binding_digest` as `expected_sha256`. Never substitute `contract.sha256`. The backend issues and validates the token using the real binding, not the display labels, and ensures it changes whenever the review revision or a bound input changes. `approved_at` is a UTC timestamp string and `reviewer_label` is a nonempty string.

A page reload restores the saved snapshot. Unsaved edits live in the open page only. On a stale conflict fetch the current snapshot for comparison without overwriting the form; the user decides what to keep and reviews the current revision before resubmitting. Do not persist unsaved edits across page reloads in v1.

## 5. States and acceptance

Keep `phase` for stage display and legacy callers. The new UI uses `execution.status` for progress and `execution.terminal` to decide when polling ends. Neither carries an acceptance verdict.

| `execution.status` | `terminal` | Meaning |
| --- | --- | --- |
| `not_started` | `false` | No run; `run_id` is `null`. |
| `queued` | `false` | The run is queued. |
| `waiting_for_executor` | `false` | The executor is unavailable; the run has not begun execution. |
| `running` | `false` | The run is active. |
| `cancelling` | `false` | Cancellation was requested and termination is not yet confirmed. |
| `completed` | `true` | The run ended; read evaluation for the verdict. |
| `failed` | `true` | The run ended in error; display its reason. |
| `cancelled` | `true` | Non-execution or termination was confirmed. |
| `unknown` | `false` | The backend cannot establish a more specific status. |

Use `reason_code: "executor_unavailable"` when waiting for the executor. Poll the snapshot every two seconds while the page is visible and has a nonterminal run. Allow only one in-flight poll. On network failure, retry after 2, 4, 8, 16, then 30 seconds until recovery. Refresh immediately when the page becomes visible or the connection returns; reset backoff after a successful read. Treat an unrecognized status as unknown and never infer completion from it. On `terminal: true`, fetch evaluation for that same run and stop scheduled execution polling. Ignore responses from a previous run or task after switching the active context.

`GET /api/jobs/{id}/evaluation?run_id=…` returns `job_id` (string), `run_id` (string, or `null` for no run), `available`, `finalized` and `demo` (booleans), `data_source` (string), `verdict: {outcome, reason}`, `obligations` and `limitations` (arrays). `verdict.reason` is a nullable machine-readable reason for the verdict; `limitations` is always an array of `{code, detail}` string pairs for display. Show these entries as safe text, not as verdicts. Each obligation contains `route` (`route_a` or `route_b`), `level` (`V0`–`V5`), `status` (`passed`, `failed`, `not_run`, `unknown` or `timed_out`) and `reason_code` (string or `null`). On a versioned request without `run_id`, resolve the latest run, or return `run_id: null` if none exists. An explicit run outside the authorized job or a nonexistent run returns `404`. The new UI always supplies the current run ID and verifies both response IDs.

Execution may become terminal before evaluation commits. With no evaluation record, return `available: false`, `finalized: false`, a null outcome and reason, and empty arrays. An unfinalized record may have `available: true` and recorded obligations or limitations, but its outcome and reason are null. Finalization writes the engine's verdict and its evidence snapshot together; finalized responses have `available: true` and a non-null outcome. If checks cannot be completed, use the engine's actual non-success outcome with limitations. The UI labels a missing or partial evaluation as pending after terminal execution and provides manual retry, including for `200` replies with `finalized: false`. Also offer retry after a read failure or ID mismatch. Do not restart execution polling or display success while evaluation is pending.

`verdict.outcome` is a server acceptance label (`success_within_scope`, `unsupported`, `rejected`, `inconclusive`, `error` or `cancelled`) or `null`. Only the acceptance engine can issue `success_within_scope`; show it as success only for finalized, matching, real evidence. `not_run`, `unknown`, `timed_out`, incomplete checks and missing evidence are distinct from `passed`. Preserve recorded labels and translate them only for display. Do not fabricate a progress percentage or a check result.

### 5.1 Allowed operations and concurrent updates

| Current run | Edit bound inputs or approve | Start with unseen key | Cancel the named current run |
| --- | --- | --- | --- |
| No run | Allowed after revision or binding checks. | Allowed only with current, complete approval; creates `queued`. | No such run: `404`. |
| `queued`, `waiting_for_executor`, `running` | `409 invalid_transition`. | `409 invalid_transition`. | Record `cancelling`, or `cancelled` if non-execution is already confirmed. |
| `cancelling` | `409 invalid_transition`. | `409 invalid_transition`. | Return the existing cancellation state. |
| `unknown` with a run ID | `409 invalid_transition`. | `409 invalid_transition`. | Record or repeat a cancellation request; do not infer termination. |
| `completed`, `failed`, `cancelled` | Allowed after revision or binding checks. | Allowed with current, complete approval and no unconfirmed-termination lock for the job. | `409 invalid_transition` for completed or failed; repeat confirmed cancellation as-is. |

The backend enforces at most one nonterminal run per job across v1, legacy routes and workers. Serialize approval checks, bound edits and new starts at the durable job store; the active-run guard and creation must be atomic. Different keys do not bypass the guard. A replay of an existing start key returns the earlier acknowledgement without applying the matrix or creating a run. A rejected new key is not reserved. A cancel request for a known older run must not mutate the current run; return `409 invalid_transition`. An unknown or unauthorized run ID returns `404`.

`queued` and `waiting_for_executor` may alternate before `running`, or settle directly as `failed` on a confirmed pre-execution failure. Active statuses may enter `cancelling`; `running` may otherwise become `completed` or `failed`. Use `unknown` only as a read projection when the state cannot be established; do not use it to release the active slot. Serialize cancellation and worker completion on the same persisted run state. If completion commits first, cancellation is rejected. If the cancellation request commits first, a later completion message cannot set `completed`; it can inform termination reconciliation, which settles the run as `cancelled` on confirmation or `failed` with `termination_unconfirmed` otherwise. All confirmed terminal execution states are immutable. Late or duplicate worker messages cannot change terminal execution, release a guest lock, add or replace evidence used by the verdict, or change a finalized evaluation. Evaluation may finalize later using only the accepted evidence for that run.

## 6. Errors, retries and cancellation

V1 errors keep the compatibility field `detail` (safe human-readable text) and add `code` (stable machine-readable string) and `correlation_id` (opaque string). Use `401 session_required` for a missing or expired session, `403 request_forbidden` for failed CSRF or origin validation, `404 not_found` for missing or nonvisible resources, `413 payload_too_large` for an upload limit, `422 invalid_input` for invalid input, and `503 capability_unavailable` when a requested capability is unavailable. The frontend maps codes to short localized messages; it does not infer behavior from `detail` or show raw provider diagnostics, including `fallback_reason`.

For `409`, use `stale_revision` for an edit against an old revision, `stale_approval` for a stale review token or missing current approval at start, `invalid_transition` for a lifecycle conflict and `idempotency_key_conflict` for reuse with a different request. The two stale v1 responses include `current: {revision, sha256, review_binding_digest}` with the same meanings as the snapshot. When an operation is blocked by the current run state, return `invalid_transition` before checking for a stale revision or approval. On a stale response, reload saved state without discarding or resubmitting edits.

For a new start, the browser makes a UUID v4 and sends it as `Idempotency-Key`. Keep the key and `expected_sha256` for retries until the start result is known. After session, CSRF and job authorization checks, replay the original `200 {run_id, phase}` acknowledgement for the same key and validated request body, even if the run has finished or the review changed. The acknowledgement is not live status; read the snapshot next. A valid but different request with the same key returns `409 idempotency_key_conflict`. For an unseen key, validate current approval and allowed transition, then create the run and persist the acknowledgement in one transaction. Key scope is the authorized principal and job, retained until task deletion. Concurrent identical starts create one run. Invalid, failed or unauthorized starts do not reserve a key or create a run.

Cancellation applies only to the named, authorized run. Coalesce repeated requests while `cancelling`; repeat a confirmed cancellation by returning its existing state. Reject cancellation of an already completed or failed run with `409 invalid_transition`. Preserve existing evidence. Return `cancelled` only after confirming the run did not start or that execution terminated. If termination cannot be confirmed, report `failed`, `terminal: true`, and `reason_code: "termination_unconfirmed"`; the browser must display that limitation and the guest must not be reused until reset.

## 7. Data, artifacts and credentials

Show comparison, export, deliverable upload and registered artifacts under Evidence. The new UI sends the selected `run_id` on comparison and export; a missing or unauthorized explicit run returns `404 not_found`. Legacy requests that omit it keep their existing behavior. When no comparison is registered for an authorized run, return `200` with matching IDs, `status: "not_provided"`, a safe `detail`, `comparison: null`, and `artifacts: []`. If registered comparison evidence is corrupt or invalid, use `status: "invalid"` and a null comparison; do not present it as absent or valid. Do not substitute another run's evidence. An export may include incomplete or unavailable evaluation, with its actual status and identities intact.

Manual deliverables belong to the job, are distinguishable from run evidence, and never satisfy an acceptance obligation. Artifact routes resolve registered opaque IDs and enforce the owning job's access policy. Unregistered or unauthorized artifacts return `404 not_found`; an authorized record whose bytes are unavailable or fail integrity checks cannot be downloaded (`409 artifact_unavailable`). Downloads use attachment disposition and a safe filename. The browser cannot supply a server path. Show server evidence as inert text. Hashes identify content; they do not prove correctness.

Show a visible demo label for synthetic data. Do not record it as a real verified repair. Shared examples must not include client binaries, confidential derived content, private logs or credentials. Client material stays local unless a specific external transfer is explicitly authorized. The new UI displays only masked provider status and does not offer key entry; provider secrets must not reach the browser, guest, logs, reports or artifacts.

### 7.1 Evidence and provider wire records

Fields listed for each record are required; only fields with an explicit `null` option are nullable. A visible UI can ignore safe additive fields. IDs are opaque strings. Timestamps, where used, are UTC date-time strings. SHA-256 digests use 64 lowercase hexadecimal characters.

| Record | Required fields and types |
| --- | --- |
| `Artifact` | `id`, `job_id`, `name`, `role`, `media_type` (strings); `run_id` (string or `null`); `kind` (`original_input`, `manual_deliverable`, `run_evidence`); `sha256` (digest), `size_bytes` (nonnegative integer), `available` (boolean), `unavailable_reason` (stable string when unavailable, otherwise `null`). `role` is a display label, not proof of eligibility. |
| `Comparison` | `job_id`, `run_id`, `detail` (strings), `status` (`available`, `not_provided` or `invalid`), `comparison` (`{summary: string}` or `null`), and `artifacts` (array of `Artifact`). An `available` comparison requires a non-null summary of recorded comparison evidence for this run, rendered as text. |
| `Export` | `schema_version` (integer `1` for the versioned envelope), `generated_at`, `job_id`, `run_id`, `data_source` (strings), `demo` (boolean), `evaluation` (section 5 record), `comparison` (`Comparison`), and `artifacts` (array of `Artifact`). All nested job and run identities match the export; the artifact list contains job-level records and only the selected run's evidence. An unfinished evaluation stays unfinished in the export. Legacy export format is unchanged. |
| Provider status | `configured` (boolean), `provider_id` (allowlisted public identifier string, or `null` when unavailable or not set). No token value, partial key, provider address or raw diagnostics. |

Use the `Artifact` record for upload acknowledgements, artifact listings and individual metadata. `run_id` is null for `original_input` and `manual_deliverable`, and required for `run_evidence`. Keep the baseline's ELF-only deliverable rule. Only the trusted evidence-ingest path can assign run evidence. The acceptance engine must independently check that its evidence belongs to the authorized job, run and matching hashes; a `run_evidence` label alone is insufficient. A manually uploaded file cannot be relabelled or used to satisfy an obligation. Return exports with `Content-Type: application/json` and attachment disposition.

### 7.2 Plan response and transfer authorization

The v1 plan endpoint defaults to `deterministic` when `mode` is omitted, regardless of provider configuration. A deterministic plan is generated locally and makes no external request. `mode: "llm"` requests a capability, not permission to send data. A planning response is advisory and does not approve a contract, start a run, or supply a verdict.

`200 {plan, agent}` contains these required fields:

| Record | Required fields and types |
| --- | --- |
| `plan` | `plan_id` (string), `mode` (`deterministic` or `llm`, the mode used), `fallback` (boolean), `model` (public model identifier string or `null`), `tokens` (`{prompt, completion}` of nonnegative integers, or `null`), and `steps` (array of `Step`). |
| `agent` | `requested_mode` and `mode` (`deterministic` or `llm`), `fallback` (boolean), `fallback_code` (`llm_unavailable` or `null`), `model` (string or `null`), and `tokens` (same shape as `plan.tokens`). Effective mode, fallback, model and usage agree with `plan`. |
| `Step` | `id`, `title`, `prompt` (strings), `kind` (`upload`, `question` or `review`), and `questions` (array of `Question`, empty where unused). |
| `Question` | `key`, `label` (strings), `input` (`text` or `textarea`), `required` and `ascii` (booleans), and `default` (string or `null`). |

Before an external provider call, the trusted provider boundary verifies a server-side authorization covering the exact job, allowed data and its current revision, purpose, and provider/model destination. For client material, the record must come from an authorized client decision-maker and specify expiry and revocation status. A job reviewer, browser checkbox, provider key or contract approval does not substitute for this authorization. Public fixture use is limited to its existing documented authorization. Check this immediately before sending, including after retries. If the required record is missing, stale, revoked or mismatched, return `403 plan_transfer_not_authorized` and make no external call. V1 does not add an authorization-grant route; until the trusted record can be checked, the external path fails closed.

Send only the minimal job description and reviewed scope labels expressly covered by the authorization. Do not include binary bytes, decompiler output, source, evidence, private logs, credentials or arbitrary request context in v1 plan requests to a provider. If transfer is permitted but the provider is unavailable, or a call fails, use a local deterministic fallback: return `200` with `requested_mode: "llm"`, effective `mode: "deterministic"`, `fallback: true`, `fallback_code: "llm_unavailable"`, and null `model` and `tokens`. Do not retry remotely as part of fallback. For a normally requested deterministic plan use `fallback: false`, null model and tokens, and null `fallback_code`. Do not send raw `fallback_reason` in the v1 response; the UI localizes `fallback_code`.

## 8. Shared examples and acceptance checks

Use [the shared synthetic examples](frontend-backend-contract-v1-examples.json) for backend response checks and frontend rendering checks. Each entry contains a request and expected response. Multipart entries describe fields and file sizes without embedding file content. Snapshots and evaluations are separate responses. Fixtures are not real acceptance evidence.

Joint checks:

1. Create a job, edit and approve its contract, start it, inspect both routes, then retrieve a registered artifact when one exists.
2. Reload the page and recover saved contract fields, review binding, digests, approval and run ID. Verify that changing only the test manifest changes the review digest, advances the revision and invalidates approval without changing the content digest. Check stale conflict fields and preservation of local edits.
3. Make the executor unavailable and verify that the page shows waiting. Display `not_run` for recorded unperformed checks and unavailable for absent evaluation, without a success label in either case.
4. Exercise a failed or incomplete check and both orderings of the Cancel/completion race. Check that edits and approvals are rejected while a run is active, that an older run cannot cancel a newer one, and that late worker updates cannot change a terminal state or finalized verdict.
5. Lose a start response and verify same-key replay returns the same run. Check incompatible key reuse and two different keys starting concurrently: exactly one new run, and `409 invalid_transition` for the other.
6. Verify session/CSRF errors, exact and over-limit uploads, an empty upload, safe artifact access, escaped output and absence of keys from browser responses.
7. Verify that synthetic examples remain visibly labelled and cannot satisfy real acceptance.
8. Verify evaluation belongs to the selected run. Return terminal execution first, then missing or partial evaluation over HTTP 200, then finalized evaluation; require a manual retry and no success before finalization. Also cover transport failure, mismatched IDs and delayed reads after switching runs.
9. Exercise comparison, export and deliverable upload, including absent versus invalid comparison, inaccessible artifacts and deliverables never counting as evidence. Test default local planning, denied transfers without provider calls, authorized minimal payloads with a fake provider and labeled local fallback.
10. In the same HTTP test suite, assert exact baseline response behavior without the version header for shared routes and v1 shapes with it. Include digest semantics, explicit unsupported-version rejection, redirects, cookie/CSRF behavior and no-store/version headers. No synthetic test requires a real provider call or executes a binary.

Real guest verification is a separate integration check under the approved procedure; the sample and browser-contract checks do not verify a repair. Record commands, environment, observed results and any `not_run` items.

## 9. Rollout and change control

### 9.1 Pinned baseline compatibility audit

Inspected repository B `v1.2.2` at `b885d21ee390c3677486aec4ab8b0a0092844bb2`. The annotated tag resolved to that commit via `git ls-remote`; a temporary no-checkout clone resolved the same commit, and `git show HEAD:<path>` read the sources below. These are static findings, not observed HTTP responses.

| Area | Pinned baseline source and behavior | Required compatibility proof |
| --- | --- | --- |
| Browser routes and writes | `app/main.py` has legacy job form POSTs and GET snapshot/evaluation/attempts; no browser `/api/session`, `/api/jobs` collection or JSON task lifecycle/plan routes. `app/controller_api.py` is a separate service; its `/start` declares `202 {id, phase}` and a fixed `http-start-v1` key. | Preserve legacy forms and controller behavior at their boundaries. Exercise the new browser JSON routes, distinct start acknowledgement, and the shared atomic active-run guard. |
| Approval digest | `app/controller_api.py` seeds `contract.sha256` from `_sha_json(body)` in `_contract_body`, but its JSON approval branch writes `approval.scope_sha256` into that metadata field. `app/backend.py` includes those fields in the legacy export. | Test legacy values before and after approval against the pinned behavior. Only versioned responses promise a content digest plus a distinct `review_binding_digest`; v1 writes reject a content-only token. |
| Session and uploads | `app/main.py` redirects a valid query token with `303`, removes it from the URL, and sets `br_session` (`HttpOnly`, `SameSite=Strict`). The legacy job form checks a 20 MiB file limit after reading; deliverable upload takes multipart `file` and uses `x-csrf-token`. | Preserve the existing bootstrap and form CSRF. Test that v1 writes enforce CSRF and exact byte limits, including streamed requests and aggregate limits. |
| Execution and evaluation | The GUI snapshot proxies backend state. The old evaluation in `app/main.py` combines the job snapshot and attempts; `app/services/evaluation.py` builds a job-level scorecard with `not_verdict: true`. The route has no run selector or finalization field. | Keep the legacy scorecard; test versioned run matching, delayed evaluation finalization, and terminal polling behavior. |
| Evidence | `app/main.py` serves job-level comparison with `available`, `not_provided` and `invalid`; export uses the existing JSON attachment (`app/backend.py` `schema_version: 2`). Controller artifact metadata lacks explicit `kind`, `job_id` and `run_id` on the wire. | Preserve old formats. Test the versioned run-bound comparison and export, ownership of artifacts and distinct manual deliverables. |
| Planning and provider status | `app/main.py` has a standalone `/api/assistant/plan`; GET `/api/assistant/key` returns `{key, assistant}`. The browser job-plan endpoint and versioned provider status shape are new. | Preserve legacy shapes without exposing secrets. Test the v1 status projection and planning authorization with a fake provider and no remote send. |

### 9.2 Release check

Use the audited commit as the implementation base, exercise both versions of shared routes in the same HTTP suite, and verify the new frontend against the same samples. Record the implementation SHA, commands, environment and outcomes. Do not call a static source comparison or fixture parse a compatibility or conformance pass. URL-token replacement and a historical-run selector are outside v1.

Measured on Windows: pinned `git rev-parse` and `git show` source assertions passed. PowerShell `ConvertFrom-Json` and fixture assertions passed for 30 synthetic cases, including version headers, review tokens, run identity, cancellation, finalization and denied plan transfer. No application or guest process was run for these checks.

For a wire change, update this contract, the shared examples and affected tests together. Pinned source comparison is complete; runtime compatibility and implementation conformance are `not_run`. Keep real guest validation in a separate record; it is also `not_run`.
