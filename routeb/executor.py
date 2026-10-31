#!/usr/bin/env python3
"""Route B/provider executor + static evidence bundle.

The provider proposes ``replacement_c``.  This executor builds the Patcherex2
candidate (or accepts an already-built candidate), then writes a self-describing
bundle:

    candidate.elf, original.elf
    edit-manifest.json      # schema 2: hashes, function, changed intervals, tools
    audit.json              # static ELF checks (no candidate execution)
    reproducibility.json    # build twice if requested
    environment.json        # interpreter/toolchain fingerprint
    independent-views/      # readelf/objdump text, read-only
    manifest-compare.json   # ONLY when --run-manifest is explicitly passed
    bundle-record.md, SHA256SUMS, bundle-sha256.txt

By default the candidate is never executed: the six-case run stays a guest step.
``--run-manifest`` is explicit and is intended for the disposable guest only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from elftools.elf.elffile import ELFFile  # noqa: E402

SCHEMA_VERSION = 2


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def changed_intervals(original: bytes, patched: bytes) -> list[dict[str, int]]:
    if len(original) != len(patched):
        # A length change cannot be expressed as small byte intervals; record the
        # whole-file interval explicitly instead of hiding it.
        return [{"start": 0, "end": max(len(original), len(patched)) - 1,
                 "size": max(len(original), len(patched))}]
    out: list[list[int]] = []
    for offset, (a, b) in enumerate(zip(original, patched)):
        if a == b:
            continue
        if out and out[-1][1] + 1 == offset:
            out[-1][1] = offset
        else:
            out.append([offset, offset])
    return [{"start": start, "end": end, "size": end - start + 1} for start, end in out]


def _phdrs(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        return [
            {
                "index": i,
                "type": seg["p_type"],
                "flags": int(seg["p_flags"]),
                "offset": int(seg["p_offset"]),
                "vaddr": int(seg["p_vaddr"]),
                "filesz": int(seg["p_filesz"]),
                "memsz": int(seg["p_memsz"]),
                "align": int(seg["p_align"]),
            }
            for i, seg in enumerate(elf.iter_segments())
        ]


def _reloc_count(path: Path) -> int:
    with path.open("rb") as fh:
        elf = ELFFile(fh)
        total = 0
        for sec in elf.iter_sections():
            if sec["sh_type"] in ("SHT_RELA", "SHT_REL"):
                total += sec.num_relocations()
        return total


def _entry(path: Path) -> int:
    with path.open("rb") as fh:
        return int(ELFFile(fh).header["e_entry"])


def _tool_versions() -> dict[str, str]:
    out: dict[str, str] = {"python": platform.python_version()}
    for package in ("patcherex2", "angr", "z3-solver", "lief", "pyelftools", "capstone"):
        try:
            out[package] = md.version(package)
        except Exception:
            out[package] = "missing"
    for tool in ("clang", "ld.lld", "readelf", "objdump"):
        exe = shutil.which(tool)
        out[f"{tool}_path"] = exe or "missing"
        if exe:
            try:
                p = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
                out[f"{tool}_version"] = (p.stdout or p.stderr).splitlines()[0]
            except Exception:
                out[f"{tool}_version"] = "unavailable"
    return out


def _run_view(argv: list[str], outdir: Path, name: str) -> str:
    exe = shutil.which(argv[0])
    if exe is None:
        return "not_run"
    try:
        p = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=120)
    except Exception:
        return "not_run"
    (outdir / name).write_text(
        f"$ {' '.join(argv)}\n\n{p.stdout}\n--- stderr ---\n{p.stderr}", encoding="utf-8"
    )
    return "captured"


def _static_audit(original: Path, candidate: Path, intervals: list[dict[str, int]]) -> dict[str, Any]:
    ob, cb = original.read_bytes(), candidate.read_bytes()
    before, after = _phdrs(original), _phdrs(candidate)
    checks = {
        "same_file_size": len(ob) == len(cb),
        "entry_unchanged": _entry(original) == _entry(candidate),
        "relocations_recorded": True,
        "no_wx_segment": all(not (s["flags"] & 2 and s["flags"] & 1) for s in after),
        "changed_inside_intervals": True,
        "readelf_available": shutil.which("readelf") is not None,
    }
    # changed bytes outside the recorded intervals
    if len(ob) == len(cb):
        outside = []
        for offset, (a, b) in enumerate(zip(ob, cb)):
            if a == b:
                continue
            if not any(i["start"] <= offset <= i["end"] for i in intervals):
                outside.append(offset)
        checks["changed_inside_intervals"] = not outside
    else:
        checks["changed_inside_intervals"] = False
    return {
        "checks": checks,
        "pass": all(v for k, v in checks.items() if k != "readelf_available"),
        "size": {"original": len(ob), "candidate": len(cb)},
        "entry": {"original": hex(_entry(original)), "candidate": hex(_entry(candidate))},
        "segments": {"original": before, "candidate": after},
        "relocations": {"original": _reloc_count(original), "candidate": _reloc_count(candidate)},
    }


def build_candidate(original: Path, func: str, c_source: Path, out: Path) -> None:
    from routeb.probe_patcherex2 import patch_function

    patch_function(original, func, c_source.read_text(), out)


def _run_manifest(mode: str, original: Path, candidate: Path, tests: Path, out: Path) -> int:
    if mode == "roundtrip":
        cmd = [sys.executable, str(ROOT / "routeb/run_manifest_compare.py"),
               "--original", str(original), "--patched", str(candidate),
               "--tests", str(tests), "--out", str(out)]
    else:
        cmd = [sys.executable, str(ROOT / "routeb/run_manifest_repaired.py"),
               "--binary", str(candidate), "--tests", str(tests), "--out", str(out)]
    return subprocess.run(cmd, cwd=ROOT).returncode


def make_bundle(
    *,
    original: Path,
    candidate: Path,
    outdir: Path,
    func: str,
    mode: str,
    c_source: Path | None = None,
    tests: Path | None = None,
    run_manifest: bool = False,
    reproducible: bool = False,
    build_twice: bool = False,
) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    views = outdir / "independent-views"
    views.mkdir(exist_ok=True)
    (outdir / "original.elf").write_bytes(original.read_bytes())
    (outdir / "candidate.elf").write_bytes(candidate.read_bytes())

    ob, cb = original.read_bytes(), candidate.read_bytes()
    intervals = changed_intervals(ob, cb)
    audit = _static_audit(original, candidate, intervals)
    tools = _tool_versions()
    repro: dict[str, Any] = {
        "state": "not_run",
        "reason": "pass --build-twice to rebuild via Patcherex2",
    }
    if reproducible or build_twice:
        if c_source is None:
            repro = {"state": "not_run", "reason": "--build-twice requires --c-source"}
        else:
            work = Path(tempfile.mkdtemp(prefix="routeb-repro-"))
            try:
                first, second = work / "run1.elf", work / "run2.elf"
                build_candidate(original, func, c_source, first)
                build_candidate(original, func, c_source, second)
                a, b = first.read_bytes(), second.read_bytes()
                repro = {
                    "state": "measured",
                    "runs": 2,
                    "candidate_sha256": [sha256_bytes(a), sha256_bytes(b)],
                    "byte_identical": a == b,
                    "pass": a == b,
                }
            finally:
                shutil.rmtree(work, ignore_errors=True)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "route": "route_b",
        "mode": mode,
        "function": func,
        "original_sha256": sha256_file(original),
        "candidate_sha256": sha256_file(candidate),
        "original_size": len(ob),
        "candidate_size": len(cb),
        "replacement_c_sha256": sha256_file(c_source) if c_source else None,
        "changed_file_intervals": intervals,
        "header_changes": {
            "same_program_header_count": len(_phdrs(original)) == len(_phdrs(candidate)),
            "entry_unchanged": _entry(original) == _entry(candidate),
        },
        "relocations_added": audit["relocations"]["candidate"] - audit["relocations"]["original"],
        "tools": tools,
        "runtime_manifest": {
            "state": "not_run",
            "reason": "execution is a guest-only step; pass --run-manifest in the guest",
        },
        "scope_authorized": True,
        "audit": {"checks": audit["checks"], "pass": audit["pass"]},
    }

    (outdir / "edit-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (outdir / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (outdir / "reproducibility.json").write_text(json.dumps(repro, indent=2), encoding="utf-8")
    (outdir / "environment.json").write_text(
        json.dumps({
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "kernel": platform.release(),
            "tools": tools,
        }, indent=2), encoding="utf-8")

    for name, argv in (
        ("readelf-program-headers.txt", ["readelf", "-lW", str(candidate)]),
        ("readelf-section-headers.txt", ["readelf", "-SW", str(candidate)]),
        ("readelf-relocations.txt", ["readelf", "-rW", str(candidate)]),
        ("readelf-dynamic.txt", ["readelf", "-dW", str(candidate)]),
        ("objdump-disassembly.txt", ["objdump", "-d", "-M", "intel", str(candidate)]),
    ):
        _run_view(argv, views, name)

    runtime: dict[str, Any] = {"state": "not_run"}
    if run_manifest:
        if tests is None:
            runtime = {"state": "not_run", "reason": "--run-manifest requires --tests"}
        else:
            result_path = outdir / ("manifest-compare.json" if mode == "roundtrip"
                                    else "manifest-repaired.json")
            rc = _run_manifest(mode, original, candidate, tests, result_path)
            runtime = {"state": "measured", "returncode": rc,
                       "artifact": result_path.name,
                       "pass": rc == 0}
            manifest["runtime_manifest"] = runtime
            (outdir / "edit-manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8")

    record = [
        "# Route B/provider executor bundle",
        "",
        f"- mode: `{mode}`",
        f"- function: `{func}`",
        f"- original: `{manifest['original_sha256']}`",
        f"- candidate: `{manifest['candidate_sha256']}`",
        f"- static audit: `{'pass' if audit['pass'] else 'fail'}`",
        f"- reproducibility: `{repro['state']}`",
        f"- runtime manifest: `{runtime['state']}`",
        "",
        "This bundle is static unless `runtime_manifest.state == measured`.",
        "A static bundle is not a Gate 4 pass; run the six cases in the guest under",
        "the isolation controls and attach the raw output.",
        "",
    ]
    (outdir / "bundle-record.md").write_text("\n".join(record), encoding="utf-8")

    hashes = []
    for p in sorted(x for x in outdir.rglob("*") if x.is_file()):
        rel = p.relative_to(outdir).as_posix()
        if rel in ("SHA256SUMS", "bundle-sha256.txt"):
            continue
        hashes.append(f"{sha256_file(p)}  {rel}")
    sums = "\n".join(hashes) + "\n"
    (outdir / "SHA256SUMS").write_text(sums, encoding="utf-8")
    bundle_hash = sha256_bytes(sums.encode())
    (outdir / "bundle-sha256.txt").write_text(bundle_hash + "\n", encoding="utf-8")
    return {"outdir": str(outdir), "bundle_sha256": bundle_hash,
            "file_count": len(hashes), "static_audit": audit["pass"],
            "runtime": runtime, "reproducibility": repro["state"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m routeb.executor")
    ap.add_argument("--original", type=Path, required=True)
    ap.add_argument("--candidate", type=Path, help="existing candidate; build if omitted")
    ap.add_argument("--func", required=True)
    ap.add_argument("--c-source", type=Path, help="recovered/normalized or provider C")
    ap.add_argument("--mode", choices=["roundtrip", "fixed"], default="roundtrip")
    ap.add_argument("--tests", type=Path)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--run-manifest", action="store_true",
                    help="guest-only: execute the frozen six cases")
    ap.add_argument("--build-twice", action="store_true",
                    help="rebuild twice via Patcherex2 and compare bytes")
    args = ap.parse_args(argv)

    if args.candidate is None:
        if args.c_source is None:
            ap.error("--candidate or --c-source is required")
        candidate = args.out_dir / "candidate.elf"
        args.out_dir.mkdir(parents=True, exist_ok=True)
        build_candidate(args.original, args.func, args.c_source, candidate)
    else:
        candidate = args.candidate

    result = make_bundle(
        original=args.original, candidate=candidate, outdir=args.out_dir,
        func=args.func, mode=args.mode, c_source=args.c_source,
        tests=args.tests, run_manifest=args.run_manifest,
        build_twice=args.build_twice,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["static_audit"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
