"""Stage 3 guest-bridge tests: packaging, ingest and honest waiting states.

Every test here is host-side and synthetic: no guest is contacted and no binary
is executed. The bridge never executes or compiles anything on the host, so the
tests assert byte handling, package shape, exit codes and store state only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from app.services import worker
from app.db.connection import connect
from tests.support_typed_contract import sample_typed_contract
from tools import guest_bridge


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed(tmp_path: Path, *, route_a_candidate: bool = True) -> dict:
    data = tmp_path / "data"
    job_id = uuid.uuid4().hex
    contract_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    attempts = {"route_a": uuid.uuid4().hex, "route_b": uuid.uuid4().hex}
    job_dir = data / "jobs" / job_id
    execution = job_dir / "execution"
    (execution / "tests").mkdir(parents=True)
    (execution / "route_b").mkdir(parents=True)
    original = job_dir / "original.bin"
    original.write_bytes(b"synthetic original bytes")
    (execution / "tests" / "valid.json").write_text(
        json.dumps({"schema_version": 1, "tests": [{"id": "v", "expected": {}}]}),
        encoding="utf-8",
    )
    (execution / "tests" / "invalid.json").write_text(
        json.dumps({"schema_version": 1, "tests": [{"id": "i", "expected": {}}]}),
        encoding="utf-8",
    )
    (execution / "route_b" / "candidate.elf").write_bytes(b"synthetic route b candidate")
    if route_a_candidate:
        (execution / "route_a").mkdir(parents=True)
        (execution / "route_a" / "candidate.elf").write_bytes(b"synthetic route a candidate")
    from app.domain.contracts import SecurityContract

    contract = SecurityContract.from_dict(sample_typed_contract())
    contract_bytes = contract.canonical_json().encode("utf-8")
    contract_sha = contract.sha256()
    assert hashlib.sha256(contract_bytes).hexdigest() == contract_sha
    original_sha = _sha(original)
    connection = connect(data)
    try:
        with connection:
            connection.execute(
                "INSERT INTO jobs (id, original_sha256, filename, defect_description, phase, created_at) "
                "VALUES (?, ?, 'original.bin', 'synthetic', 'draft', '2026-11-19T00:00:00+00:00')",
                (job_id, original_sha),
            )
            connection.execute(
                "INSERT INTO contracts (id, job_id, revision, body_json, sha256, approved_at, reviewer_label) "
                "VALUES (?, ?, 1, ?, ?, '2026-11-19T00:00:00+00:00', 'synthetic')",
                (contract_id, job_id, contract_bytes.decode("utf-8"), contract_sha),
            )
            connection.execute(
                "INSERT INTO runs (id, job_id, contract_id, manifest_sha256, reviewed_scope_sha256, "
                "idempotency_key, phase, started_at) VALUES (?, ?, ?, ?, ?, 'synthetic-key', 'queued', "
                "'2026-11-19T00:00:00+00:00')",
                (run_id, job_id, contract_id, "0" * 64, contract_sha),
            )
            for route, attempt_id in attempts.items():
                connection.execute(
                    "INSERT INTO attempts (id, run_id, route, sequence, phase) VALUES (?, ?, ?, 1, 'queued')",
                    (attempt_id, run_id, route),
                )
    finally:
        connection.close()
    return {
        "data": data,
        "job_id": job_id,
        "run_id": run_id,
        "attempts": attempts,
        "original_sha256": original_sha,
        "contract_sha256": contract_sha,
    }


def test_build_package_route_a(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    package_root = tmp_path / "package-a"
    package = guest_bridge.build_package(
        data_dir=seeded["data"],
        job_id=seeded["job_id"],
        run_id=seeded["run_id"],
        attempt_id=seeded["attempts"]["route_a"],
        route="route_a",
        package_root=package_root,
    )
    assert package["route"] == "route_a"
    assert package["input_mode"] == "argv"
    assert package["expected"]["original_sha256"] == seeded["original_sha256"]
    assert package["expected"]["contract_sha256"] == seeded["contract_sha256"]
    assert package["expected"]["candidate_sha256"] == _sha(package_root / "candidate.elf")
    assert package["candidate"] == "candidate.elf"
    sums = (package_root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    listed = {line.split(None, 1)[1].lstrip("* ") for line in sums}
    assert {"original.elf", "contract.json", "candidate.elf", "tests/valid.json", "package.json"} <= listed
    assert "SHA256SUMS" not in listed
    for line in sums:
        expected, name = line.split(None, 1)
        assert _sha(package_root / name.lstrip("* ")) == expected


def test_build_package_route_b_uses_contract_target(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    package = guest_bridge.build_package(
        data_dir=seeded["data"],
        job_id=seeded["job_id"],
        run_id=seeded["run_id"],
        attempt_id=seeded["attempts"]["route_b"],
        route="route_b",
        package_root=tmp_path / "package-b",
    )
    assert package["route"] == "route_b"
    assert package["func"] == "target"
    assert package["mode"] == "fixed"
    assert package["run_manifest"] is True
    assert package["expected"]["candidate_sha256"] == _sha(tmp_path / "package-b" / "candidate.elf")


def test_build_package_refuses_missing_route_a_candidate(tmp_path: Path) -> None:
    seeded = _seed(tmp_path, route_a_candidate=False)
    with pytest.raises(guest_bridge.BridgeError) as raised:
        guest_bridge.build_package(
            data_dir=seeded["data"],
            job_id=seeded["job_id"],
            run_id=seeded["run_id"],
            attempt_id=seeded["attempts"]["route_a"],
            route="route_a",
            package_root=tmp_path / "package-a",
        )
    assert raised.value.code == guest_bridge.EXIT_PACKAGE
    assert "candidate" in str(raised.value)


def test_build_package_refuses_missing_tests(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    (seeded["data"] / "jobs" / seeded["job_id"] / "execution" / "tests" / "valid.json").unlink()
    with pytest.raises(guest_bridge.BridgeError) as raised:
        guest_bridge.build_package(
            data_dir=seeded["data"],
            job_id=seeded["job_id"],
            run_id=seeded["run_id"],
            attempt_id=seeded["attempts"]["route_a"],
            route="route_a",
            package_root=tmp_path / "package-a",
        )
    assert raised.value.code == guest_bridge.EXIT_PACKAGE


def _guest_bundle(path: Path, *, candidate: bytes, original: bytes, contract: bytes, tests: bytes) -> Path:
    path.mkdir(parents=True)
    (path / "original.elf").write_bytes(original)
    (path / "contract.json").write_bytes(contract)
    (path / "candidate.elf").write_bytes(candidate)
    (path / "tests").mkdir()
    (path / "tests" / "valid.json").write_bytes(tests)
    manifest = {
        "schema_version": 1,
        "route": "route_a",
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "gates": {
            "V0": {"status": "passed", "artifacts": ["candidate.elf"]},
            "V1": {"status": "not_run"},
            "V2": {"status": "not_run"},
            "V3": {"status": "not_run"},
            "V4": {"status": "not_run"},
            "V5": {"status": "not_run"},
        },
    }
    (path / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    sums = []
    for file in sorted(path.rglob("*")):
        if file.is_file() and file.name != "SHA256SUMS":
            sums.append(f"{_sha(file)}  {file.relative_to(path).as_posix()}")
    (path / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return path


def test_ingest_bundle_registers_evidence_and_result(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    bundle = _guest_bundle(
        tmp_path / "bundle",
        candidate=b"synthetic route a candidate",
        original=(seeded["data"] / "jobs" / seeded["job_id"] / "original.bin").read_bytes(),
        contract=(seeded["data"] / "jobs" / seeded["job_id"] / "execution" / "tests" / "valid.json").read_bytes(),
        tests=(seeded["data"] / "jobs" / seeded["job_id"] / "execution" / "tests" / "valid.json").read_bytes(),
    )
    result = guest_bridge.ingest_bundle(
        data_dir=seeded["data"],
        job_id=seeded["job_id"],
        run_id=seeded["run_id"],
        attempt_id=seeded["attempts"]["route_a"],
        route="route_a",
        local_bundle=bundle,
    )
    assert result["imported"]["changed"] is True
    stored = (
        seeded["data"] / "jobs" / seeded["job_id"] / "runs" / seeded["run_id"]
        / "attempts" / f"{seeded['attempts']['route_a']}.json"
    )
    assert stored.is_file()
    payload = json.loads(stored.read_text(encoding="utf-8"))
    assert payload["candidate_sha256"] == hashlib.sha256(b"synthetic route a candidate").hexdigest()
    assert payload["results"]["V0"] == "passed"
    connection = connect(seeded["data"])
    try:
        row = connection.execute(
            "SELECT count(*) AS n FROM artifacts WHERE job_id = ? AND attempt_id = ?",
            (seeded["job_id"], seeded["attempts"]["route_a"]),
        ).fetchone()
    finally:
        connection.close()
    assert row["n"] >= 1


def test_ingest_bundle_rejects_tampered_candidate(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    bundle = _guest_bundle(
        tmp_path / "bundle",
        candidate=b"tampered candidate bytes",
        original=(seeded["data"] / "jobs" / seeded["job_id"] / "original.bin").read_bytes(),
        contract=(seeded["data"] / "jobs" / seeded["job_id"] / "execution" / "tests" / "valid.json").read_bytes(),
        tests=(seeded["data"] / "jobs" / seeded["job_id"] / "execution" / "tests" / "valid.json").read_bytes(),
    )
    manifest = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    manifest["candidate_sha256"] = "0" * 64
    (bundle / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(guest_bridge.BridgeError) as raised:
        guest_bridge.ingest_bundle(
            data_dir=seeded["data"],
            job_id=seeded["job_id"],
            run_id=seeded["run_id"],
            attempt_id=seeded["attempts"]["route_a"],
            route="route_a",
            local_bundle=bundle,
        )
    assert raised.value.code == guest_bridge.EXIT_INGEST


def test_settings_require_host_and_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BINARYREPAIR_GUEST_SSH_HOST", raising=False)
    monkeypatch.delenv("BINARYREPAIR_GUEST_SSH_KEY", raising=False)
    with pytest.raises(guest_bridge.BridgeError) as raised:
        guest_bridge.settings()
    assert raised.value.code == guest_bridge.EXIT_ENV


def test_probe_command_gates_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    """A failing readiness probe records an observation and leaves the run queued."""

    seeded = _seed(tmp_path)
    monkeypatch.setenv("BINARYREPAIR_ATTEMPT_CMD", "synthetic-executor")
    monkeypatch.setenv(
        "BINARYREPAIR_EXECUTOR_PROBE_CMD", "python -c \"import sys; sys.exit(1)\""
    )
    worker._PROBE_CACHE.clear()
    processed = worker.run_once(data_dir=seeded["data"])
    assert processed == 0
    connection = connect(seeded["data"])
    try:
        run = connection.execute(
            "SELECT phase FROM runs WHERE id = ?", (seeded["run_id"],)
        ).fetchone()
        observation = connection.execute(
            "SELECT count(*) AS n FROM run_executor_preflight WHERE run_id = ?",
            (seeded["run_id"],),
        ).fetchone()
    finally:
        connection.close()
    assert run["phase"] == "queued"
    assert observation["n"] == 1


def test_probe_command_ready_allows_claim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    monkeypatch.setenv("BINARYREPAIR_ATTEMPT_CMD", "synthetic-executor")
    monkeypatch.setenv(
        "BINARYREPAIR_EXECUTOR_PROBE_CMD", "python -c \"import sys; sys.exit(0)\""
    )
    worker._PROBE_CACHE.clear()
    # The synthetic executor does nothing; the worker claims the run and then
    # leaves the attempts queued without evidence (no fabricated result).
    worker.run_once(data_dir=seeded["data"])
    connection = connect(seeded["data"])
    try:
        run = connection.execute(
            "SELECT phase FROM runs WHERE id = ?", (seeded["run_id"],)
        ).fetchone()
        observation = connection.execute(
            "SELECT count(*) AS n FROM run_executor_preflight WHERE run_id = ?",
            (seeded["run_id"],),
        ).fetchone()
    finally:
        connection.close()
    assert run["phase"] != "queued"
    assert observation["n"] == 0
