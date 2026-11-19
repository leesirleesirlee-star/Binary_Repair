CREATE TABLE IF NOT EXISTS run_executor_preflight (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL
);
