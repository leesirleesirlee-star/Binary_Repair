#!/usr/bin/env python3
"""Host-side execution channel for one attempt: package, guest dispatch, ingest.

Stage 3 connects the existing execution protocol end to end:

1. build a guest package from the job's stored bytes (original, approved
   canonical contract, staged tests and per-route candidate inputs);
2. hand the package to the isolated guest over the approved SSH channel and run
   the installed executor under ``jobwrap-executor``;
3. fetch the guest result bundle and ingest it through
   :mod:`tools.route_bundle_ingest`, which validates hashes and bindings,
   registers artifacts and hands a result bundle to the worker/acceptance
   engine.

The host never executes or compiles the original binary, a candidate, or
generated C.  This module only copies bytes and reads the guest's evidence.

Environment provided by the worker (see ``app.services.worker``):

    BINARYREPAIR_DATA_DIR, BINARYREPAIR_JOB_ID, BINARYREPAIR_RUN_ID,
    BINARYREPAIR_ATTEMPT_ID, BINARYREPAIR_ROUTE, BINARYREPAIR_RESULT_PATH

Guest channel configuration:

    BINARYREPAIR_GUEST_SSH_HOST   guest address (required; no default)
    BINARYREPAIR_GUEST_SSH_USER   ssh user (default ``lab``)
    BINARYREPAIR_GUEST_SSH_KEY    private key path (required)
    BINARYREPAIR_GUEST_SSH_PORT   optional port
    BINARYREPAIR_GUEST_JOB_ROOT   guest job store (default /var/lib/binrepair/jobs)
    BINARYREPAIR_GUEST_REPO       installed executor repo (default
                                  /opt/binrepair/executor-repo)
    BINARYREPAIR_GUEST_JOBWRAP    jobwrap-executor path
                                  (default /usr/local/sbin/jobwrap-executor)
    BINARYREPAIR_GUEST_TIMEOUT    seconds for the guest execution (default 900)
    BINARYREPAIR_GUEST_FUNC       optional Route B ``func`` override; defaults to
                                  the approved contract's ``target_entry``

Required package inputs live in ``<data>/jobs/<job>/execution/``:

    tests/valid.json              structured valid cases (required)
    tests/invalid.json            required for route_a unless input_mode=none
    route_a/candidate.elf         Route A candidate bytes (required)
    route_a/audit.json            optional Route A static evidence
    route_a/edit-manifest.json    optional Route A static evidence
    route_a/readelf-audit.txt     optional Route A static evidence
    route_a/evidence/             optional Route A typed evidence tree
    route_b/candidate.elf         Route B candidate bytes (candidate mode)
    route_b/replacement.c         Route B C source (compiled in the guest only)

Exit codes:

    0   evidence ingested; the worker will evaluate the attempt
    2   worker environment incomplete
    3   package inputs missing or invalid (nothing was dispatched)
    4   the guest produced no usable bundle (see the run log)
    5   the guest bundle was rejected by the host ingest
    75  guest unavailable (probe/transport failure); the worker keeps waiting

``--probe`` checks the guest channel only and exits 0 (ready) or 75
(unavailable); the worker uses it as ``BINARYREPAIR_EXECUTOR_PROBE_CMD``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_OK = 0
EXIT_ENV = 2
EXIT_PACKAGE = 3
EXIT_NO_EVIDENCE = 4
EXIT_INGEST = 5
EXIT_UNAVAILABLE = 75

ROUTES = ("route_a", "route_b")
SUFFIX = {"route_a": "routea", "route_b": "routeb"}
HEX = re.compile(r"^[0-9a-f]{8,64}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
PROBE_CACHE_SECONDS = 10.0
_probe_cache: tuple[float, bool] | None = None


class BridgeError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        if default is None:
            raise BridgeError(EXIT_ENV, f"{name} is not set")
        return default
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _check_id(kind: str, value: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise BridgeError(EXIT_ENV, f"{kind} must be a lowercase hex identifier")
    return value


def settings() -> dict[str, Any]:
    """Resolve the guest channel configuration from the environment."""

    host = _env("BINARYREPAIR_GUEST_SSH_HOST")
    if not re.fullmatch(r"[A-Za-z0-9.:_-]+", host):
        raise BridgeError(EXIT_ENV, f"invalid guest host: {host!r}")
    key = Path(_env("BINARYREPAIR_GUEST_SSH_KEY"))
    if not key.is_file():
        raise BridgeError(EXIT_ENV, f"guest ssh key is missing: {key}")
    return {
        "host": host,
        "user": _env("BINARYREPAIR_GUEST_SSH_USER", "lab"),
        "key": str(key),
        "port": _env("BINARYREPAIR_GUEST_SSH_PORT", ""),
        "job_root": _env("BINARYREPAIR_GUEST_JOB_ROOT", "/var/lib/binrepair/jobs"),
        "repo": _env("BINARYREPAIR_GUEST_REPO", "/opt/binrepair/executor-repo"),
        "jobwrap": _env(
            "BINARYREPAIR_GUEST_JOBWRAP", "/usr/local/sbin/jobwrap-executor"
        ),
        "timeout": float(_env("BINARYREPAIR_GUEST_TIMEOUT", "900")),
    }


def _ssh_base(cfg: dict[str, Any]) -> list[str]:
    argv = [
        shutil.which("ssh") or "ssh",
        "-i",
        cfg["key"],
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=8",
    ]
    if cfg["port"]:
        argv += ["-p", str(cfg["port"])]
    argv += [f"{cfg['user']}@{cfg['host']}"]
    return argv


def _scp_base(cfg: dict[str, Any]) -> list[str]:
    argv = [
        shutil.which("scp") or "scp",
        "-i",
        cfg["key"],
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=8",
    ]
    if cfg["port"]:
        argv += ["-P", str(cfg["port"])]
    return argv


def _run(
    argv: list[str],
    *,
    timeout: float,
    input_text: str | None = None,
    what: str,
) -> subprocess.CompletedProcess[str]:
    # Send stdin as bytes: with ``text=True`` Windows would translate ``\n`` to
    # ``\r\n`` and the guest shell would see CR characters in the script.
    payload = input_text.encode("utf-8") if input_text is not None else None
    try:
        completed = subprocess.run(
            argv,
            input=payload,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(EXIT_UNAVAILABLE, f"{what} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise BridgeError(EXIT_UNAVAILABLE, f"{what} could not start: {exc}") from exc
    return subprocess.CompletedProcess(
        argv,
        completed.returncode,
        stdout=(completed.stdout or b"").decode("utf-8", "replace"),
        stderr=(completed.stderr or b"").decode("utf-8", "replace"),
    )


def probe(cfg: dict[str, Any], *, use_cache: bool = True) -> bool:
    """Return True when the guest channel and executor are ready."""

    global _probe_cache
    now = time.monotonic()
    if use_cache and _probe_cache is not None:
        stamp, ready = _probe_cache
        if now - stamp < PROBE_CACHE_SECONDS:
            return ready
    script = (
        f"test -x {cfg['jobwrap']} && test -d {cfg['repo']} && "
        f"test -d {cfg['job_root']} && sudo -n true && echo GUEST_READY"
    )
    try:
        completed = _run(
            _ssh_base(cfg) + ["bash", "-s"],
            input_text=script + "\n",
            timeout=30,
            what="guest readiness probe",
        )
    except BridgeError:
        ready = False
    else:
        ready = completed.returncode == 0 and "GUEST_READY" in completed.stdout
    _probe_cache = (now, ready)
    return ready


def _load_contract(data_dir: Path, job_id: str, run_id: str) -> tuple[bytes, dict[str, Any]]:
    from app.db.connection import connect

    connection = connect(data_dir)
    try:
        row = connection.execute(
            "SELECT c.body_json FROM contracts AS c JOIN runs AS r ON c.id = r.contract_id "
            "AND c.job_id = r.job_id WHERE r.id = ? AND r.job_id = ?",
            (run_id, job_id),
        ).fetchone()
    finally:
        connection.close()
    if row is None or not isinstance(row["body_json"], str):
        raise BridgeError(EXIT_PACKAGE, "the run has no approved canonical contract")
    body = row["body_json"].encode("utf-8")
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise BridgeError(EXIT_PACKAGE, f"approved contract is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BridgeError(EXIT_PACKAGE, "approved contract must be a JSON object")
    return body, parsed


def _stage(data_dir: Path, job_id: str) -> Path:
    staged = data_dir / "jobs" / job_id / "execution"
    if not staged.is_dir():
        raise BridgeError(
            EXIT_PACKAGE,
            f"no execution inputs staged for job {job_id}: {staged}",
        )
    return staged


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True)


def build_package(
    *,
    data_dir: Path,
    job_id: str,
    run_id: str,
    attempt_id: str,
    route: str,
    package_root: Path,
) -> dict[str, Any]:
    """Write one guest package from stored bytes and staged inputs."""

    from app.db.connection import connect

    if route not in ROUTES:
        raise BridgeError(EXIT_ENV, f"unknown attempt route: {route!r}")
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)

    connection = connect(data_dir)
    try:
        job = connection.execute(
            "SELECT original_sha256 FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    finally:
        connection.close()
    if job is None:
        raise BridgeError(EXIT_PACKAGE, f"unknown job id: {job_id}")
    original = data_dir / "jobs" / job_id / "original.bin"
    if not original.is_file():
        raise BridgeError(EXIT_PACKAGE, f"stored original is missing: {original}")
    original_sha = _sha256(original)
    if original_sha != job["original_sha256"]:
        raise BridgeError(EXIT_PACKAGE, "stored original bytes do not match the job record")

    contract_bytes, contract = _load_contract(data_dir, job_id, run_id)
    contract_sha = _sha256_bytes(contract_bytes)
    staged = _stage(data_dir, job_id)

    tests_source = staged / "tests"
    valid = tests_source / "valid.json"
    if not valid.is_file():
        raise BridgeError(EXIT_PACKAGE, f"execution tests are missing: {valid}")
    input_mode = str(contract.get("input_mode") or "argv")
    if route == "route_a" and input_mode != "none" and not (tests_source / "invalid.json").is_file():
        raise BridgeError(
            EXIT_PACKAGE,
            "route_a needs tests/invalid.json unless the contract input_mode is 'none'",
        )

    shutil.copyfile(original, package_root / "original.elf")
    (package_root / "contract.json").write_bytes(contract_bytes)
    _copy_tree(tests_source, package_root / "tests")

    route_dir = staged / route
    package: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job_id,
        "route": route,
        "purpose": "repair",
        "input_mode": input_mode,
        "original": "original.elf",
        "contract": "contract.json",
        "tests": "tests",
    }
    expected = {
        "original_sha256": original_sha,
        "contract_sha256": contract_sha,
    }

    if route == "route_a":
        candidate = route_dir / "candidate.elf"
        if not candidate.is_file():
            raise BridgeError(EXIT_PACKAGE, f"Route A candidate is missing: {candidate}")
        shutil.copyfile(candidate, package_root / "candidate.elf")
        candidate_sha = _sha256(candidate)
        expected["candidate_sha256"] = candidate_sha
        package["candidate"] = "candidate.elf"
        for filename, key in (
            ("audit.json", "audit"),
            ("edit-manifest.json", "edit_manifest"),
            ("readelf-audit.txt", "readelf"),
        ):
            source = route_dir / filename
            if source.is_file():
                shutil.copyfile(source, package_root / filename)
                package[key] = filename
        evidence = route_dir / "evidence"
        if evidence.is_dir():
            _copy_tree(evidence, package_root / "evidence")
    else:
        candidate = route_dir / "candidate.elf"
        c_source = route_dir / "replacement.c"
        if candidate.is_file():
            shutil.copyfile(candidate, package_root / "candidate.elf")
            expected["candidate_sha256"] = _sha256(candidate)
            package["candidate"] = "candidate.elf"
        elif c_source.is_file():
            shutil.copyfile(c_source, package_root / "replacement.c")
            package["c_source"] = "replacement.c"
        else:
            raise BridgeError(
                EXIT_PACKAGE,
                f"Route B needs a staged candidate.elf or replacement.c under {route_dir}",
            )
        func = os.environ.get("BINARYREPAIR_GUEST_FUNC") or contract.get("target_entry")
        if not isinstance(func, str) or not func:
            raise BridgeError(
                EXIT_PACKAGE,
                "Route B needs a target function: contract.target_entry or BINARYREPAIR_GUEST_FUNC",
            )
        package["func"] = func
        package["mode"] = "fixed"
        package["run_manifest"] = True

    package["expected"] = expected
    _write_json(package_root / "package.json", package)

    sums = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        rel = path.relative_to(package_root).as_posix()
        sums.append(f"{_sha256(path)}  {rel}")
    (package_root / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return package


def _guest_run_script(
    *,
    cfg: dict[str, Any],
    package_json: dict[str, Any],
    job_id: str,
    run_id: str,
    attempt_id: str,
    remote_pkg: str,
    remote_out: str,
) -> str:
    suffix = SUFFIX[package_json["route"]]
    out_dir = f"{cfg['job_root']}/{job_id}/runs/{run_id}/attempts/{attempt_id}.{suffix}"
    return f"""set -u
JOB={cfg['job_root']}/{job_id}
OUT={out_dir}
PKG={remote_pkg}
sudo -n install -d -o root -g root -m 0755 "$JOB" "$JOB/staging" "$JOB/runs" "$JOB/runs/{run_id}" "$JOB/runs/{run_id}/attempts"
sudo -n rm -rf "$JOB/staging/{attempt_id}"
sudo -n cp -a "$PKG" "$JOB/staging/{attempt_id}"
sudo -n chown -R root:root "$JOB/staging/{attempt_id}"
sudo -n chmod -R go-w "$JOB/staging/{attempt_id}"
sudo -n find "$JOB/staging/{attempt_id}" -type d -exec chmod 0755 {{}} +
sudo -n find "$JOB/staging/{attempt_id}" -type f -exec chmod 0644 {{}} +
# The Route B manifest runner executes the staged candidate in place; the
# executor account needs the execute bit while group/other stay read-only.
sudo -n chmod 0755 "$JOB/staging/{attempt_id}/candidate.elf" 2>/dev/null || true
( cd "$JOB/staging/{attempt_id}" && sha256sum -c SHA256SUMS )
echo "PACKAGE_SUMS_RC=$?"
sudo -n rm -rf "$OUT"
{cfg['jobwrap']} executor {cfg['repo']} "$JOB/staging/{attempt_id}" "$OUT"
echo "WRAPPER_RC=$?"
sudo -n rm -rf {remote_out}
sudo -n cp -a "$OUT" {remote_out} 2>/dev/null || true
sudo -n chown -R {cfg['user']}:{cfg['user']} {remote_out} 2>/dev/null || true
test -f {remote_out}/bundle.json && echo "BUNDLE_READY=yes" || echo "BUNDLE_READY=no"
"""


def dispatch(
    *,
    cfg: dict[str, Any],
    package_json: dict[str, Any],
    package_root: Path,
    job_id: str,
    run_id: str,
    attempt_id: str,
    local_bundle: Path,
) -> str:
    """Upload, execute under jobwrap, fetch the bundle. Returns the guest log."""

    tag = f"br-{job_id[:16]}-{attempt_id[:16]}"
    remote_pkg = f"/tmp/{tag}-pkg"
    remote_out = f"/tmp/{tag}-out"

    _run(
        _scp_base(cfg) + ["-q", "-r", str(package_root), f"{cfg['user']}@{cfg['host']}:{remote_pkg}"],
        timeout=120,
        what="package upload",
    )
    script = _guest_run_script(
        cfg=cfg,
        package_json=package_json,
        job_id=job_id,
        run_id=run_id,
        attempt_id=attempt_id,
        remote_pkg=remote_pkg,
        remote_out=remote_out,
    )
    try:
        completed = _run(
            _ssh_base(cfg) + ["bash", "-s"],
            input_text=script,
            timeout=cfg["timeout"],
            what="guest execution",
        )
    finally:
        pass
    log = (completed.stdout or "") + (completed.stderr or "")
    match = re.search(r"^WRAPPER_RC=(\d+)$", completed.stdout or "", re.MULTILINE)
    wrapper_rc = int(match.group(1)) if match else None
    if completed.returncode != 0 or wrapper_rc != 0 or "BUNDLE_READY=yes" not in (completed.stdout or ""):
        _cleanup_remote(cfg, remote_pkg, remote_out)
        detail = log.strip().splitlines()[-8:]
        raise BridgeError(
            EXIT_NO_EVIDENCE,
            "the guest did not produce a result bundle"
            + (f" (wrapper rc {wrapper_rc})" if wrapper_rc is not None else "")
            + (": " + " | ".join(detail) if detail else ""),
        )

    if local_bundle.exists():
        shutil.rmtree(local_bundle)
    local_bundle.parent.mkdir(parents=True, exist_ok=True)
    _run(
        _scp_base(cfg) + ["-q", "-r", f"{cfg['user']}@{cfg['host']}:{remote_out}", str(local_bundle)],
        timeout=180,
        what="bundle download",
    )
    _cleanup_remote(cfg, remote_pkg, remote_out)
    if not (local_bundle / "bundle.json").is_file():
        raise BridgeError(EXIT_NO_EVIDENCE, f"fetched bundle has no bundle.json: {local_bundle}")
    return log


def _cleanup_remote(cfg: dict[str, Any], *paths: str) -> None:
    script = "sudo -n rm -rf " + " ".join(paths) + "\n"
    try:
        _run(
            _ssh_base(cfg) + ["bash", "-s"],
            input_text=script,
            timeout=30,
            what="guest cleanup",
        )
    except BridgeError:
        pass


def ingest_bundle(
    *,
    data_dir: Path,
    job_id: str,
    run_id: str,
    attempt_id: str,
    route: str,
    local_bundle: Path,
) -> dict[str, Any]:
    from tools.route_bundle_ingest import IngestError, ingest as ingest_guest_bundle

    try:
        return ingest_guest_bundle(
            data_dir=data_dir,
            job_id=job_id,
            run_id=run_id,
            attempt_id=attempt_id,
            route=route,
            bundle=local_bundle,
            trigger=False,
        )
    except (IngestError, OSError, ValueError) as exc:
        raise BridgeError(EXIT_INGEST, f"guest bundle was rejected: {exc}") from exc


def _run_once() -> int:
    data_dir = Path(_env("BINARYREPAIR_DATA_DIR"))
    job_id = _check_id("BINARYREPAIR_JOB_ID", _env("BINARYREPAIR_JOB_ID"))
    run_id = _check_id("BINARYREPAIR_RUN_ID", _env("BINARYREPAIR_RUN_ID"))
    attempt_id = _check_id("BINARYREPAIR_ATTEMPT_ID", _env("BINARYREPAIR_ATTEMPT_ID"))
    route = _env("BINARYREPAIR_ROUTE")
    if route not in ROUTES:
        raise BridgeError(EXIT_ENV, f"unknown attempt route: {route!r}")

    cfg = settings()
    if not probe(cfg, use_cache=False):
        raise BridgeError(EXIT_UNAVAILABLE, f"guest {cfg['host']} is not ready")

    attempt_root = data_dir / "jobs" / job_id / "runs" / run_id / "attempts" / attempt_id
    package_root = attempt_root.with_suffix(".package")
    local_bundle = attempt_root.with_suffix(f".{SUFFIX[route]}")
    package_json = build_package(
        data_dir=data_dir,
        job_id=job_id,
        run_id=run_id,
        attempt_id=attempt_id,
        route=route,
        package_root=package_root,
    )
    log = dispatch(
        cfg=cfg,
        package_json=package_json,
        package_root=package_root,
        job_id=job_id,
        run_id=run_id,
        attempt_id=attempt_id,
        local_bundle=local_bundle,
    )
    (attempt_root.parent / f"{attempt_id}.{SUFFIX[route]}.bridge.log").write_text(
        log, encoding="utf-8"
    )
    result = ingest_bundle(
        data_dir=data_dir,
        job_id=job_id,
        run_id=run_id,
        attempt_id=attempt_id,
        route=route,
        local_bundle=local_bundle,
    )
    print(json.dumps({"attempt": attempt_id, "route": route, "ingested": result.get("imported")}, sort_keys=True))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.guest_bridge")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="check the guest channel and exit 0 (ready) or 75 (unavailable)",
    )
    args = parser.parse_args(argv)
    try:
        if args.probe:
            cfg = settings()
            return EXIT_OK if probe(cfg, use_cache=False) else EXIT_UNAVAILABLE
        return _run_once()
    except BridgeError as exc:
        print(f"guest_bridge: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - a bridge must fail closed
        print(f"guest_bridge: unexpected failure: {exc}", file=sys.stderr)
        return EXIT_NO_EVIDENCE


if __name__ == "__main__":
    raise SystemExit(main())
