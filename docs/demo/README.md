# BinaryRepair GUI demo (Ubuntu 24.04 / desktop Edge)

This directory contains the one-command launcher, the demo journey and the
honest status of the current HEAD. It does not replace the root `README.md`
(owned by M1).

## Prerequisites

- Ubuntu 24.04
- Python 3.12 (`python3 --version`)
- A Chromium-based browser for the visual walkthrough (Edge/Chromium)

## One-command setup

```bash
chmod +x start.sh
./start.sh
```

Then open <http://127.0.0.1:8123>.

`start.sh` creates `.venv`, installs `requirements.txt`, and runs:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8123
```

The script is marked **unverified on Ubuntu until it is actually run there**.
The Windows equivalent for development is `start.ps1`.

## Mock mode vs HTTP controller mode

### Mock (default, no controller)

```bash
./start.sh
```

Synthetic demo data (`demo: true`); no binary is executed. The page shows a demo
banner and the export contains synthetic artifacts only.

### HTTP controller mode

Start the controller (M1) in a separate terminal from a controller checkout:

```bash
cd ../binaryrepair-controller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
BINARYREPAIR_CONTROLLER_DATA=var/controller \
  python -m uvicorn app.controller_api:app --host 127.0.0.1 --port 8000
```

Then start the GUI against it:

```bash
BINARYREPAIR_BACKEND=http \
BINARYREPAIR_API_BASE=http://127.0.0.1:8000 \
./start.sh
```

Real jobs are persisted with `demo: false`. Until the worker/executor pipeline
delivers result bundles, routes stay `not_run` and the honest state is
`BLOCKED`, not success.

## Demo journey (setup -> compare -> evidence -> export)

1. **Setup / contract** — upload a sample ELF, edit domain/forbidden/invalid
   outcome/assumptions if needed, then approve the current contract revision.
2. **Compare** — start the run; the page polls the controller. Phase and
   verdict come from the controller/acceptance engine; the GUI never computes
   an outcome.
3. **Evidence** — inspect V0-V5 obligations, artifacts and their hashes. Missing
   evidence is shown as `not_run`/`not_available`, never as passed.
4. **Export** — download JSON v2 (`/api/jobs/{id}/export`) and the printable
   HTML report (`/jobs/{id}/report`, also `?download=1`). The JSON contains
   `manifest_sha256`; the report contains a `SHA256SUMS` block.

## Honest status at this HEAD

- P0 GUI/controller contract: **17/17 PASS** (`tools/p0_acceptance.py`); log in
  `p0.log`.
- Browser journeys: **7/7 passed** on Edge (observed 32.14 s); full verbose
  output in `e2e.log`. This is the current desktop result, not a sandbox
  `not_run` placeholder.
- Real pipeline: **BLOCKED (exit 2)** until real Route A/B bundles exist; see
  `tools/p2_acceptance.py`. It is not reported as a pass.
- Gate 4: **NOT RUN**. Route B static evidence is not gate evidence.
- Export/report renders the controller verdict only; `inconclusive` is not
  rewritten into success.

## Screenshots

- `01-landing.png`
- `02-setup.png`
- `03-comparison.png`
- `04-evidence.png`
- `05-report.png`

## Verification logs

- `p0.log` — P0 acceptance output (17/17).
- `e2e.log` — browser journey output; on a host without Edge this must be
  `not_run` with the reason. On the current desktop it is recorded as observed.
