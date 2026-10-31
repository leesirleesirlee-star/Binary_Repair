# Route A worker contract (proposed) — v1

**Status:** contract proposal for Member 2 (GUI) and Member 1 (job controller).
The Route A worker and `jobwrap routea` are **not implemented yet**; this file
fixes the interface so the frontend does not code against guesses.

## 1. Where the candidate lives

The canonical candidate is on `main` `1826505`:

```text
routea/out/cave-probe.elf   26,792 B
SHA-256 c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82
```

It is **not** in a local `dist/` copy on D:. It is delivered by
`routea-gate3-delivery.bundle` (ref `delivery-main`) and pinned in
`dist/m5-routea-c4a159-2026-10-22.zip` (`957d73d4…`). The local
`ecb9df57…` zip is stale and must not be used.

Fetch:

```bash
git bundle verify <path>/routea-gate3-delivery.bundle
git fetch <path>/routea-gate3-delivery.bundle \
  refs/heads/delivery-main:refs/remotes/bundle/delivery-main
git checkout -B gate3-c4a159 refs/remotes/bundle/delivery-main
sha256sum routea/out/cave-probe.elf    # c4a159…
```

## 2. Worker invocation (proposed)

```bash
/opt/angr-venv/bin/python workers/route_a/worker.py \
  --job-dir   /var/lib/binrepair/jobs/<job_id> \
  --original  original.elf \
  --contract  contract.json \
  --manifest-set manifest-set.json \
  --out       route_a
```

Environment (whitelist; no API keys, no network):

```text
PYTHON=/opt/angr-venv/bin/python
JAVA_HOME=<jdk21>
GHIDRA_HOME=<ghidra>
BINARYREPAIR_DATA_DIR=/var/lib/binrepair
PATH=<shim>:/opt/angr-venv/bin:/usr/local/bin:/usr/bin:/bin
```

The worker runs only inside the disposable Ubuntu guest, through the broker's
fixed operation. The browser never executes binaries and never passes a shell
string.

## 3. Outputs

All paths are relative to `<job_dir>/route_a/`:

```text
candidate.elf          generated candidate
edit-manifest.json     M5 schema 2: hashes, changed/added, header before/after,
                       displaced bytes, detour, writer, termination, tool versions
audit.json             segment mapping, NX/RELRO/canary/W^X, readelf/LIEF summary
readelf-audit.txt      raw readelf -l/-S/-d
route_a.log            command, exit code, tool versions
status.json            worker status (schema below)
```

`status.json` **must not** contain a final outcome. The acceptance engine writes
`decision.json`; the UI renders that decision.

## 4. `status.json` schema v1 (final for M2)

```json
{
  "schema_version": 1,
  "route": "A",
  "job_id": "8f1c2e4a-6b7d-4e90-9a1b-2c3d4e5f6a7b",
  "phase": "generating",
  "reason_code": null,
  "started_at": "2026-10-22T10:00:00Z",
  "finished_at": "2026-10-22T10:01:00Z",
  "original_sha256": "aa42815704d9ffc65b3a49821b3cc6189126a738a7e02d668dec250982408393",
  "contract_sha256": "24bfe568e162be57de013abaeda96060d7edf4cad4b2eca0b003d929d9ec4e37",
  "manifest_set_sha256": "a564f0397a741b1a4fc386a3149d54550c1bfe7c02958309e021fb6f03333a78",
  "candidate": {
    "relative_path": "route_a/candidate.elf",
    "sha256": "c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82",
    "size_bytes": 26792
  },
  "artifacts": [
    {"role": "edit_manifest", "relative_path": "route_a/edit-manifest.json", "sha256": "<64 hex>", "size_bytes": 1234, "media_type": "application/json"},
    {"role": "audit", "relative_path": "route_a/audit.json", "sha256": "<64 hex>", "size_bytes": 2345, "media_type": "application/json"},
    {"role": "readelf", "relative_path": "route_a/readelf-audit.txt", "sha256": "<64 hex>", "size_bytes": 3456, "media_type": "text/plain"},
    {"role": "log", "relative_path": "route_a/route_a.log", "sha256": "<64 hex>", "size_bytes": 4567, "media_type": "text/plain"}
  ],
  "evidence": [
    {"obligation": "structure", "result": "pass", "evidence_path": "route_a/audit.json"},
    {"obligation": "behaviour", "result": "not_run", "evidence_path": null}
  ],
  "tool_versions": {
    "python": "3.11.8",
    "patcherex2": "0.2.14",
    "pyelftools": "0.33",
    "capstone": "5.0.9"
  },
  "manual_interventions": [],
  "warnings": []
}
```

Rules:

- `phase` is one of `draft`, `screening`, `awaiting_review`, `queued`,
  `analyzing`, `generating`, `applying`, `validating`, `roundtrip`, `finished`.
- `evidence[].result` is one of `pass`, `fail`, `not_run`, `inconclusive`,
  `unsupported`. It is not an outcome.
- No `outcome` field. The six outcome labels belong to `decision.json` written
  by the acceptance engine.
- `relative_path` is under `<job_dir>`, forward slashes, no `..`, no absolute
  paths.
- Hashes are 64 lowercase hex; missing values are `null`, never empty strings.
- `reason_code` is typed `snake_case` and may be `null`.
- `candidate` may be `null` when the worker produced no candidate.

## 5. `decision.json` (acceptance engine, not the worker)

```json
{
  "schema_version": 1,
  "job_id": "8f1c2e4a-6b7d-4e90-9a1b-2c3d4e5f6a7b",
  "outcome": "success_within_scope",
  "reason_code": null,
  "bound_hashes": {
    "original_sha256": "aa428157…",
    "contract_sha256": "24bfe568…",
    "manifest_set_sha256": "a564f039…",
    "candidate_sha256": "c4a159…"
  }
}
```

`outcome` is exactly one of `success_within_scope`, `unsupported`, `rejected`,
`inconclusive`, `error`, `cancelled`.

## 6. Ownership (proposed)

| Component | Owner | Notes |
|---|---|---|
| `workers/route_a/worker.py` | Member 3 (algorithm) + Member 1 (service integration) | one code owner must be named; M3 owns patch/audit logic, M1 owns job wiring |
| `jobwrap routea` fixed op | VMer (C wrapper) + M3 (spec) | fixed argv, env whitelist, target hash; no arbitrary paths |
| `status.json` schema | M3 proposes, M1 consumes, M4 approves | this file is the v1 proposal |
| Broker operation list | Member 1 | `analyze/assemble/compile/apply/test/verify`; no shell |

## 7. Current state

- Worker: not implemented.
- `jobwrap`: supports `gate3 original|patched <case-id|all>` and `probe` only;
  `routea` is not implemented.
- Candidate `c4a159…`: exists on main `1826505` / bundle, not in local `dist/`.
- Gate 3: `CONDITIONALLY FEASIBLE`; Gate 4: `NOT RUN`.
