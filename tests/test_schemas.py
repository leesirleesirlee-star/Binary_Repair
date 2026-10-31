from __future__ import annotations

import hashlib

import httpx
import pytest
from pydantic import ValidationError

from binaryrepair_ai.schemas import CandidateRejected, CandidateV1, RepairContext, check_candidate

H = hashlib.sha256


def hx(text: str) -> str:
    return H(text.encode()).hexdigest()


def ctx_a(**over) -> RepairContext:
    base = dict(
        route="route_a",
        original_sha256=hx("orig"),
        contract_sha256=hx("contract"),
        analysis_sha256=hx("analysis"),
        target_entry="copy_name",
        target_signature="int copy_name(char *dst, const char *src, unsigned int n)",
        contract_summary="Valid: 0..15 bytes. Invalid: >=16 must return -1 without writing.",
        allowed_evidence_ids=["ev_analysis_1", "ev_bytes_2"],
        templates=[
            {
                "template_id": "guard_capacity",
                "description": "Return error before the copy when n >= capacity.",
                "parameters": {
                    "capacity": {"min_value": 1, "max_value": 4096},
                    "error_return": {"min_value": -4096, "max_value": 0},
                },
            }
        ],
    )
    base.update(over)
    return RepairContext(**base)


def ctx_b(**over) -> RepairContext:
    base = dict(
        route="route_b",
        original_sha256=hx("orig"),
        contract_sha256=hx("contract"),
        analysis_sha256=hx("analysis"),
        target_entry="copy_name",
        target_signature="int copy_name(char *dst, const char *src, unsigned int n)",
        contract_summary="Valid: 0..15 bytes. Invalid: >=16 must return -1 without writing.",
        allowed_evidence_ids=["ev_analysis_1"],
        recovered_c="int copy_name(char *dst, const char *src, unsigned int n) { return 0; }",
        recovered_function_sha256=hx("recovered"),
        allowed_calls=[],
    )
    base.update(over)
    return RepairContext(**base)


def cand_a(**over) -> dict:
    c = ctx_a()
    base = dict(
        schema_version=1,
        original_sha256=c.original_sha256,
        contract_sha256=c.contract_sha256,
        analysis_sha256=c.analysis_sha256,
        route="route_a",
        target_entry="copy_name",
        evidence_ids=["ev_bytes_2"],
        template_id="guard_capacity",
        parameters={"capacity": 16, "error_return": -1},
        expected_effects=["overlong input returns -1 before any write"],
        assumptions=["capacity value is the reviewed destination size"],
        uncertainties=["none"],
    )
    base.update(over)
    return base


def cand_b(**over) -> dict:
    c = ctx_b()
    src = (
        "int copy_name(char *dst, const char *src, unsigned int n)\n"
        "{\n"
        "    unsigned int i;\n"
        "    if (n >= 16u) return -1;\n"
        "    for (i = 0; i < n; i++) dst[i] = src[i];\n"
        "    dst[n] = 0;\n"
        "    return (int)i;\n"
        "}\n"
    )
    base = dict(
        schema_version=1,
        original_sha256=c.original_sha256,
        contract_sha256=c.contract_sha256,
        analysis_sha256=c.analysis_sha256,
        route="route_b",
        target_entry="copy_name",
        evidence_ids=["ev_analysis_1"],
        template_id=None,
        parameters=None,
        recovered_function_sha256=c.recovered_function_sha256,
        replacement_c=src,
        expected_effects=["overlong input returns -1 before writing"],
        assumptions=["int is 32-bit, unsigned int is 32-bit"],
        uncertainties=["recovered loop shape may differ from source"],
    )
    base.update(over)
    return base


def test_valid_route_a_passes() -> None:
    c = ctx_a()
    cand = CandidateV1.model_validate(cand_a())
    check_candidate(cand, c)


def test_valid_route_b_passes() -> None:
    c = ctx_b()
    cand = CandidateV1.model_validate(cand_b())
    check_candidate(cand, c)


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateV1.model_validate(cand_a(extra="surprise"))


def test_stringly_typed_parameter_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateV1.model_validate(cand_a(parameters={"capacity": "16", "error_return": -1}))


def test_route_a_without_template_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateV1.model_validate(cand_a(template_id=None))


def test_unknown_template_rejected() -> None:
    c = ctx_a()
    cand = CandidateV1.model_validate(cand_a(template_id="arbitrary_asm"))
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert exc.value.reason_code == "scope_violation"


def test_parameter_out_of_range_rejected() -> None:
    c = ctx_a()
    cand = CandidateV1.model_validate(cand_a(parameters={"capacity": 99999, "error_return": -1}))
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert "outside" in str(exc.value)


def test_identity_mismatch_rejected() -> None:
    c = ctx_a()
    cand = CandidateV1.model_validate(cand_a(original_sha256=hx("other")))
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert exc.value.reason_code == "identity_mismatch"


def test_unknown_evidence_rejected() -> None:
    c = ctx_a()
    cand = CandidateV1.model_validate(cand_a(evidence_ids=["ev_made_up"]))
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert exc.value.reason_code == "scope_violation"


def test_route_b_include_rejected() -> None:
    c = ctx_b()
    bad = "#include <string.h>\nint copy_name(char *dst, const char *src, unsigned int n){return 0;}"
    cand = CandidateV1.model_validate(cand_b(replacement_c=bad))
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert "forbidden" in str(exc.value)


def test_route_b_unknown_callee_rejected() -> None:
    c = ctx_b()
    bad = "int copy_name(char *dst, const char *src, unsigned int n)\n{\n    puts(dst);\n    return 0;\n}"
    cand = CandidateV1.model_validate(cand_b(replacement_c=bad))
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert "allowed set" in str(exc.value)


def test_route_b_wrong_recovered_hash_rejected() -> None:
    c = ctx_b()
    cand = CandidateV1.model_validate(cand_b(recovered_function_sha256=hx("different")))
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert exc.value.reason_code == "identity_mismatch"


def test_route_b_must_define_target() -> None:
    c = ctx_b()
    cand = CandidateV1.model_validate(
        cand_b(replacement_c="int other(char *d, const char *s, unsigned int n){return 0;}")
    )
    with pytest.raises(CandidateRejected) as exc:
        check_candidate(cand, c)
    assert "does not define" in str(exc.value)


def test_context_rejects_route_mismatch() -> None:
    with pytest.raises(ValueError):
        RepairContext(
            route="route_a",
            original_sha256=hx("o"),
            contract_sha256=hx("c"),
            analysis_sha256=hx("a"),
            target_entry="copy_name",
            contract_summary="x",
            allowed_evidence_ids=["ev_1"],
        )


def test_context_rejects_uppercase_hash() -> None:
    with pytest.raises(ValueError):
        ctx_a(original_sha256="A" * 64)
