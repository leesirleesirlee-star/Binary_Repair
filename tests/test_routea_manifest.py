from __future__ import annotations

import json
from pathlib import Path

from app.domain.contracts import SecurityContract
from routea.cave import build_cave_patched, plan_route_a_cave

ROOT = Path(__file__).resolve().parents[1]
ELF = ROOT / "fixtures/public/CWE121_src_char_declare_cpy_51b/original.elf"
CONTRACT = ROOT / "fixtures/public/CWE121_src_char_declare_cpy_51b/contract.json"


def test_edit_manifest_schema_2(tmp_path) -> None:
    contract = SecurityContract.from_dict(json.loads(CONTRACT.read_text()))
    plan = plan_route_a_cave(ELF, contract, cave_addr=0x4018E9, cave_size=1815)
    manifest = build_cave_patched(ELF, plan, tmp_path / "candidate.elf", scope_authorized=True)
    data = manifest.to_dict()
    assert data["schema_version"] == 2
    assert data["patched_sha256"] == "c4a1592157a2858fcf2dd52623b6ae8c280355ce735a1c224530845569bcaa82"
    assert data["displaced"][0]["original_hex"] == "e8c7fdffff"
    assert data["displaced"][0]["new_hex"] == "e8e0050000"
    assert {a["kind"] for a in data["added"]} == {"code", "data"}
    assert all(len(a["sha256"]) == 64 for a in data["added"])
    rx = data["header_changes"]["rx_load"]
    assert rx["old_filesz"] == "0x8e9"
    assert rx["new_filesz"] == "0x1000"
    assert rx["page_congruent"] is True
    assert rx["no_overlap"] is True
    assert data["writer"].startswith("write(2")
    assert data["termination"] == "exit_group(1)"
    assert data["tool_versions"]["python"]
