# Interface documents

| File | Purpose |
| --- | --- |
| `frontend-backend-contract-v1.md` | Selected v1 browser contract supplied on 2026-11-17. Authority for wire shapes, states and errors. |
| `frontend-job-list-integration-v1.md` | Session bootstrap and task-collection walkthrough that accompanied the contract. |

## Implementation status, 2026-11-18

The v1 job surface is implemented in `app/api_v1.py` and served by `app/main.py`.
`GET/POST /api/jobs`, contract, approve, start, cancel, plan, snapshot,
evaluation, attempts, comparison, artifacts, deliverables, export, provider
status and session all return the contract shapes when
`X-BinaryRepair-Contract-Version: 1` is present. Requests without the header keep
the pinned baseline wire format.

The browser now uses the contract from two pages: `/ui/chat/` for upload,
contract approval, run control and the running transcript, and `/ui/` for the
read-mostly archive of task state, checks, evidence and export. `/welcome` is the
entry page that links both.

Differences and limits that a reviewer must keep in mind:

* the conversation/SSE contract in
  `docs/handoff/2026-11-17-conversation-ui-design.md` is **not** implemented;
  `/ui/chat/` renders the live job lifecycle, and `?preview=1` keeps the
  labelled synthetic event demo;
* explicit historical `run_id` reads are supported for evaluation, comparison
  and export through `GET /api/jobs/{id}/runs/{run_id}/evaluation` and the
  run-scoped export filter; the archive UI does not yet offer a history selector;
* **deny is a local review decision**, not a backend mutation. The v1 contract has
  no deny endpoint, so the job stays an unapproved draft; `Review again` reopens
  the same approval card and start stays disabled;
* `waiting_for_executor` is a valid state in the contract, but the current
  controller reports `queued` until the worker starts the run; it does not invent
  the more specific state;
* the archive filters run evidence to the selected/current run; original input
  and manual deliverables stay job-level;
* the review binding descriptors for patch policy and verification scope are
  fixed effective values for this build (there is no user-selectable policy), and
  the environment descriptor is the stored job environment;
* artifact access is scoped to the single local session; there is no multi-user
  principal model, and a job-level download cannot be requested by another job id
  because the v1 routes have no job id in the path.
