# M3 Route A converter — for M1 review

Files:
- `workers/route_a/attempt_from_bundle.py` — result-bundle.json -> attempt_writer JSON (M1 schema v2).
- `workers/route_a/result_bundle.py` — V0-V5 collector (evidence -> result-bundle.json).
- `workers/route_a/worker.py` — deterministic fixture worker.
- `tests/unit/test_attempt_from_bundle.py` — 2 unit tests, both pass on host.
- `examples/routea-c4a159-attempt-DRYRUN.json` — schema example; **dry-run**, not gate evidence.

Mapping:
  pass -> passed
  fail -> failed
  not_run -> not_run
  inconclusive -> unknown
  unsupported -> unknown
  evidence_files only for V1..V5 with result=passed; V0 never gets evidence.
  outcome/reason/decision are dropped.

Command:
  python workers/route_a/attempt_from_bundle.py \
    --bundle <result-bundle.json> \
    --job-dir <job> --work-dir <work> \
    --out <attempts>/<attempt>.json

Notes:
- Per-attempt JSON is a runtime artifact; do not commit it.
- The current `dist/gate3-bundle-2026-10-22.zip` (0ef65546...) is under G1-G4
  correction; a v2 bundle + new hash is pending. Do not pin the old hash.
- Push is blocked here by SEC_E_NO_CREDENTIALS; this zip is the code handoff.
