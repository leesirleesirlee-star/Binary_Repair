from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.contracts import OBLIGATION_KINDS, SecurityContract


def contract(**over) -> SecurityContract:
    base = dict(
        target_entry="copy_input",
        target_signature="int copy_input(const char *src)",
        valid_domain="ASCII inputs of length 0..63 bytes",
        valid_behavior="returns 0 and copies the input unchanged",
        forbidden_behavior="any write outside the 64-byte destination",
        invalid_input_response="return -1 without writing",
        dest_capacity_bytes=50,
        max_valid_input_bytes=49,
        capacity_includes_terminator=True,
        invalid_exit_code=1,
        invalid_stdout="",
        invalid_stderr="ERROR: input exceeds destination capacity\n",
        authorized_edits=("add a capacity guard at function entry",),
        allowed_calls=(),
        environment_assumptions=("System V AMD64 ABI", "single thread"),
    )
    base.update(over)
    return SecurityContract(**base)


def test_hash_is_deterministic_and_hex() -> None:
    h1 = contract().sha256()
    h2 = contract().sha256()
    assert h1 == h2
    assert len(h1) == 64
    assert all(ch in "0123456789abcdef" for ch in h1)


def test_list_order_does_not_change_hash() -> None:
    a = contract(allowed_calls=("memcpy", "strlen"))
    b = contract(allowed_calls=("strlen", "memcpy"))
    assert a.sha256() == b.sha256()


@pytest.mark.parametrize(
    "field,value",
    [
        ("valid_domain", "ASCII inputs of length 0..31 bytes"),
        ("valid_behavior", "returns 1"),
        ("forbidden_behavior", "any write outside the 32-byte destination"),
        ("invalid_input_response", "return -2 without writing"),
        ("authorized_edits", ("replace the copy loop",)),
        ("allowed_calls", ("memcpy",)),
        ("environment_assumptions", ("System V AMD64 ABI",)),
        ("required_obligations", ("identity",)),
        ("dest_capacity_bytes", 64),
        ("max_valid_input_bytes", 48),
        ("invalid_exit_code", 2),
        ("invalid_stderr", "different error\n"),
    ],
)
def test_any_field_change_changes_hash(field: str, value) -> None:
    assert contract().sha256() != replace(contract(), **{field: value}).sha256()


def test_round_trip_preserves_hash() -> None:
    original = contract()
    restored = SecurityContract.from_dict(original.to_dict())
    assert restored.sha256() == original.sha256()
    assert restored == original


def test_unknown_field_is_rejected() -> None:
    data = contract().to_dict()
    data["reviewer_label"] = "optional elsewhere, not in the contract hash"
    with pytest.raises(ValueError):
        SecurityContract.from_dict(data)


def test_missing_field_is_rejected() -> None:
    data = contract().to_dict()
    del data["forbidden_behavior"]
    with pytest.raises(ValueError):
        SecurityContract.from_dict(data)


def test_empty_text_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract(valid_behavior="   ")


def test_non_ascii_text_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract(valid_domain="长度为 0..63 字节")


def test_invalid_target_entry_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract(target_entry="not a function name")


def test_unknown_obligation_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract(required_obligations=("identity", "vibes"))


def test_empty_obligations_are_rejected() -> None:
    with pytest.raises(ValueError):
        contract(required_obligations=())


def test_duplicate_allowed_calls_are_rejected() -> None:
    with pytest.raises(ValueError):
        contract(allowed_calls=("memcpy", "memcpy"))


def test_default_obligations_cover_v0_to_v5() -> None:
    assert contract().required_obligations == OBLIGATION_KINDS
    assert set(OBLIGATION_KINDS) == {
        "identity",
        "structure",
        "interface",
        "behaviour",
        "symbolic",
        "reproducibility",
    }


def test_module_helpers_match_methods() -> None:
    from app.domain.contracts import canonical_contract_json, contract_sha256

    original = contract()
    data = original.to_dict()
    assert canonical_contract_json(data) == original.canonical_json()
    assert contract_sha256(data) == original.sha256()


def test_module_helper_rejects_unknown_field() -> None:
    from app.domain.contracts import canonical_contract_json

    data = contract().to_dict()
    data["extra"] = "not allowed"
    with pytest.raises(ValueError):
        canonical_contract_json(data)


def test_capacity_must_leave_room_for_terminator() -> None:
    with pytest.raises(ValueError):
        contract(dest_capacity_bytes=50, max_valid_input_bytes=50)


def test_invalid_exit_code_must_fit_a_process_status() -> None:
    with pytest.raises(ValueError):
        contract(invalid_exit_code=256)


def test_empty_stdout_is_allowed_but_stderr_can_be_exact() -> None:
    c = contract(invalid_stdout="", invalid_stderr="ERROR: input exceeds destination capacity\n")
    assert c.invalid_stdout == ""
    assert c.invalid_stderr.endswith("\n")
    assert c.to_dict()["invalid_stderr"] == "ERROR: input exceeds destination capacity\n"


def test_schema_version_is_two() -> None:
    assert contract().schema_version == 2
