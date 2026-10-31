-- v1 baseline for the controller schema: jobs + artifacts only.
-- AI tables (contracts/runs/attempts/obligations/events/model_calls) are created
-- by migrations/002_ai_tables.sql; verdict columns are added by 003.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id                 TEXT PRIMARY KEY,
    original_sha256    TEXT NOT NULL,
    filename           TEXT NOT NULL,
    defect_description TEXT NOT NULL,
    phase              TEXT NOT NULL DEFAULT 'draft',
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    role          TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_job_id ON artifacts(job_id);
