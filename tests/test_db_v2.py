from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db.migrate import (
    SCHEMA_PATH,
    DuplicateArtifactsError,
    LegacySchemaError,
    init_db,
)

JOB = {
    "id": "job1",
    "original_sha256": "a" * 64,
    "filename": "original.elf",
    "defect_description": "stack buffer overflow",
    "phase": "draft",
    "created_at": "2026-10-20T00:00:00+00:00",
}


def _job(conn: sqlite3.Connection, job_id: str = "job1") -> None:
    conn.execute(
        "INSERT INTO jobs (id, original_sha256, filename, defect_description, phase, created_at) "
        "VALUES (:id, :original_sha256, :filename, :defect_description, :phase, :created_at)",
        {**JOB, "id": job_id},
    )


def _contract(conn: sqlite3.Connection, job_id: str = "job1", contract_id: str = "c1") -> None:
    conn.execute(
        "INSERT INTO contracts (id, job_id, revision, body_json, sha256, approved_at) "
        "VALUES (?, ?, 1, '{}', ?, '2026-10-20T00:00:00+00:00')",
        (contract_id, job_id, "b" * 64),
    )


def _run(conn: sqlite3.Connection, run_id: str = "r1", key: str = "k1") -> None:
    conn.execute(
        "INSERT INTO runs (id, job_id, contract_id, manifest_sha256, "
        "reviewed_scope_sha256, idempotency_key, started_at) "
        "VALUES (?, 'job1', 'c1', ?, ?, ?, '2026-10-20T00:00:00+00:00')",
        (run_id, "c" * 64, "b" * 64, key),
    )


def test_init_creates_baseline_and_ai_tables(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions == [1, 2, 3]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "artifacts", "contracts", "runs", "attempts",
            "obligations", "events", "model_calls"} <= tables
    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert {"classification", "cancel_requested"} <= job_cols
    artifact_cols = {r[1] for r in conn.execute("PRAGMA table_info(artifacts)")}
    assert {"attempt_id", "media_type"} <= artifact_cols
    conn.close()


def test_reinit_is_idempotent(tmp_path: Path) -> None:
    init_db(tmp_path).close()
    conn = init_db(tmp_path)
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")] == [1, 2, 3]
    conn.close()


def test_idempotency_is_per_job(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    _job(conn, "job1")
    _job(conn, "job2")
    _contract(conn, "job1", "c1")
    _contract(conn, "job2", "c2")
    _run(conn, "r1", "same")
    with pytest.raises(sqlite3.IntegrityError):
        _run(conn, "r2", "same")
    conn.execute(
        "INSERT INTO runs (id, job_id, contract_id, manifest_sha256, "
        "reviewed_scope_sha256, idempotency_key, started_at) "
        "VALUES ('r3', 'job2', 'c2', ?, ?, 'same', '2026-10-20T00:00:00+00:00')",
        ("c" * 64, "b" * 64),
    )
    conn.close()


def test_attempt_uniqueness_and_obligation_vocabulary(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    _job(conn)
    _contract(conn)
    _run(conn)
    conn.execute(
        "INSERT INTO attempts (id, run_id, route, sequence) VALUES ('a1', 'r1', 'route_a', 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO attempts (id, run_id, route, sequence) VALUES ('a2', 'r1', 'route_a', 1)"
        )
    # old vocabulary is rejected
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO obligations (id, attempt_id, kind, required, result) "
            "VALUES ('o1', 'a1', 'behaviour', 1, 'pass')"
        )
    # passed requires evidence
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO obligations (id, attempt_id, kind, required, result) "
            "VALUES ('o2', 'a1', 'behaviour', 1, 'passed')"
        )
    # not_run without evidence is allowed
    conn.execute(
        "INSERT INTO obligations (id, attempt_id, kind, required, result) "
        "VALUES ('o3', 'a1', 'reproducibility', 1, 'not_run')"
    )
    conn.close()


def test_legacy_001_is_rejected_before_ddl(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE jobs (id TEXT PRIMARY KEY, fixture_id TEXT, classification TEXT);
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                                        applied_at TEXT NOT NULL);
        INSERT INTO schema_migrations VALUES (1, 'init', '2026-10-20');
        """
    )
    conn.close()
    with pytest.raises(LegacySchemaError):
        init_db(tmp_path)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "contracts" not in tables
    conn.close()


def test_duplicate_artifacts_fail_and_list_rows(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _job(conn)
    for artifact_id in ("a1", "a2"):
        conn.execute(
            "INSERT INTO artifacts (id, job_id, role, relative_path, sha256, size_bytes, created_at) "
            "VALUES (?, 'job1', 'original', 'same.bin', ?, 1, '2026-10-20')",
            (artifact_id, "d" * 64),
        )
    conn.commit()
    conn.close()
    with pytest.raises(DuplicateArtifactsError) as exc:
        init_db(tmp_path)
    assert exc.value.rows == [("job1", "same.bin", 2)]
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(artifacts)")}
    assert "media_type" not in cols  # 002 must not run after the failure
    conn.close()


def test_artifact_media_type_default(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    _job(conn)
    conn.execute(
        "INSERT INTO artifacts (id, job_id, role, relative_path, sha256, size_bytes, created_at) "
        "VALUES ('a1', 'job1', 'original', 'original.elf', ?, 1, '2026-10-20')",
        ("d" * 64,),
    )
    got = conn.execute("SELECT media_type FROM artifacts WHERE id='a1'").fetchone()[0]
    assert got == "application/octet-stream"
    conn.close()


def test_acceptance_verdict_fields_persist_verbatim(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    _job(conn)
    _contract(conn)
    _run(conn)
    conn.execute(
        "INSERT INTO attempts (id, run_id, route, sequence) VALUES ('a1', 'r1', 'route_a', 1)"
    )
    conn.execute(
        "UPDATE attempts SET outcome='rejected', reason='counterexample', "
        "failing_obligation='behaviour', detail='invalid input still crashes' "
        "WHERE id='a1'"
    )
    conn.execute(
        "UPDATE runs SET outcome='rejected', reason='counterexample', "
        "failing_obligation='behaviour', detail='attempt a1 failed' WHERE id='r1'"
    )
    attempt = conn.execute(
        "SELECT outcome, reason, failing_obligation, detail FROM attempts WHERE id='a1'"
    ).fetchone()
    run = conn.execute(
        "SELECT outcome, reason, failing_obligation, detail FROM runs WHERE id='r1'"
    ).fetchone()
    assert tuple(attempt) == ("rejected", "counterexample", "behaviour", "invalid input still crashes")
    assert tuple(run) == ("rejected", "counterexample", "behaviour", "attempt a1 failed")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(attempts)")}
    assert "reason" in cols and "reason_code" not in cols
    conn.close()
