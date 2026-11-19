CREATE TABLE IF NOT EXISTS controller_job_metadata (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL
);
