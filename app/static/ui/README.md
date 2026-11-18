# BinaryRepair web UI

The product has three surfaces, all served by the same local session:

| Surface | Path | Role |
| --- | --- | --- |
| Welcome | `/welcome` | Entry page: choose conversation or archive, switch language |
| Conversation | `/ui/chat/` | Main workspace: attach the binary, describe the defect, review and approve the contract, start, watch, cancel |
| Archive | `/ui/` | Library: task state, checks, verdict, artifacts, export, and the link back into the conversation |

The launcher opens `/welcome?token=...`; the redirect sets the HttpOnly cookie and
removes the token from the address. `/ui/` and `/ui/chat/` then share that cookie.

## Run it

Mock mode, one process:

```powershell
.\start-gui.ps1
```

Controller mode, three processes (the backend handoff has the same commands):

```powershell
# 1 controller
$env:BINARYREPAIR_DATA_DIR="$PWD\var"
python -m uvicorn app.controller_api:app --host 127.0.0.1 --port 8000

# 2 GUI
$env:BINARYREPAIR_BACKEND="http"; $env:BINARYREPAIR_API_BASE="http://127.0.0.1:8000"
$env:BINARYREPAIR_DATA_DIR="$PWD\var"; $env:NO_PROXY="127.0.0.1,localhost"
$env:BINARYREPAIR_SESSION_TOKEN="dev-token-please-change"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8123

# 3 worker (only consumes result bundles; it does not execute binaries)
python -m app.services.worker --data-dir "$PWD\var" --poll 2
```

## Conversation behaviour

* The first turn needs an attached binary and an ASCII defect description.
* `POST /api/jobs` creates the draft; the contract card appears in the transcript.
* Approval uses `contract.review_binding_digest`; a content-only hash is rejected
  by the server as a stale approval. The reviewer name is required.
* Edit reopens the valid-input scope; the server keeps the other saved fields and
  advances the revision.
* Start and cancel are explicit. Polling follows `execution.terminal`; a missing
  or unfinalized evaluation stays pending and is never shown as success.
* The run summary links to the archive for checks, artifacts, export and the
  deliverable upload. Deliverables are not acceptance evidence.
* Every `/api/...` request sends `X-BinaryRepair-Contract-Version: 1`; writes add
  `x-csrf-token` from `GET /api/session`. The CSRF token is kept in memory only.

The conversation page still offers a synthetic design preview at
`/ui/chat/?preview=1`. It uses in-memory events and never contacts the job API.

## Archive behaviour

The archive is a read-mostly library. It lists tasks newest first, shows the
lifecycle phase separately from the acceptance outcome, and opens a task with
`#task/<id>`. A task page links back to the same conversation with
`/ui/chat/?job=<id>`. Contract editing, approval and start are available from the
conversation; the archive focuses on state, checks and evidence.

## Design notes

System font stack, one indigo accent, neutral glass surfaces, visible keyboard
focus, 150 ms or shorter transitions, and solid surfaces under reduced
transparency. Status is always written as text in addition to colour. UI copy
switches between English and Simplified Chinese; submitted data (defect, contract
fields, reviewer label, filenames) stays ASCII because the backend rejects
non-ASCII input.

Tests: `tests/test_ui_v1_api.py` (contract shape, CSRF, review binding, stale
paths, cancellation, planning, welcome/chat/archive routes) and
`tests/test_ui_preview_assets.py` (asset and module resolution).
