#!/usr/bin/env python3
"""Generate the JSON Schema contract files from the Pydantic models.

The Pydantic models in ``binaryrepair_ai/schemas.py`` are the source of truth.
This script renders them to portable JSON Schema so that:

  * the GUI owner can validate/display fields without importing Python,
  * the validation owner can write a checker in another language if needed,
  * CI can fail when the checked-in schema drifts from the models.

Usage:
    python schemas/generate.py            # write files
    python schemas/generate.py --check    # exit 1 if files are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from binaryrepair_ai.schemas import (  # noqa: E402
    CandidateV1,
    RepairContext,
    TemplateParam,
    TemplateSpec,
)

OUT_DIR = ROOT / "schemas" / "generated"
BASE_ID = "https://binaryrepair.local/schemas"

# Human documentation belongs next to the field, not only in a wiki page.
DESCRIPTIONS: dict[str, dict[str, str]] = {
    "CandidateV1": {
        "schema_version": "Contract version. Bump only with a coordinated GUI + validation change.",
        "original_sha256": "SHA-256 of the frozen original ELF; binds the candidate to one input.",
        "contract_sha256": "SHA-256 of the reviewed contract revision.",
        "analysis_sha256": "SHA-256 of the binary-derived analysis bundle used for this candidate.",
        "route": "Repair route: A = reviewed instruction template, B = recovered C.",
        "target_entry": "Reviewed function entry the candidate is allowed to modify.",
        "evidence_ids": "Server-issued evidence identifiers this candidate relies on.",
        "template_id": "Route A only: exactly one template offered in the request.",
        "parameters": "Route A only: exactly the template's parameters, integers inside range.",
        "recovered_function_sha256": "Route B only: hash of the recovered C supplied in the request.",
        "replacement_c": "Route B only: complete C definition of the target function.",
        "expected_effects": "Claimed effects. Reviewed by humans; never treated as verified.",
        "assumptions": "Conditions the candidate depends on.",
        "uncertainties": "Known gaps. An entry here does not by itself block validation.",
    },
    "RepairContext": {
        "route": "Which route this request is for; the model cannot change it.",
        "original_sha256": "Identity of the frozen input binary.",
        "contract_sha256": "Identity of the reviewed contract.",
        "analysis_sha256": "Identity of the approved analysis bundle.",
        "target_entry": "The only function the candidate may target.",
        "target_signature": "Recovered C signature, for interface compatibility.",
        "contract_summary": "Reviewed contract excerpt. The only authority on scope.",
        "allowed_evidence_ids": "Closed set of evidence ids the model may cite.",
        "templates": "Route A: closed list of reviewed templates and parameter ranges.",
        "recovered_function_sha256": "Route B: hash of recovered_c, echoed back by the model.",
        "recovered_c": "Route B: recovered function source, treated as untrusted data.",
        "allowed_calls": "Route B: callees the replacement may use; everything else is scope violation.",
        "max_replacement_chars": "Route B: maximum accepted replacement_c length.",
    },
    "TemplateSpec": {
        "template_id": "Stable identifier used by the deterministic builder.",
        "description": "What the template does, in one sentence.",
        "parameters": "Parameters the model must supply, with inclusive integer ranges.",
    },
    "TemplateParam": {
        "min_value": "Inclusive lower bound.",
        "max_value": "Inclusive upper bound.",
    },
}


def _decorate(model_name: str, schema: dict, id_name: str) -> dict:
    """Add $schema/$id and per-field descriptions without changing structure."""
    descriptions = DESCRIPTIONS.get(model_name, {})
    for prop, text in descriptions.items():
        target = schema.get("properties", {}).get(prop)
        if target is not None:
            target["description"] = text

    for def_name, def_schema in (schema.get("$defs") or {}).items():
        for prop, text in DESCRIPTIONS.get(def_name, {}).items():
            target = (def_schema.get("properties") or {}).get(prop)
            if target is not None:
                target["description"] = text

    ordered = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_ID}/{id_name}.v1.json",
    }
    ordered.update({k: v for k, v in schema.items() if k not in ordered})
    return ordered


def build() -> dict[str, str]:
    """Return {filename: serialized json} for every contract file."""
    files = {
        "candidate.v1.json": _decorate("CandidateV1", CandidateV1.model_json_schema(), "candidate"),
        "context.v1.json": _decorate("RepairContext", RepairContext.model_json_schema(), "context"),
        "template.v1.json": _decorate("TemplateSpec", TemplateSpec.model_json_schema(), "template"),
        "template-param.v1.json": _decorate("TemplateParam", TemplateParam.model_json_schema(), "template-param"),
    }
    return {
        name: json.dumps(schema, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
        for name, schema in files.items()
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify files are up to date")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    rendered = build()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    stale = []
    for name, text in rendered.items():
        path = args.out_dir / name
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            stale.append(name)
            if not args.check:
                path.write_text(text, encoding="utf-8")

    if args.check:
        if stale:
            print("schema drift detected: " + ", ".join(stale), file=sys.stderr)
            print("run: python schemas/generate.py", file=sys.stderr)
            return 1
        print(f"schema up to date ({len(rendered)} files)")
        return 0

    print(f"wrote {len(rendered)} files to {args.out_dir}")
    if stale:
        print("updated: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
