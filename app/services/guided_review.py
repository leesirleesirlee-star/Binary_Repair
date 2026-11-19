"""Versioned contract preparation for human-reviewed conversation jobs."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from app.db.connection import connect
from app.domain.contracts import OBLIGATION_KINDS, SecurityContract, _clean_list, _clean_text
from app.domain.manifests import ManifestEntry, manifest_set_sha256
from app.services import jobs

ANSWER_KEYS = ("valid_domain", "forbidden", "invalid_outcome", "assumptions")
SETUP_KEYS = {
    "target_entry", "target_signature", "authorized_edits", "allowed_calls",
    "input_mode", "valid_behavior", "dest_capacity_bytes",
    "max_valid_input_bytes", "capacity_includes_terminator", "invalid_exit_code",
    "invalid_stdout", "invalid_stderr", "environment",
}
INVALID_KEYS = {
    "max_valid_input_bytes", "invalid_exit_code", "invalid_stdout", "invalid_stderr",
}
MAX_MANIFEST_BYTES = 256 * 1024


class ReviewConflict(ValueError):
    """The requested revision or lifecycle no longer permits the operation."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _answers(fields: dict[str, Any]) -> dict[str, Any]:
    normalized = {}
    for key in ANSWER_KEYS:
        value = fields.get(key)
        if key == "assumptions" and isinstance(value, list):
            value = "\n".join(value)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        # Reuse the same limits and validation for drafts and formal contracts.
        if key == "assumptions":
            normalized[key] = sorted(_clean_list(
                (line.strip() for line in value.split("\n") if line.strip()),
                key, unicode_prose=True,
            ))
        else:
            normalized[key] = _clean_text(value, key, allow_empty=True, unicode_prose=True)
    return normalized


def draft_json(fields: dict[str, Any]) -> str:
    return _json(_answers(fields))


def _latest(connection, job_id: str):
    return connection.execute(
        "SELECT * FROM guided_reviews WHERE job_id = ? ORDER BY revision DESC LIMIT 1",
        (job_id,),
    ).fetchone()


@contextmanager
def _transaction(data_dir: str | Path):
    with closing(connect(data_dir)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        yield connection


def initialize(
    job_id: str, fields: dict[str, Any], *, data_dir: str | Path,
    synthetic_job: dict[str, Any] | None = None,
) -> None:
    with _transaction(data_dir) as connection:
        if synthetic_job is not None:
            connection.execute(
                "INSERT INTO jobs (id, original_sha256, filename, defect_description, phase, created_at) "
                "VALUES (?, ?, ?, ?, 'draft', ?)",
                (job_id, synthetic_job["original_sha256"], synthetic_job["filename"],
                 synthetic_job["defect"], synthetic_job["created_at"]),
            )
        connection.execute(
            "INSERT INTO guided_reviews (job_id, revision, answers_json) VALUES (?, 1, ?)",
            (job_id, draft_json(fields)),
        )


def _contract(answers: dict[str, Any], setup: dict[str, Any]) -> SecurityContract:
    fields = setup["fields"]
    body = {key: value for key, value in fields.items() if key != "environment"}
    body.update(
        schema_version=3,
        valid_domain=answers["valid_domain"],
        forbidden_behavior=answers["forbidden"],
        environment_assumptions=answers["assumptions"],
        required_obligations=list(OBLIGATION_KINDS),
    )
    if fields["input_mode"] != "none":
        body["invalid_input_response"] = answers["invalid_outcome"]
    return SecurityContract.from_dict(body)


def _manifest_binding(
    files: dict[str, str], original_sha256: str, *, input_mode: str,
    fields: dict[str, Any],
) -> str:
    names = {"valid.json"} if input_mode == "none" else {"valid.json", "invalid.json"}
    if set(files) != names:
        raise ValueError("Attach valid.json and, for external input, invalid.json.")
    case_ids = set()
    test_ids = set()
    entries = []
    for name, contents in sorted(files.items()):
        if not isinstance(contents, str) or len(contents.encode("utf-8")) > MAX_MANIFEST_BYTES:
            raise ValueError(f"{name} must be JSON text up to 256 KiB")
        try:
            manifest = json.loads(contents.removeprefix("\ufeff"))
        except ValueError as exc:
            raise ValueError(f"{name} contains invalid JSON") from exc
        if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise ValueError(f"{name} must be a schema-version 1 test manifest")
        if manifest.get("original_sha256") != original_sha256:
            raise ValueError(f"{name} is bound to a different original file")
        if not isinstance(manifest.get("case_id"), str):
            raise ValueError(f"{name} is missing its case ID")
        case_ids.add(manifest["case_id"])
        tests = manifest.get("tests")
        if not isinstance(tests, list) or not tests:
            raise ValueError(f"{name} must include at least one test")
        kind = name.removesuffix(".json")
        for case in tests:
            if not isinstance(case, dict) or case.get("kind") != kind:
                raise ValueError(f"{name} must contain {kind} tests")
            test_id = case.get("id")
            if not isinstance(test_id, str) or not test_id.strip() or test_id in test_ids:
                raise ValueError("Each test must have a unique nonempty ID")
            test_ids.add(test_id)
            argv = case.get("argv")
            if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
                raise ValueError(f"{name} tests must include an argument list")
            if input_mode != "argv" and argv:
                raise ValueError(f"{name} tests cannot declare arguments for this input mode")
            if input_mode == "stdin" and not isinstance(case.get("stdin"), str):
                raise ValueError(f"{name} tests must include standard input")
            if input_mode == "none" and case.get("stdin") not in (None, ""):
                raise ValueError(f"{name} cannot supply standard input")
            expected = case.get("expected")
            if (not isinstance(expected, dict) or type(expected.get("return_code")) is not int
                or not 0 <= expected["return_code"] <= 255
                or not all(isinstance(expected.get(key), str) for key in ("stdout", "stderr"))):
                raise ValueError(f"{name} tests must specify an exit code, stdout and stderr")
            if kind == "invalid" and any(
                expected[key] != fields[rule] for key, rule in (
                    ("return_code", "invalid_exit_code"), ("stdout", "invalid_stdout"),
                    ("stderr", "invalid_stderr"),
                )
            ):
                raise ValueError("Invalid test expectations must match the confirmed response")
        entries.append(ManifestEntry(name, hashlib.sha256(contents.encode("utf-8")).hexdigest()))
    if len(case_ids) != 1:
        raise ValueError("The manifests must use the same case ID")
    return manifest_set_sha256(
        case_id=case_ids.pop(), original_sha256=original_sha256, entries=entries,
    )


def _confirmed_setup(
    body: dict[str, Any], previous: dict[str, Any] | None, original_sha256: str,
) -> dict[str, Any]:
    fields = body.get("fields")
    files = body.get("manifests")
    reviewer = body.get("reviewer_label")
    if not isinstance(fields, dict) or set(fields) - SETUP_KEYS:
        raise ValueError("Send the structured setup fields only")
    fields = dict(fields)
    if (not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128
        or any(not 32 <= ord(char) <= 126 for char in reviewer.strip())):
        raise ValueError("Enter a reviewer name using at most 128 printable ASCII characters")
    environment = fields.get("environment")
    if (not isinstance(environment, str) or not environment.strip() or len(environment) > 300
        or any(not 32 <= ord(char) <= 126 for char in environment.strip())):
        raise ValueError("Enter the execution environment using printable ASCII characters")
    fields["environment"] = environment.strip()
    if fields.get("input_mode") == "none" and set(fields) & INVALID_KEYS:
        raise ValueError("A no-input setup must omit rejection settings")
    # Technical setup can be confirmed before the prose draft is finished.
    shape = _contract(
        dict(valid_domain="Pending", forbidden="Pending", invalid_outcome="Pending", assumptions=[]),
        {"fields": fields},
    ).to_dict()
    fields.update({key: shape[key] for key in shape.keys() & SETUP_KEYS})
    if not fields["authorized_edits"]:
        raise ValueError("Specify the authorized edits for this target")
    if not isinstance(files, dict):
        raise ValueError("Attach the test manifests")
    if previous and previous["fields"]["input_mode"] == fields["input_mode"]:
        files = {**previous["manifests"], **files}
    return {
        "fields": fields, "manifests": files, "confirmed_by": reviewer.strip(),
        "manifest_sha256": _manifest_binding(
            files, original_sha256, input_mode=fields["input_mode"], fields=fields,
        ),
    }


def _view(connection, row) -> dict[str, Any]:
    answers = json.loads(row["answers_json"])
    setup = json.loads(row["setup_json"]) if row["setup_json"] else None
    formal = None
    blockers = []
    if setup is None:
        blockers.extend(("repair_target", "execution_settings", "test_manifests"))
    else:
        for key in ANSWER_KEYS[:3]:
            if not answers[key] and (key != "invalid_outcome" or setup["fields"]["input_mode"] != "none"):
                blockers.append(key)
        if not blockers:
            formal = _contract(answers, setup)
            answers = {
                "valid_domain": formal.valid_domain, "forbidden": formal.forbidden_behavior,
                "invalid_outcome": formal.invalid_input_response or "",
                "assumptions": formal.canonical_payload()["environment_assumptions"],
            }
    digest = formal.sha256() if formal else hashlib.sha256(_json(answers).encode("utf-8")).hexdigest()
    approval = connection.execute(
        "SELECT * FROM contracts WHERE job_id = ? AND revision = ? AND sha256 = ?",
        (row["job_id"], row["revision"], digest),
    ).fetchone() if formal else None
    return {
        "contract": {
            **answers, "revision": row["revision"], "sha256": digest,
            "approved": approval is not None,
            "approved_at": approval["approved_at"] if approval else None,
            "reviewer_label": approval["reviewer_label"] if approval else None,
        },
        "technical_setup": {
            "fields": setup["fields"] if setup else {},
            "confirmed_by": setup["confirmed_by"] if setup else None,
            "manifests": sorted(setup["manifests"]) if setup else [],
            "blockers": blockers,
            "required_obligations": list(OBLIGATION_KINDS),
        },
        "manifest_sha256": setup["manifest_sha256"] if setup else None,
        "environment": setup["fields"]["environment"] if setup else "",
    }


def read(job_id: str, *, data_dir: str | Path) -> dict[str, Any] | None:
    with closing(connect(data_dir)) as connection:
        row = _latest(connection, job_id)
        return _view(connection, row) if row else None


def change(
    job_id: str, action: str, body: dict[str, Any], *, data_dir: str | Path,
    synthetic: bool = False,
) -> dict[str, Any]:
    with _transaction(data_dir) as connection:
        row = _latest(connection, job_id)
        if row is None:
            raise ReviewConflict("This job does not use guided contract review.")
        expected = body.get("expected_revision")
        if type(expected) is not int or expected != row["revision"]:
            raise ReviewConflict("The contract revision changed.")
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job["phase"] == "cancelled":
            raise ReviewConflict("The job is cancelled.")
        view = _view(connection, row)
        if action in ("approve", "start"):
            return _authorize(connection, job, row, action, body, view, data_dir, synthetic)
        if connection.execute(
            "SELECT 1 FROM runs WHERE job_id = ? AND phase NOT IN ('finished', 'cancelled')",
            (job_id,),
        ).fetchone():
            raise ReviewConflict("A run is active for this job.")
        answers = json.loads(row["answers_json"])
        setup = json.loads(row["setup_json"]) if row["setup_json"] else None
        if action == "setup":
            setup = _confirmed_setup(body, setup, job["original_sha256"])
        elif action == "save":
            answers = _answers({key: body.get(key, answers[key]) for key in ANSWER_KEYS})
        else:
            raise ValueError("Unknown guided review action")
        if setup and setup["fields"]["input_mode"] == "none":
            answers["invalid_outcome"] = ""
        answers_json, setup_json = _json(answers), _json(setup) if setup else None
        if (answers_json, setup_json) != (row["answers_json"], row["setup_json"]):
            connection.execute(
                "INSERT INTO guided_reviews VALUES (?, ?, ?, ?)",
                (job_id, row["revision"] + 1, answers_json, setup_json),
            )
        return _view(connection, _latest(connection, job_id))


def _authorize(connection, job, row, action, body, view, data_dir, synthetic) -> dict[str, Any]:
    contract = view["contract"]
    if view["technical_setup"]["blockers"] or body.get("expected_sha256") != contract["sha256"]:
        raise ReviewConflict("Complete setup and review the current contract before continuing.")
    manifest = view["manifest_sha256"]
    if action == "approve":
        reviewer = body.get("reviewer_label")
        if (not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128
            or any(not 32 <= ord(char) <= 126 for char in reviewer.strip())):
            raise ValueError("A reviewer name is required")
        if connection.execute(
            "SELECT 1 FROM runs WHERE job_id = ? AND phase NOT IN ('finished', 'cancelled')",
            (job["id"],),
        ).fetchone():
            raise ReviewConflict("A run is active for this job.")
        formal = _contract(json.loads(row["answers_json"]), json.loads(row["setup_json"]))
        jobs.approve_contract(
            job["id"], contract_revision=row["revision"], reviewer_label=reviewer.strip(),
            data_dir=data_dir, contract_body=formal.to_dict(), input_sha256=job["original_sha256"],
            manifest_sha256=manifest, transaction=connection,
        )
        return _view(connection, row)
    if not contract["approved"]:
        raise ReviewConflict("Approve the current contract before starting.")
    if synthetic:
        return {}
    stored = connection.execute(
        "SELECT * FROM contracts WHERE job_id = ? AND revision = ?", (job["id"], row["revision"]),
    ).fetchone()
    approval = jobs.approval_from_contract(stored, manifest_sha256=manifest)
    key = body.get("idempotency_key")
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValueError("A valid idempotency key is required")
    try:
        run = jobs.start_run(
            job["id"], approval=approval, idempotency_key=key,
            data_dir=data_dir, transaction=connection,
        )
    except ValueError as exc:
        raise ReviewConflict(str(exc)) from exc
    return {"run_id": run.id, "phase": run.phase}
