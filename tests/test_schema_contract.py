from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from binaryrepair_ai.schemas import CandidateV1
from schemas.generate import OUT_DIR, build

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "schemas" / "examples"


def load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def load_schema(name: str) -> dict:
    return json.loads((OUT_DIR / name).read_text(encoding="utf-8"))


def test_generated_files_are_current() -> None:
    rendered = build()
    for name, text in rendered.items():
        path = OUT_DIR / name
        assert path.exists(), f"missing generated schema: {name}"
        assert path.read_text(encoding="utf-8") == text, (
            f"{name} is stale; run: python schemas/generate.py"
        )


def test_schema_has_identity_and_closed_object() -> None:
    schema = load_schema("candidate.v1.json")
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["$id"].endswith("candidate.v1.json")
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("name", ["candidate.routeA.json", "candidate.routeB.json"])
def test_examples_validate_against_json_schema_and_pydantic(name: str) -> None:
    obj = load_example(name)
    Draft202012Validator(load_schema("candidate.v1.json")).validate(obj)
    CandidateV1.model_validate(obj)


def test_context_example_validates() -> None:
    obj = load_example("context.routeB.json")
    Draft202012Validator(load_schema("context.v1.json")).validate(obj)


def test_extra_field_rejected_by_both_layers() -> None:
    obj = load_example("candidate.routeA.json")
    obj["surprise"] = 1

    with pytest.raises(Exception):
        Draft202012Validator(load_schema("candidate.v1.json")).validate(obj)
    with pytest.raises(ValidationError):
        CandidateV1.model_validate(obj)


def test_uppercase_hash_rejected_by_both_layers() -> None:
    obj = load_example("candidate.routeA.json")
    obj["original_sha256"] = "A" * 64

    with pytest.raises(Exception):
        Draft202012Validator(load_schema("candidate.v1.json")).validate(obj)
    with pytest.raises(ValidationError):
        CandidateV1.model_validate(obj)


def test_boolean_is_not_an_integer_parameter() -> None:
    obj = load_example("candidate.routeA.json")
    obj["parameters"]["capacity"] = True

    with pytest.raises(Exception):
        Draft202012Validator(load_schema("candidate.v1.json")).validate(obj)
    with pytest.raises(ValidationError):
        CandidateV1.model_validate(obj)


def test_json_schema_does_not_encode_route_semantics() -> None:
    """Documents the layered contract: JSON Schema is structural, Pydantic is semantic."""
    obj = load_example("candidate.routeA.json")
    obj["replacement_c"] = "int copy_name(void) { return 0; }"

    # structurally allowed: the field type is fine
    Draft202012Validator(load_schema("candidate.v1.json")).validate(obj)
    # semantically rejected: Route A must not carry Route B fields
    with pytest.raises(ValidationError):
        CandidateV1.model_validate(obj)
