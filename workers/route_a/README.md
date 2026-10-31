# Route A worker (implemented, fixture mode) and result bundle

Status:

- `worker.py` — **implemented** for the frozen fixture (deterministic mode).
  It materialises a candidate from a supplied file or a stdlib regenerator and
  writes `route_a/status.json` + artifacts. It does **not** execute the
  candidate and does **not** decide an outcome.
- `result_bundle.py` — **implemented**; collects V0–V5 evidence, enforces
  hashes, writes `result-bundle.json` (+ optional zip). Missing evidence becomes
  `not_run` / `inconclusive`, never `pass`.
- `jobwrap routea` — **not compiled here**: the C wrapper source lives only in
  the guest and is owned by VMer. The required interface/merge points are in
  `JOBWRAP_ROUTEA.md`.

## Worker usage (guest only)

```bash
/opt/angr-venv/bin/python workers/route_a/worker.py \
  --job-dir /var/lib/binrepair/jobs/<job> \
  --original original.elf \
  --contract contract.json \
  --tests-dir tests \
  --out route_a \
  --candidate candidate.elf \
  --expected-candidate c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82 \
  --expected-contract 24bfe568e162be57de013abaeda96060d7edf4cad4b2eca0b003d929d9ec4e37 \
  --edit-manifest edit-manifest.json \
  --audit audit.json \
  --readelf readelf-audit.txt
```

Or `--regen <regen_c4a159.py>` instead of `--candidate`; the output hash must
still match `--expected-candidate`.

## Result bundle usage

```bash
python3 workers/route_a/result_bundle.py \
  --job-dir . --work-dir . \
  --out result-bundle.json --zip result-bundle.zip \
  --image-manifest /var/lib/binrepair/image-chain.txt \
  --isolation isolation.json \
  --formal-run evidence/v3/formal-run.json
```

`formal-run.json` (only a reverted-snapshot run can pass V3):

```json
{
  "reverted": true,
  "candidate_sha256_before": "c4a159…",
  "candidate_sha256_after": "c4a159…",
  "cases": [
    {"id": "valid-empty", "ok": true, "rc": 0, "stdout_hex": "0a", "stderr_hex": ""}
  ]
}
```

`evidence/v1/lief.json`, `evidence/v2/interface.json`,
`evidence/v4/symbolic.json`, `evidence/v5/replay.json` are optional typed
evidence; their absence yields `not_run` / `inconclusive`.

## Dry-run output (host, not formal evidence)

A collector dry run against the M5 c4a159 package produced:

```text
V0 pass
V1 inconclusive  (LIEF/NX/RELRO/canary view missing)
V2 not_run
V3 inconclusive  (GitHub Actions 6/6, not a reverted-snapshot run)
V4 not_run
V5 not_run
```

This is a code-path check, not Gate 3 evidence; it ran on the host (Python
3.15), so its `tool_versions` must not be used in a formal bundle.
