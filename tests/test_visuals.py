"""Security-visualization data tests (disassembly / recovered C code view)."""

from __future__ import annotations

from app.backend import LocalBackend, MockBackend, _has_cjk_in_payload

FORM = dict(
    defect="strcpy in stack_buf does not check length; overlong input writes out of bounds",
    valid_tests="valid: hello",
    environment="Ubuntu x86-64 VM (test)",
    valid_domain="Input length 0-64 bytes",
    forbidden="Out-of-bounds write",
    invalid_outcome="Return error code 1",
    assumptions="Single process",
)


def _create(backend) -> str:
    return backend.create_job(
        binary_bytes=b"\x7fELF visuals demo",
        filename="sample.bin",
        defect=FORM["defect"],
        valid_tests=FORM["valid_tests"],
        environment=FORM["environment"],
        valid_domain=FORM["valid_domain"],
        forbidden=FORM["forbidden"],
        invalid_outcome=FORM["invalid_outcome"],
        assumptions=FORM["assumptions"],
    )


def test_mock_visuals_have_highlighted_lines_and_candidate():
    backend = MockBackend()
    job_id = _create(backend)
    visuals = backend.get_visuals(job_id)

    assert visuals["available"] is True
    assert visuals["note"]
    assert not _has_cjk_in_payload(visuals)

    route_a = visuals["routes"]["A"]
    assert route_a["mode"] == "asm"
    assert route_a["vulnerable"]["addr"] == "0x0000000000401136"
    assert any(line.get("tag") == "vulnerable" for line in route_a["original"])
    assert any(line.get("tag") == "added" for line in route_a["repaired"])
    assert route_a["candidate"]["template_id"] == "guard-strcpy-v1"

    route_b = visuals["routes"]["B"]
    assert route_b["mode"] == "c"
    assert any(line.get("tag") == "vulnerable" for line in route_b["original"])
    assert any(line.get("tag") == "added" for line in route_b["repaired"])
    assert route_b["candidate"]["assumptions"]


def test_local_visuals_are_honestly_unavailable(tmp_path):
    backend = LocalBackend(data_dir=tmp_path)
    job_id = _create(backend)
    visuals = backend.get_visuals(job_id)

    assert visuals["available"] is False
    assert "not implemented" in visuals["note"]
    assert not _has_cjk_in_payload(visuals)
