"""Synthetic canonical contracts for the review-binding tests.

The v1 review texts are projections of a canonical ``SecurityContract``; a job
must carry the typed source before approval. These helpers build a valid v3
argv contract for synthetic tests only. No bytes are executed.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.contracts import SecurityContract

BASE_CONTRACT: dict[str, Any] = {
    "schema_version": 3,
    "target_entry": "target",
    "target_signature": "void target(void)",
    "input_mode": "argv",
    "valid_domain": "Bounds-checked input of at most 49 bytes",
    "valid_behavior": "Documented behavior for in-range input",
    "forbidden_behavior": "Out-of-bounds write to the target buffer",
    "invalid_input_response": "Return error code 1 without out-of-bounds access",
    "dest_capacity_bytes": 50,
    "max_valid_input_bytes": 49,
    "capacity_includes_terminator": True,
    "invalid_exit_code": 1,
    "invalid_stdout": "",
    "invalid_stderr": "",
    "authorized_edits": ["replace the bounds check"],
    "allowed_calls": ["strcpy", "strlen"],
    "environment_assumptions": ["Single process", "Linux x86-64 ELF", "Stack protector disabled"],
    "required_obligations": [
        "identity",
        "structure",
        "interface",
        "behaviour",
        "symbolic",
        "reproducibility",
    ],
}


def sample_typed_contract(**overrides: Any) -> dict[str, Any]:
    """Return a valid canonical contract payload, with optional field overrides."""
    payload = dict(BASE_CONTRACT)
    payload.update(overrides)
    SecurityContract.from_dict(payload)
    return payload


def typed_contract_form(**overrides: Any) -> str:
    """Return the canonical JSON text used as the multipart ``contract`` field."""
    return json.dumps(sample_typed_contract(**overrides), sort_keys=True)
