CREATE TABLE IF NOT EXISTS guided_reviews (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    answers_json TEXT NOT NULL,
    setup_json TEXT,
    PRIMARY KEY (job_id, revision)
);
