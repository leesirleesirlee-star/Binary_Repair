#!/usr/bin/env python3
"""End-to-end candidate demo: reviewed context -> DeepSeek -> validated candidate.

This is the vertical slice for this week. It sends only public fixture material.

    # offline: uses a canned model response, exercises the whole validation path
    .venv/bin/python scripts/demo_candidate.py --mock

    # real call (costs a fraction of a cent at flash pricing)
    .venv/bin/python scripts/demo_candidate.py \
        --recovered routeb/out/copy_name.normalized.c \
        --out-c routeb/out/copy_name.model.c

    # then, in the Patcherex2 venv:
    .venv-patcherex/bin/python routeb/probe_patcherex2.py \
        --mode fixed --c-source routeb/out/copy_name.model.c
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from binaryrepair_ai.client import DeepSeekClient  # noqa: E402
from binaryrepair_ai.provider import DeepSeekProvider  # noqa: E402
from binaryrepair_ai.schemas import RepairContext  # noqa: E402
from binaryrepair_ai.settings import Settings  # noqa: E402

DEFAULT_BINARY = ROOT / "routeb" / "fixture" / "vuln"
DEFAULT_RECOVERED = ROOT / "routeb" / "out" / "copy_name.normalized.c"
EVIDENCE_IDS = ["ev_binary_identity", "ev_ghidra_export", "ev_roundtrip_baseline"]
CONTRACT = """\
Destination capacity: 16 bytes (reviewed from the binary's frame layout).
Valid input: 0..15 bytes; the bytes are copied unchanged and the terminator is written
inside the destination.
Invalid input: 16 bytes or more; the function must return -1 and must not write to the
destination at all.
No new external calls, no global state, same signature and calling convention.
"""

MOCK_REPLACEMENT = """\
int copy_name(char *dst, const char *src, unsigned int n)
{
    unsigned int i;
    if (n >= 16u) {
        return -1;
    }
    for (i = 0; i < n; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\\0';
    return (int)i;
}
"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_context(
    binary: Path,
    recovered: Path,
    signature: str,
    *,
    function: str = "copy_name",
    contract_text: str = CONTRACT,
    allowed_calls: tuple[str, ...] = (),
    evidence_ids: list[str] | None = None,
) -> RepairContext:
    recovered_c = recovered.read_text()
    analysis = json.dumps(
        {"binary": sha256_file(binary), "function": function, "callees": list(allowed_calls)},
        sort_keys=True,
    )
    return RepairContext(
        route="route_b",
        original_sha256=sha256_file(binary),
        contract_sha256=sha256_text(contract_text),
        analysis_sha256=sha256_text(analysis),
        target_entry=function,
        target_signature=signature,
        contract_summary=contract_text,
        allowed_evidence_ids=evidence_ids or EVIDENCE_IDS,
        recovered_c=recovered_c,
        recovered_function_sha256=sha256_text(recovered_c),
        allowed_calls=list(allowed_calls),
    )


class MockTransport:
    def __init__(self, candidate: dict) -> None:
        self.candidate = candidate
        self.calls = 0

    def post(self, url, *, headers, json, timeout):
        import json as _json

        import httpx

        self.calls += 1
        return httpx.Response(
            200,
            json={
                "model": json["model"],
                "choices": [{"message": {"content": _json.dumps(self.candidate)}}],
                "usage": {
                    "prompt_tokens": 2400,
                    "completion_tokens": 180,
                    "total_tokens": 2580,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 2400,
                },
            },
        )

    def get(self, url, *, headers, timeout):
        raise NotImplementedError



def mock_candidate(ctx: RepairContext) -> dict:
    return {
        "schema_version": 1,
        "original_sha256": ctx.original_sha256,
        "contract_sha256": ctx.contract_sha256,
        "analysis_sha256": ctx.analysis_sha256,
        "route": "route_b",
        "target_entry": "copy_name",
        "evidence_ids": [EVIDENCE_IDS[1]],
        "template_id": None,
        "parameters": None,
        "recovered_function_sha256": ctx.recovered_function_sha256,
        "replacement_c": MOCK_REPLACEMENT,
        "expected_effects": ["inputs of 16 bytes or more return -1 before any write"],
        "assumptions": ["16 is the reviewed destination capacity"],
        "uncertainties": ["mock response for plumbing only"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    ap.add_argument("--recovered", type=Path, default=DEFAULT_RECOVERED)
    ap.add_argument("--function", default="copy_name", help="reviewed target function")
    ap.add_argument("--signature", default="int copy_name(char *dst, const char *src, unsigned int n)")
    ap.add_argument("--contract", type=Path, help="reviewed contract text file")
    ap.add_argument("--allowed-calls", default="", help="comma-separated callee allowlist")
    ap.add_argument("--evidence-ids", default=",".join(EVIDENCE_IDS))
    ap.add_argument("--out-c", type=Path)
    ap.add_argument("--thinking", action="store_true")
    args = ap.parse_args()

    if not args.binary.is_file():
        print(f"missing binary: {args.binary} (build it with routeb/fixture/build.sh)", file=sys.stderr)
        return 2
    if not args.recovered.is_file():
        print(
            f"missing recovered C: {args.recovered}\n"
            "Export it first: routeb/ghidra/export.sh routeb/fixture/vuln copy_name",
            file=sys.stderr,
        )
        return 2

    if args.mock and args.function != "copy_name":
        print("--mock is only defined for the synthetic copy_name fixture", file=sys.stderr)
        return 2

    contract_text = args.contract.read_text() if args.contract else CONTRACT
    allowed_calls = tuple(c.strip() for c in args.allowed_calls.split(",") if c.strip())
    evidence_ids = [e.strip() for e in args.evidence_ids.split(",") if e.strip()]

    ctx = build_context(
        args.binary,
        args.recovered,
        args.signature,
        function=args.function,
        contract_text=contract_text,
        allowed_calls=allowed_calls,
        evidence_ids=evidence_ids,
    )

    print("== reviewed context ==")
    print(f"  original_sha256          {ctx.original_sha256}")
    print(f"  recovered_function_sha256 {ctx.recovered_function_sha256}")
    print(f"  target_entry             {ctx.target_entry}")

    if args.mock:
        settings = Settings(api_key="mock-key", model="deepseek-flash")
        transport = MockTransport(mock_candidate(ctx))
        provider = DeepSeekProvider(
            settings,
            client=DeepSeekClient(settings, transport=transport, sleep=lambda _s: None),
            budget_usd=2.0,
        )
    else:
        provider = DeepSeekProvider.from_env()

    result = provider.request_candidate(ctx, thinking=args.thinking)

    print("\n== result ==")
    print(f"  ok: {result.ok}")
    print(f"  status: {result.record.status}")
    print(f"  latency_ms: {result.record.latency_ms}")
    print(f"  usage: {result.record.usage}")
    print(f"  estimated_cost_usd: {result.record.estimated_cost_usd}")

    if not result.ok:
        assert result.failure is not None
        print(json.dumps(result.failure.as_dict(), indent=2, ensure_ascii=False))
        return 3

    assert result.candidate is not None
    cand = result.candidate
    print("\n== candidate ==")
    print(f"  route: {cand.route}")
    print(f"  evidence_ids: {cand.evidence_ids}")
    print(f"  expected_effects: {cand.expected_effects}")
    print(f"  assumptions: {cand.assumptions}")
    print(f"  uncertainties: {cand.uncertainties}")
    print("\n--- replacement_c ---")
    print(cand.replacement_c)

    if args.out_c:
        args.out_c.parent.mkdir(parents=True, exist_ok=True)
        args.out_c.write_text(cand.replacement_c)
        print(f"\nwrote {args.out_c}")
        print("next: probe_patcherex2.py --mode fixed --c-source " + str(args.out_c))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
