# Documentation

This directory holds the project documentation. Start with the repository
[README](../README.md), then the current status and acceptance record.

## Current status

```text
docs/status/final-status.md
docs/acceptance/ACCEPTANCE.md
docs/acceptance/LIMITATIONS.md
```

## Overview

Product and design background:

```text
docs/overview/product/PRD-BinaryRepair-MVP.md
docs/overview/product/TechDesign-BinaryRepair-MVP.md
docs/overview/product/research-BinaryRepair.md
docs/overview/product/verification-design.md
docs/overview/proposal/
```

## Architecture

```text
docs/architecture/agent-console.md
docs/architecture/controller-integration.md
docs/architecture/attempt-writer-schema.md
docs/architecture/host-importer-broker.md
```

## Operations

```text
docs/operations/run-worker.md
docs/operations/fixtures.md
```

## Reference

```text
docs/interfaces/            browser contract v1 and implementation status
docs/reference/interfaces/
docs/reference/m5/
```

The current frontend/backend handoff and the selected v1 browser contract are in
`docs/handoff/2026-11-17-frontend-delivery.md`, `docs/handoff/2026-11-17-frontend-handoff.md`,
`docs/handoff/2026-11-17-backend-handoff.md` and
`docs/interfaces/frontend-backend-contract-v1.md`.

## Development records

Current handoffs, reviews, analysis and route notes. These are development
records, not a second acceptance authority; use `docs/status/final-status.md`.

```text
docs/development/handoffs/          current records 165-210
docs/development/reviews/           Review 9-12 / Review 12 records
docs/development/analysis/          root-cause and verification analysis
docs/development/routes/            Route A/B notes
docs/development/repository-guide.md
comparison records 193-199; v1.2.0 records 203-210 (upload gate, P1 replay, approval, M4 browser check)
docs/development/repository-layout.md
```

Review provenance: the original Review 9–12 text was not recovered; the verdict
is against a canonical reconstruction, not verbatim original text.

## Assets

```text
docs/showcase/             current UI screenshots (start here)
docs/assets/screenshots/   historical screenshots from earlier UI revisions
docs/assets/demo/          earlier demo screenshots retained for history
```

Use `docs/showcase/` when describing the current workbench. The older
screenshots stay for provenance only; several show the retired form intake.

## Archive

```text
docs/archive/mvp-2026-10-31/evidence/   historical pre-2026-11-08 evidence
docs/archive/updates/2026-11-08.md      earlier curated update note
```

Historical records are retained for provenance. For current acceptance, use
`docs/status/final-status.md` and the canonical artifacts in `artifacts/`.

## Canonical acceptance evidence

```text
artifacts/INDEX.md
artifacts/MANIFEST.md
artifacts/p1-real-window-2026-11-07/
artifacts/p1-routea-v4-symbolic-2026-11-07/
artifacts/p1-routea-real-v4-2026-11-07/
artifacts/p1-routea-v4-input-2026-11-07/
artifacts/v4-harness-2026-11-07/
artifacts/cwe416-124-v4-rerun-2026-11-06/
artifacts/m4/v4-2026-11-07/
artifacts/m4-revalidation-ebc92a8/
artifacts/p1-v5-logs-2026-11-07/
```
