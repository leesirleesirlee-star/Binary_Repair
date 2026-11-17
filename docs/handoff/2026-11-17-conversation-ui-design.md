# BinaryRepair conversation UI — design review 1

Status: Proposed. This is the information architecture, component tree, and event contract for review. The target baseline is BinaryRepair v1.2.2 at `b885d21`. The baseline has job reads, separate evaluation, and scoped history. It does not expose the proposed SSE stream. The revised frontend/backend integration draft remains the authority for proposed job operations and still requires backend agreement.

## Information architecture

| Surface | Visible by default | Available on demand |
| --- | --- | --- |
| Sidebar | New chat, Search, recent conversation titles, one status per active or completed conversation | Settings for English / Simplified Chinese and system / light / dark |
| Conversation | A narrow, chronological transcript with user messages, assistant prose, compact activity rows, and the next required action | Expanded run activity, output, diffs, check evidence, and artifact links |
| Composer | A single growing text field, Send, and an attachment affordance when intake needs a binary | Stop during an active run, and an explicit Queue message action while sending a new turn is blocked |

A conversation can exist before a job is created. Job creation, contract review and approval, and run execution are explicit actions. A user can inspect and edit the proposed contract inline. Approving that revision and starting the run are separate actions. The approval card presents the action, scope, and bound revision or hash together. An edit creates a new review request. A rejection or stale approval never authorizes a start.

During a run, adjacent status events for the same step update one activity row. The row shows a short localized label, its actual state, and a timestamp or elapsed time. Expand it for output and evidence. Plan updates replace the checklist in place. An approval request stays visible until resolved. The end summary shows elapsed time and known counts, links to evidence, and references the matching acceptance verdict if one exists. Collapsed details and grouped output keep the conversation readable as the event history grows.

English and Simplified Chinese are alternative display locales, with one active at a time. The UI localizes labels from stable codes. User text, assistant text, canonical verdicts, and evidence values remain as received. Use system fonts, a neutral palette, one action accent, and monospace only for evidence that benefits from it. Use a heavier sidebar material and a single lightly translucent composer over an otherwise solid reading surface, with solid surfaces when reduced transparency or increased contrast is requested. Press feedback starts immediately. Expanding a row preserves the reader's anchor and never blocks input. The composer and sidebar open instantly from the keyboard. Thinking motion is confined to the thinking indicator, with a static equivalent under reduced motion. These choices apply the skill's hierarchy, familiarity, restraint, and interruptibility to this workflow. [Emil Kowalski's Apple Design skill](https://github.com/emilkowalski/skills/blob/main/skills/apple-design/SKILL.md)

Enter sends, Shift+Enter inserts a newline, and Enter during IME composition commits the composition instead of sending. During a run, ordinary Send is disabled and Queue message remains available. Approval responses use the inline card and do not enter that queue. A queued message shows its server receipt and stays in its original transcript position when it is dispatched. Streaming text is painted in batches per frame. Screen readers receive meaningful state changes and complete replies, with chunk announcements suppressed. Readers who scroll up retain their position and get a jump-to-latest action. Selecting a step anchor loads the corresponding history before revealing it.

## Component tree

```text
ConversationApp
  Sidebar
    NewChatButton
    ConversationSearch
    ConversationList → ConversationItem + RunBadge
    Settings
  ConversationView
    ConversationHeader + ConnectionStatus
    Transcript
      UserMessage + DeliveryStatus
      AssistantMessage + PlainTextOrSafeCodeBlock
      RunActivity
        ThinkingRow
        PlanChecklist
        StepRow → ToolDetails / OutputDetails / FileDiff
        VerificationRow → CheckEvidence
        ErrorRow → RetryStatus
        ApprovalCard → Approve / Deny / Edit
        RunSummary → Evaluation and EvidenceLinks
    JumpToLatest
    Composer → Attachment / Send / Queue / Stop

Conversation store
  Ordered event log → conversation/run/step/message projections
  Localized labels + view preferences
  SSE reader + replay cursor + same-origin action client
```

Keep FastAPI and the offline, build-free frontend baseline. Use TypeScript here to specify the API. The first implementation can use native modules with this contract. SSE fits the one-way event stream, and authenticated HTTP writes carry user actions separately.

## Proposed TypeScript event schema

```ts
type Id = string;
type Timestamp = string; // UTC ISO 8601
type Seq = string;       // Canonical positive decimal integer, compared numerically
type Source = "live" | "synthetic";
type Route = "route_a" | "route_b";
type Level = "V0" | "V1" | "V2" | "V3" | "V4" | "V5";
type CheckStatus = "passed" | "failed" | "not_run" | "unknown";
type StepStatus = "pending" | "in_progress" | "completed" | "failed" | "skipped";
type Outcome = "success_within_scope" | "unsupported" | "rejected" |
  "inconclusive" | "error" | "cancelled";
type RunStatus = "queued" | "running" | "waiting_for_executor" |
  "awaiting_user" | "cancel_requested";
type TerminalExecution = "completed" | "failed" | "cancelled" | "termination_unconfirmed";
type Decision = "approve" | "deny" | "edit";

type TextPreview = { text: string; truncated: boolean; artifact_id?: Id };
type ApprovalBinding = { contract_revision: number; contract_sha256: string };
type ActionBinding = { action_id: Id; scope_sha256: string };
type RunContext = { kind: "assistant_turn"; job_id: Id | null } |
  { kind: "repair"; job_id: Id };

type EventEnvelope<T extends string, P, Run extends Id | null, Step extends Id | null> = {
  version: 1;
  type: T;
  conversation_id: Id;
  seq: Seq;
  run_id: Run;
  step_id: Step;
  ts: Timestamp;
  source: Source;
  payload: P;
};
type ChatEvent<T extends string, P> = EventEnvelope<T, P, Id | null, null>;
type RunEvent<T extends string, P> = EventEnvelope<T, P, Id, null>;
type StepEvent<T extends string, P> = EventEnvelope<T, P, Id, Id>;

type UserRequest =
  | { kind: "question"; request_id: Id; prompt: string }
  | { kind: "contract_review"; request_id: Id; label_code: string;
      binding: ApprovalBinding; allowed: Decision[] }
  | { kind: "action_review"; request_id: Id; label_code: string;
      scope_summary: string; binding: ActionBinding; allowed: Decision[] };
type UserResponse =
  | { kind: "answer"; request_id: Id; text: string }
  | { kind: "decision"; request_id: Id; decision: Decision; binding: ApprovalBinding | ActionBinding };
type Verification =
  | { kind: "check"; route: Route; level: Level; obligation_id: Id;
      status: CheckStatus; evidence_ids: Id[]; reason_code: string | null }
  | { kind: "evaluation"; evaluation_id: Id; generated_at: Timestamp;
      verdict: { outcome: Outcome; reason_code: string | null } | null };

export type ConversationEvent =
  | ChatEvent<"user_message", { message_id: Id; text: string; attachment_ids: Id[];
      delivery: "queued" | "accepted" }>
  | RunEvent<"message_dispatched", { message_id: Id }>
  | RunEvent<"assistant_delta", { message_id: Id; append: string }>
  | RunEvent<"assistant_end", { message_id: Id; status: "complete" | "interrupted" }>
  | RunEvent<"run_state", RunContext & { status: RunStatus; message_id: Id | null }>
  | StepEvent<"thinking", { phase: "started" } |
      { phase: "finished"; duration_ms: number; summary_code: string }>
  | RunEvent<"plan_update", { revision: number; steps: Array<{
      step_id: Id; label_code: string; status: StepStatus }> }>
  | StepEvent<"tool_call", { call_id: Id; tool_code: string;
      label_code: string; input_summary: string }>
  | StepEvent<"tool_result", { call_id: Id; status: "completed" | "failed";
      duration_ms: number; output: TextPreview }>
  | StepEvent<"file_change", { change_id: Id; stage: "proposed" | "applied"; files: Array<{
      display_path: string; added: number; removed: number; diff: TextPreview }> }>
  | StepEvent<"command", { command_id: Id; phase: "started"; label_code: string;
      display_cwd: string } |
      { command_id: Id; phase: "output"; channel: "stdout" | "stderr"; append: string } |
      { command_id: Id; phase: "finished"; exit_code: number | null;
        duration_ms: number; output_artifact_id?: Id }>
  | StepEvent<"verification", Verification>
  | EventEnvelope<"error", { code: string; correlation_id: Id;
      retry: { kind: "none" | "manual" } | { kind: "automatic"; next_at: Timestamp } }, Id, Id | null>
  | StepEvent<"awaiting_user", UserRequest>
  | StepEvent<"user_response", UserResponse>
  | RunEvent<"run_end", RunContext & { execution: TerminalExecution; duration_ms: number;
      steps_taken: number | null; files_changed: number | null;
      ai: { calls: number | null; tokens: number | null; cost_usd: number | null };
      evaluation_id: Id | null; guest_reset_required?: boolean }>;

export type Heartbeat = { version: 1; ts: Timestamp };
```

An assistant turn can run before job approval and can inspect existing evidence. For a repair run, the envelope uses the backend job-run ID and the run context includes its job ID. A repair verdict is tied to that job and run. An action approval binds an identified backend operation to its reviewed scope and cannot grant new permissions. A proposed diff is distinct from an applied change. IDs are opaque. Count and duration values are non-negative and supplied by the server. Each delta appends to its identified message or command stream. `run_id` and `step_id` must match the referenced records. `plan_update` carries the full ordered checklist and a monotonic run-local revision. `thinking` carries only public progress and a summary code. Model reasoning, credentials, private local paths, and raw provider errors are excluded. Tool labels and command events report backend activity. Command labels, display paths, and diffs provide no execution or write capability. Evidence links use registered artifact IDs, and display text is escaped. Only engine-issued evaluation data can provide a verdict. Cancellation requested, confirmed cancellation, and termination unconfirmed have distinct labels.

## Synthetic JSON examples

All examples share one conversation. Independent actions are interleaved. The first JSON block contains one example of every durable event type and examples of the three command phases. This conversation shows an assistant turn reviewing existing evidence. The verdict example stays null because a required check did not run.

```json
[
  {"version":1,"type":"user_message","conversation_id":"demo-1","seq":"1","run_id":null,"step_id":null,"ts":"2026-11-17T08:00:00Z","source":"synthetic","payload":{"message_id":"m-1","text":"Review the repair contract.","attachment_ids":[],"delivery":"accepted"}},
  {"version":1,"type":"run_state","conversation_id":"demo-1","seq":"2","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:01Z","source":"synthetic","payload":{"kind":"assistant_turn","job_id":"demo-job-1","status":"running","message_id":"m-1"}},
  {"version":1,"type":"plan_update","conversation_id":"demo-1","seq":"3","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:02Z","source":"synthetic","payload":{"revision":1,"steps":[{"step_id":"s-review","label_code":"review_contract","status":"in_progress"},{"step_id":"s-check","label_code":"review_verification_evidence","status":"pending"}]}},
  {"version":1,"type":"thinking","conversation_id":"demo-1","seq":"4","run_id":"r-1","step_id":"s-review","ts":"2026-11-17T08:00:03Z","source":"synthetic","payload":{"phase":"started"}},
  {"version":1,"type":"tool_call","conversation_id":"demo-1","seq":"5","run_id":"r-1","step_id":"s-review","ts":"2026-11-17T08:00:04Z","source":"synthetic","payload":{"call_id":"call-1","tool_code":"read_contract_metadata","label_code":"review_contract","input_summary":"Contract revision 2"}},
  {"version":1,"type":"tool_result","conversation_id":"demo-1","seq":"6","run_id":"r-1","step_id":"s-review","ts":"2026-11-17T08:00:05Z","source":"synthetic","payload":{"call_id":"call-1","status":"completed","duration_ms":1000,"output":{"text":"Saved contract revision 2.","truncated":false}}},
  {"version":1,"type":"thinking","conversation_id":"demo-1","seq":"7","run_id":"r-1","step_id":"s-review","ts":"2026-11-17T08:00:06Z","source":"synthetic","payload":{"phase":"finished","duration_ms":3000,"summary_code":"contract_metadata_reviewed"}},
  {"version":1,"type":"assistant_delta","conversation_id":"demo-1","seq":"8","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:07Z","source":"synthetic","payload":{"message_id":"m-2","append":"Contract revision 2 is ready for review."}},
  {"version":1,"type":"assistant_end","conversation_id":"demo-1","seq":"9","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:07Z","source":"synthetic","payload":{"message_id":"m-2","status":"complete"}},
  {"version":1,"type":"awaiting_user","conversation_id":"demo-1","seq":"10","run_id":"r-1","step_id":"s-review","ts":"2026-11-17T08:00:08Z","source":"synthetic","payload":{"kind":"contract_review","request_id":"q-1","label_code":"approve_contract","binding":{"contract_revision":2,"contract_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"allowed":["approve","deny","edit"]}},
  {"version":1,"type":"run_state","conversation_id":"demo-1","seq":"11","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:08Z","source":"synthetic","payload":{"kind":"assistant_turn","job_id":"demo-job-1","status":"awaiting_user","message_id":"m-1"}},
  {"version":1,"type":"user_message","conversation_id":"demo-1","seq":"12","run_id":null,"step_id":null,"ts":"2026-11-17T08:00:09Z","source":"synthetic","payload":{"message_id":"m-3","text":"Summarize the available evidence next.","attachment_ids":[],"delivery":"queued"}},
  {"version":1,"type":"user_response","conversation_id":"demo-1","seq":"13","run_id":"r-1","step_id":"s-review","ts":"2026-11-17T08:00:10Z","source":"synthetic","payload":{"kind":"decision","request_id":"q-1","decision":"approve","binding":{"contract_revision":2,"contract_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}},
  {"version":1,"type":"run_state","conversation_id":"demo-1","seq":"14","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:11Z","source":"synthetic","payload":{"kind":"assistant_turn","job_id":"demo-job-1","status":"running","message_id":"m-1"}},
  {"version":1,"type":"file_change","conversation_id":"demo-1","seq":"15","run_id":"r-1","step_id":"s-review","ts":"2026-11-17T08:00:12Z","source":"synthetic","payload":{"change_id":"change-1","stage":"proposed","files":[{"display_path":"review.txt","added":1,"removed":1,"diff":{"text":"--- a/review.txt\n+++ b/review.txt\n@@ -1 +1 @@\n-Review pending\n+Revision 2 reviewed","truncated":false}}]}},
  {"version":1,"type":"command","conversation_id":"demo-1","seq":"16","run_id":"r-1","step_id":"s-check","ts":"2026-11-17T08:00:13Z","source":"synthetic","payload":{"command_id":"cmd-1","phase":"started","label_code":"inspect_evidence_manifest","display_cwd":"evidence"}},
  {"version":1,"type":"command","conversation_id":"demo-1","seq":"17","run_id":"r-1","step_id":"s-check","ts":"2026-11-17T08:00:14Z","source":"synthetic","payload":{"command_id":"cmd-1","phase":"output","channel":"stdout","append":"No verification bundle found.\n"}},
  {"version":1,"type":"command","conversation_id":"demo-1","seq":"18","run_id":"r-1","step_id":"s-check","ts":"2026-11-17T08:00:15Z","source":"synthetic","payload":{"command_id":"cmd-1","phase":"finished","exit_code":1,"duration_ms":2000}},
  {"version":1,"type":"error","conversation_id":"demo-1","seq":"19","run_id":"r-1","step_id":"s-check","ts":"2026-11-17T08:00:15Z","source":"synthetic","payload":{"code":"no_required_evidence","correlation_id":"demo-request-1","retry":{"kind":"manual"}}},
  {"version":1,"type":"verification","conversation_id":"demo-1","seq":"20","run_id":"r-1","step_id":"s-check","ts":"2026-11-17T08:00:16Z","source":"synthetic","payload":{"kind":"check","route":"route_a","level":"V3","obligation_id":"V3-A","status":"not_run","evidence_ids":[],"reason_code":"required_check_not_run"}},
  {"version":1,"type":"verification","conversation_id":"demo-1","seq":"21","run_id":"r-1","step_id":"s-check","ts":"2026-11-17T08:00:17Z","source":"synthetic","payload":{"kind":"evaluation","evaluation_id":"eval-1","generated_at":"2026-11-17T08:00:17Z","verdict":null}},
  {"version":1,"type":"plan_update","conversation_id":"demo-1","seq":"22","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:17Z","source":"synthetic","payload":{"revision":2,"steps":[{"step_id":"s-review","label_code":"review_contract","status":"completed"},{"step_id":"s-check","label_code":"review_verification_evidence","status":"failed"}]}},
  {"version":1,"type":"run_end","conversation_id":"demo-1","seq":"23","run_id":"r-1","step_id":null,"ts":"2026-11-17T08:00:18Z","source":"synthetic","payload":{"kind":"assistant_turn","job_id":"demo-job-1","execution":"failed","duration_ms":17000,"steps_taken":2,"files_changed":0,"ai":{"calls":null,"tokens":null,"cost_usd":null},"evaluation_id":"eval-1"}},
  {"version":1,"type":"message_dispatched","conversation_id":"demo-1","seq":"24","run_id":"r-2","step_id":null,"ts":"2026-11-17T08:00:19Z","source":"synthetic","payload":{"message_id":"m-3"}},
  {"version":1,"type":"run_state","conversation_id":"demo-1","seq":"25","run_id":"r-2","step_id":null,"ts":"2026-11-17T08:00:19Z","source":"synthetic","payload":{"kind":"assistant_turn","job_id":"demo-job-1","status":"running","message_id":"m-3"}}
]
```

The heartbeat is a separate unsequenced SSE frame. Its data is:

```json
{"version":1,"ts":"2026-11-17T08:00:20Z"}
```

## Transport and replay semantics for review

- **Read.** Propose same-origin `GET /api/conversations/{id}/events` with `Accept: text/event-stream`. One authorized stream carries all persisted events for that conversation. Each durable SSE frame uses `event: conversation_event`, `id: <seq>`, and a JSON event in `data:`. A heartbeat uses `event: heartbeat` without an `id` and is never written to the transcript.
- **Order.** The server appends before broadcasting and makes the replay-to-live handoff gapless. Sequence numbers are contiguous within a conversation, including across runs. Apply only the next expected sequence and deduplicate by conversation ID and sequence. If a later frame arrives first, buffer it briefly and resume from the last contiguous sequence. `step_id` groups UI activity. It does not determine delivery order.
- **Resume.** Reconnect with the last sequence applied, using `Last-Event-ID`. Use a same-origin fetch stream so a reconnect can send the applied cursor explicitly. On reload, resume from zero or from an agreed server checkpoint and replay the tail. If the cursor is expired or invalid, the server returns an explicit recovery response with the available history boundary. A client must never jump a gap and continue with incomplete approval or verdict state.
- **Actions.** Propose separate authenticated, CSRF-protected writes for sending or queueing text and responding to an outstanding question. The contract approval endpoint remains the authority for approving a saved revision. Its resulting durable event records the decision. Reuse the agreed job start and cancel endpoints for a repair run and an agreed idempotency key for retries. The backend validates the current request, contract binding, actor, and state. The frontend never sends raw commands or arbitrary host paths. Lost responses reconcile through the durable event stream.
- **Timing.** Propose a heartbeat every 15 seconds and a connection warning after 30 seconds without any frame. Backoff is bounded. Heartbeats report transport health. The interface uses durable events to report work progress. Stop shows cancellation requested until a server event confirms the termination state. Measure the requested two-second cancellation target on the backend before presenting it as a guarantee.
- **Limits.** The backend owns authorized history, retention, replay consistency, and payload limits. Log events provide bounded text previews and registered artifact links for additional output. The UI never relies on local browser storage to recover sensitive transcript content. The same stream supplies history and live updates, without periodic status requests.

Open backend decisions for the next review include the exact conversation creation and action routes, replay/checkpoint API, heartbeat and retention policy, and mapping from current job and executor enums. The schema above proposes behavior for review and does not authorize implementing or running any execution feature.
