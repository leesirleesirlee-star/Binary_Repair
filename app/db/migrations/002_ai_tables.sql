-- v2: AI tables on top of the v1 baseline (app/db/schema.sql).
-- The legacy 001 schema is retired; migrate.py rejects it before this file runs.

ALTER TABLE jobs ADD COLUMN classification TEXT NOT NULL DEFAULT 'public';
ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS contracts (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    revision     INTEGER NOT NULL,
    body_json    TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    approved_at  TEXT NOT NULL,
    reviewer_label TEXT,
    UNIQUE (job_id, revision),
    UNIQUE (job_id, id)
);

CREATE TABLE IF NOT EXISTS runs (
    id                     TEXT PRIMARY KEY,
    job_id                 TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    contract_id            TEXT NOT NULL,
    manifest_sha256        TEXT NOT NULL,
    reviewed_scope_sha256  TEXT NOT NULL,
    idempotency_key        TEXT NOT NULL,
    phase                  TEXT NOT NULL DEFAULT 'queued',
    outcome                TEXT,
    started_at             TEXT NOT NULL,
    finished_at            TEXT,
    UNIQUE (job_id, idempotency_key),
    FOREIGN KEY (job_id, contract_id) REFERENCES contracts(job_id, id)
);

CREATE TABLE IF NOT EXISTS attempts (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    route            TEXT NOT NULL CHECK (route IN ('route_a', 'route_b')),
    sequence         INTEGER NOT NULL,
    phase            TEXT NOT NULL DEFAULT 'queued',
    outcome          TEXT CHECK (outcome IN (
                         'success_within_scope', 'unsupported', 'rejected',
                         'inconclusive', 'error', 'cancelled')),
    reason_code      TEXT,
    candidate_sha256 TEXT,
    started_at       TEXT,
    finished_at      TEXT,
    UNIQUE (run_id, route, sequence)
);

ALTER TABLE artifacts ADD COLUMN attempt_id TEXT REFERENCES attempts(id);
ALTER TABLE artifacts ADD COLUMN media_type TEXT NOT NULL
    DEFAULT 'application/octet-stream';
CREATE UNIQUE INDEX IF NOT EXISTS ux_artifacts_job_path
    ON artifacts(job_id, relative_path);

CREATE TABLE IF NOT EXISTS obligations (
    id                   TEXT PRIMARY KEY,
    attempt_id           TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    kind                 TEXT NOT NULL,
    required             INTEGER NOT NULL CHECK (required IN (0, 1)),
    result               TEXT NOT NULL CHECK (result IN (
                             'passed', 'failed', 'not_run', 'unknown', 'timed_out')),
    evidence_artifact_id TEXT REFERENCES artifacts(id),
    detail               TEXT,
    UNIQUE (attempt_id, kind),
    CHECK (result <> 'passed' OR evidence_artifact_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS events (
    sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    run_id      TEXT REFERENCES runs(id) ON DELETE CASCADE,
    attempt_id  TEXT REFERENCES attempts(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    payload_json TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_calls (
    id                  TEXT PRIMARY KEY,
    attempt_id          TEXT REFERENCES attempts(id) ON DELETE SET NULL,
    route               TEXT,
    model_requested     TEXT NOT NULL,
    model_returned      TEXT,
    status              TEXT NOT NULL,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    total_tokens        INTEGER,
    estimated_cost_usd  REAL CHECK (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0),
    latency_ms          INTEGER,
    request_id          TEXT,
    usage_json          TEXT,
    created_at          TEXT NOT NULL
);
