"""Connector inventory and the declarative plugin interface.

The registry is read-only. It reports what the host has configured from the
environment, the filesystem, the PATH and installed Python modules. It never
opens a provider connection, never returns a credential or absolute path, and
never executes plugin code: plugins are inert JSON manifests.

API payloads stay English ASCII (the project's data-language rule); the page
translates names, descriptions and instructions to Chinese.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from app.backend import backend_mode
from app.services.assistant_key_store import key_status

PLUGIN_DIR_ENV = "BINARYREPAIR_PLUGIN_DIR"
DEFAULT_PLUGIN_DIR = "plugins"
MCP_ENV = "BINARYREPAIR_MCP_ENDPOINT"
GIT_ENV = "BINARYREPAIR_GIT_REMOTE"
WEBHOOK_ENV = "BINARYREPAIR_WEBHOOK_URL"
STORE_ENV = "BINARYREPAIR_ARTIFACT_STORE"
DATA_DIR_ENV = "BINARYREPAIR_DATA_DIR"
GHIDRA_ENV = "BINARYREPAIR_GHIDRA_HOME"

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_STATUSES = {"connected", "ready", "unconfigured", "unavailable"}
CATEGORY_ORDER = ("model", "analysis", "workflow", "storage", "plugin")


@dataclass(frozen=True)
class Connector:
    """One integration the workbench can talk to.

    ``setup`` chooses the generic install hint rendered by the page:
    ``builtin``, ``env``, ``path``, ``binary``, ``python`` or ``dir``.
    ``hint`` is an ASCII package/command name, never an absolute path.
    """

    id: str
    name: str
    category: str
    kind: str
    description: str
    status: str
    detail: str
    env: tuple[str, ...] = ()
    docs_url: str = ""
    setup: str = "env"
    hint: str = ""


class Plugin(Protocol):
    """Python-side contract a future loader would satisfy."""

    manifest: Mapping[str, Any]

    def probe(self) -> str: ...


def _flag(env: Mapping[str, str], name: str) -> bool:
    return bool((env.get(name) or "").strip())


def _relative_data_dir(env: Mapping[str, str]) -> Path:
    return Path((env.get(DATA_DIR_ENV) or "var").strip() or "var")


def _which(*names: str) -> str:
    for name in names:
        if shutil.which(name):
            return name
    return ""


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _env_path(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def _tool(id: str, name: str, description: str, *, binary: tuple[str, ...] = (),
          module: str = "", env: tuple[str, ...] = (), env_names: tuple[str, ...] = (),
          docs_url: str = "", setup: str = "binary", hint: str = "") -> Connector:
    """Detect an analysis tool without executing it or echoing a path."""
    found = _which(*binary) if binary else ""
    if not found and module and _module(module):
        found = "python module %s" % module
    if not found:
        configured = _env_path(os.environ, *env_names) if env_names else ""
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.exists():
                found = "configured location"
    if found:
        if found.startswith("python module"):
            detail = found + " importable"
        elif found == "configured location":
            detail = "configured location detected"
        else:
            detail = "command '%s' found on PATH" % found
        status = "ready"
    else:
        status = "unavailable"
        if setup == "python" and module:
            detail = "python module '%s' not importable" % module
        elif binary:
            detail = "%s not found on PATH" % " / ".join(binary)
        else:
            detail = "not detected"
    return Connector(
        id=id, name=name, category="analysis", kind="tool",
        description=description, status=status, detail=detail,
        env=env, docs_url=docs_url, setup=setup, hint=hint,
    )


def _connectors(env: Mapping[str, str]) -> list[Connector]:
    rows: list[Connector] = []

    key = key_status()
    if key.get("configured"):
        detail = "provider key configured"
        status = "ready"
    else:
        detail = "no provider key; deterministic planner in use"
        status = "unconfigured"
    rows.append(Connector(
        id="assistant", name="Planning model", category="model", kind="llm",
        description="Generates guided plans and model-assisted code patches; without a key the deterministic planner answers.",
        status=status, detail=detail,
        env=("BINARYREPAIR_ASSISTANT_PROVIDER_KEY", "BINARYREPAIR_ASSISTANT_MODEL"),
        docs_url="https://api-docs.deepseek.com/", setup="env",
    ))

    mode = backend_mode()
    if mode == "http":
        detail = "controller adapter active; reachability is checked per request"
    elif mode == "local":
        detail = "local controller store; execution is not implemented yet"
    else:
        detail = "synthetic demo backend; no execution"
    rows.append(Connector(
        id="controller", name="Controller", category="workflow", kind="controller",
        description="Job store and snapshot source the GUI renders.",
        status="ready", detail=detail,
        env=("BINARYREPAIR_BACKEND", "BINARYREPAIR_API_BASE"),
        docs_url="https://github.com/chenfanaichifan/binary-repair", setup="builtin",
    ))

    rows.append(Connector(
        id="mcp", name="MCP bridge", category="workflow", kind="bridge",
        description="Hands tool calls to an MCP client over a declared endpoint.",
        status="ready" if _flag(env, MCP_ENV) else "unconfigured",
        detail="endpoint configured; not probed by this page" if _flag(env, MCP_ENV) else "no MCP endpoint declared",
        env=(MCP_ENV,), docs_url="https://modelcontextprotocol.io/", setup="env",
    ))

    git_ready = _flag(env, GIT_ENV) or (Path.cwd() / ".git").exists()
    rows.append(Connector(
        id="git", name="Git handoff", category="workflow", kind="delivery",
        description="Exports repair evidence into a reviewable Git work tree.",
        status="ready" if git_ready else "unconfigured",
        detail="git work tree detected" if git_ready else "no git work tree or remote declared",
        env=(GIT_ENV,), docs_url="https://git-scm.com/doc", setup="env",
    ))

    rows.append(Connector(
        id="webhook", name="Webhook", category="workflow", kind="event",
        description="Posts run and evidence events to one operator-declared URL.",
        status="ready" if _flag(env, WEBHOOK_ENV) else "unconfigured",
        detail="destination configured" if _flag(env, WEBHOOK_ENV) else "no destination declared",
        env=(WEBHOOK_ENV,), docs_url="https://webhook.site/", setup="env",
    ))

    rows.append(Connector(
        id="docker", name="Sandbox (Docker)", category="workflow", kind="runtime",
        description="Optional container boundary for running analysis tools on untrusted samples.",
        status="ready" if _which("docker") else "unavailable",
        detail="docker CLI found; daemon not probed" if _which("docker") else "docker CLI not found on PATH",
        env=(), docs_url="https://docs.docker.com/", setup="binary", hint="docker",
    ))

    data_dir = _relative_data_dir(env)
    rows.append(Connector(
        id="workspace", name="Workspace", category="storage", kind="workspace",
        description="Local data directory for job records, history and the GUI session.",
        status="ready" if data_dir.exists() else "unconfigured",
        detail="data directory present" if data_dir.exists() else "data directory missing",
        env=(DATA_DIR_ENV,), docs_url="https://github.com/chenfanaichifan/binary-repair",
        setup="path",
    ))

    store = (env.get(STORE_ENV) or "").strip()
    store_ok = bool(store) and Path(store).expanduser().exists()
    rows.append(Connector(
        id="artifacts", name="Artifact sink", category="storage", kind="storage",
        description="Optional external directory for exported evidence bundles.",
        status="ready" if store_ok else "unconfigured",
        detail="external directory present" if store_ok else "no external sink declared",
        env=(STORE_ENV,), docs_url="https://github.com/chenfanaichifan/binary-repair",
        setup="path",
    ))

    rows.append(_tool(
        "ghidra", "Ghidra", "Headless decompilation and symbol recovery for binaries under review.",
        binary=("analyzeHeadless", "ghidraRun"), env=(GHIDRA_ENV,), env_names=(GHIDRA_ENV,),
        docs_url="https://ghidra-sre.org/", setup="binary", hint="analyzeHeadless",
    ))
    rows.append(_tool(
        "rizin", "Rizin / radare2", "CLI disassembly, function recovery and binary diffing.",
        binary=("rizin", "radare2", "r2"), docs_url="https://rizin.re/", setup="binary", hint="rizin",
    ))
    rows.append(_tool(
        "binutils", "GNU binutils", "objdump/readelf evidence used by the static Route B audit.",
        binary=("objdump", "llvm-objdump", "readelf"), docs_url="https://www.gnu.org/software/binutils/",
        setup="binary", hint="objdump",
    ))
    rows.append(_tool(
        "gdb", "GDB", "Recorded-session debugging and crash inspection.",
        binary=("gdb",), docs_url="https://sourceware.org/gdb/documentation/", setup="binary", hint="gdb",
    ))
    rows.append(_tool(
        "qemu", "QEMU", "User-mode or system emulation for cross-architecture validation.",
        binary=("qemu-x86_64", "qemu-aarch64", "qemu-system-x86_64"),
        docs_url="https://www.qemu.org/docs/master/", setup="binary", hint="qemu-x86_64",
    ))
    rows.append(_tool(
        "frida", "Frida", "Dynamic instrumentation and runtime API tracing.",
        binary=("frida",), module="frida", docs_url="https://frida.re/docs/home/",
        setup="binary", hint="frida",
    ))
    rows.append(_tool(
        "angr", "angr", "Symbolic execution for path exploration and constraint solving.",
        module="angr", docs_url="https://angr.io/", setup="python", hint="angr",
    ))
    rows.append(_tool(
        "triton", "Triton", "Dynamic symbolic execution and taint analysis.",
        module="triton", docs_url="https://triton-library.github.io/", setup="python", hint="triton",
    ))

    plugin_dir = Path((env.get(PLUGIN_DIR_ENV) or DEFAULT_PLUGIN_DIR).strip() or DEFAULT_PLUGIN_DIR)
    manifests, _warnings = _read_manifests(plugin_dir)
    rows.append(Connector(
        id="plugins", name="Plugin loader", category="plugin", kind="plugin",
        description="Discovers inert JSON manifests from the plugin directory.",
        status="ready", detail="%d manifest(s) discovered" % len(manifests),
        env=(PLUGIN_DIR_ENV,), docs_url="https://github.com/chenfanaichifan/binary-repair",
        setup="dir", hint=str(plugin_dir.name),
    ))

    for row in rows:
        assert row.status in _STATUSES, row.status
        assert row.category in CATEGORY_ORDER, row.category
    return rows


def _read_manifests(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read inert JSON manifests; never import or execute them."""
    plugins: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not directory.is_dir():
        return plugins, warnings
    for path in sorted(directory.glob("*.json")):
        if not path.name.isascii():
            warnings.append("manifest filename is not ASCII")
            continue
        try:
            if path.stat().st_size > 64 * 1024:
                warnings.append("%s: manifest is too large" % path.name)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            warnings.append("%s: not readable JSON" % path.name)
            continue
        if not isinstance(data, dict):
            warnings.append("%s: manifest must be a JSON object" % path.name)
            continue
        pid = data.get("id")
        name = data.get("name")
        kind = data.get("kind")
        description = data.get("description")
        if not (isinstance(pid, str) and _PLUGIN_ID.match(pid)):
            warnings.append("%s: invalid id" % path.name)
            continue
        if not all(isinstance(value, str) and value.strip().isascii() for value in (name, kind, description)):
            warnings.append("%s: name, kind and description must be ASCII text" % path.name)
            continue
        docs_url = data.get("docs_url")
        if not (isinstance(docs_url, str) and docs_url.startswith(("https://", "http://")) and len(docs_url) <= 500):
            docs_url = ""
        plugins.append({
            "id": pid,
            "name": name.strip(),
            "kind": kind.strip(),
            "description": description.strip(),
            "enabled": bool(data.get("enabled", False)),
            "file": path.name,
            "docs_url": docs_url,
        })
    return plugins, warnings


def connector_snapshot(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the connector page/API payload without credentials or paths."""
    environ = os.environ if env is None else env
    plugin_dir = Path((environ.get(PLUGIN_DIR_ENV) or DEFAULT_PLUGIN_DIR).strip() or DEFAULT_PLUGIN_DIR)
    plugins, warnings = _read_manifests(plugin_dir)
    rows = _connectors(environ)
    ready = sum(1 for row in rows if row.status in {"ready", "connected"})
    categories: dict[str, int] = {}
    for row in rows:
        categories[row.category] = categories.get(row.category, 0) + 1
    return {
        "connectors": [asdict(row) for row in rows],
        "plugins": plugins,
        "warnings": warnings,
        "plugin_dir_source": "env" if _flag(environ, PLUGIN_DIR_ENV) else "default",
        "plugin_dir_name": plugin_dir.name,
        "counts": {
            "total": len(rows),
            "ready": ready,
            "needs_setup": len(rows) - ready,
            "plugins": len(plugins),
            "categories": categories,
        },
        "backend": backend_mode(),
        "backend_detail": backend_description_from_mode(),
    }


def backend_description_from_mode() -> str:
    """English-only backend summary that does not echo a configured path."""
    mode = backend_mode()
    if mode == "http":
        return "controller API adapter"
    if mode == "local":
        return "local controller store"
    return "synthetic demo data"


def plugin_count(env: Mapping[str, str] | None = None) -> int:
    environ = os.environ if env is None else env
    directory = Path((environ.get(PLUGIN_DIR_ENV) or DEFAULT_PLUGIN_DIR).strip() or DEFAULT_PLUGIN_DIR)
    plugins, _warnings = _read_manifests(directory)
    return len(plugins)


__all__ = [
    "Connector",
    "Plugin",
    "connector_snapshot",
    "backend_description_from_mode",
    "plugin_count",
    "PLUGIN_DIR_ENV",
    "DEFAULT_PLUGIN_DIR",
    "CATEGORY_ORDER",
]
