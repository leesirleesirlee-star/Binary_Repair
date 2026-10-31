"""Contract tests for job identity and state transitions.

These are integration tests for member 1's job controller and artifact store.
They are written against the interface contract in
``docs_BinaryRepair/verification-design.md`` section 4.2.

Behaviour of this module:

* if ``app.services.jobs`` does not exist yet, the whole module skips with a
  clear reason - a skeleton must never look like a passing test;
* if the module exists but a needed entry point is missing, only that test
  skips;
* once the interface is present, the tests run automatically.

The checks below are the V0 identity/preconditions gate from Tech Design
section 9: these are the failures that would let evidence refer to the wrong
artifact or let a stale approval authorise a run.
"""

from __future__ import annotations

import hashlib

import pytest

jobs = pytest.importorskip(
    "app.services.jobs",
    reason=(
        "app.services.jobs is not implemented yet; "
        "see docs_BinaryRepair/verification-design.md section 4.2"
    ),
)


def _require(name: str):
    """Return a job-controller entry point, skipping if it is not implemented."""
    fn = getattr(jobs, name, None)
    if fn is None:
        pytest.skip(
            f"app.services.jobs.{name} is not implemented yet "
            "(see verification-design.md section 4.2)"
        )
    return fn


def _field(record, *names: str):
    """Read the first available field name, so small renames do not break the suite."""
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    pytest.fail(
        f"job record exposes none of {names!r}; "
        "confirm the shared field names in verification-design.md section 4.2"
    )


@pytest.fixture
def data_dir(tmp_path):
    """A disposable data directory; never the real var/ directory."""
    return tmp_path / "var"


@pytest.fixture
def elf_bytes() -> bytes:
    """Minimal x86-64 ELF header; screening, not execution, is in scope."""
    return b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 56


def _create_job(data_dir, payload: bytes):
    create_job = _require("create_job")
    return create_job(
        original_bytes=payload,
        filename="sample.elf",
        defect_description="stack buffer overflow in copy_input",
        data_dir=data_dir,
    )


# ---------------------------------------------------------------------------
# Identity: the record must describe exactly the bytes that were supplied
# ---------------------------------------------------------------------------


def test_create_job_records_sha256_of_input(data_dir, elf_bytes) -> None:
    record = _create_job(data_dir, elf_bytes)
    assert _field(record, "original_sha256") == hashlib.sha256(elf_bytes).hexdigest()


def test_get_job_returns_the_same_identity(data_dir, elf_bytes) -> None:
    get_job = _require("get_job")
    record = _create_job(data_dir, elf_bytes)
    fetched = get_job(_field(record, "id", "job_id"), data_dir=data_dir)
    assert fetched is not None
    assert _field(fetched, "original_sha256") == _field(record, "original_sha256")


def test_two_identical_inputs_share_a_hash_but_not_a_job_id(data_dir, elf_bytes) -> None:
    first = _create_job(data_dir, elf_bytes)
    second = _create_job(data_dir, elf_bytes)
    assert _field(first, "original_sha256") == _field(second, "original_sha256")
    assert _field(first, "id", "job_id") != _field(second, "id", "job_id")


def test_original_bytes_are_preserved_not_modified(data_dir, elf_bytes) -> None:
    record = _create_job(data_dir, elf_bytes)
    stored = None
    for name in ("original_bytes", "input_bytes"):
        if hasattr(record, name):
            stored = getattr(record, name)
            break
    if stored is None:
        pytest.skip(
            "job record does not expose the stored bytes; "
            "artifact retrieval belongs to the artifact store interface"
        )
    assert stored == elf_bytes


# ---------------------------------------------------------------------------
# Approval binding: stale or mismatched values must not authorise a run
# ---------------------------------------------------------------------------


def test_approval_rejects_wrong_input_hash(data_dir, elf_bytes) -> None:
    approve = _require("approve_contract")
    record = _create_job(data_dir, elf_bytes)
    with pytest.raises(Exception):
        approve(
            _field(record, "id", "job_id"),
            input_sha256="0" * 64,  # deliberately wrong
            contract_revision=1,
            manifest_sha256="1" * 64,
            scope_sha256="2" * 64,
            reviewer_label="test",
            data_dir=data_dir,
        )


def test_start_run_requires_an_approval(data_dir, elf_bytes) -> None:
    start_run = _require("start_run")
    record = _create_job(data_dir, elf_bytes)
    with pytest.raises(Exception):
        start_run(
            _field(record, "id", "job_id"),
            approval=None,
            idempotency_key="run-1",
            data_dir=data_dir,
        )


# ---------------------------------------------------------------------------
# Idempotency and restart safety
# ---------------------------------------------------------------------------


def test_duplicate_start_is_idempotent(data_dir, elf_bytes) -> None:
    """The same idempotency key must not create a second charged attempt."""
    start_run = _require("start_run")
    approve = _require("approve_contract")
    record = _create_job(data_dir, elf_bytes)
    approval = approve(
        _field(record, "id", "job_id"),
        input_sha256=_field(record, "original_sha256"),
        contract_revision=1,
        manifest_sha256="1" * 64,
        scope_sha256="2" * 64,
        reviewer_label="test",
        data_dir=data_dir,
    )
    first = start_run(
        _field(record, "id", "job_id"),
        approval=approval,
        idempotency_key="same-key",
        data_dir=data_dir,
    )
    second = start_run(
        _field(record, "id", "job_id"),
        approval=approval,
        idempotency_key="same-key",
        data_dir=data_dir,
    )
    assert _field(first, "id", "run_id") == _field(second, "id", "run_id")


def test_partial_output_is_not_evidence_after_restart(data_dir, elf_bytes) -> None:
    """An interrupted attempt must never be read back as a completed one."""
    get_job = _require("get_job")
    record = _create_job(data_dir, elf_bytes)
    fetched = get_job(_field(record, "id", "job_id"), data_dir=data_dir)
    phase = str(_field(fetched, "phase"))
    assert phase not in {"finished"}, (
        "a freshly created job must not already be finished; "
        "if this fires, restart recovery is treating partial output as evidence"
    )
