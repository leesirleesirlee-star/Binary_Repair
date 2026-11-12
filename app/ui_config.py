"""Workspace and mode configuration for the GUI.

This is the workbench's own configuration, not a controller API. The landing
page already renders from this shape, so connecting a real backend later only
means pointing ``load_ui_config`` at that source; templates and JavaScript stay
untouched.

Source priority:
1. ``BINARYREPAIR_UI_CONFIG`` -> path to a JSON file with the same shape.
2. Built-in defaults below.

A malformed file never breaks the page: it falls back to the defaults and
reports ``source="builtin"`` so the UI can say so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "workspaces": [
        {
            "id": "local",
            "label": "默认工作区",
            "detail": "仅绑定本机回环地址，不对外暴露。",
            "base_url": "http://127.0.0.1:8011",
        },
        {
            "id": "local-loopback",
            "label": "本地工作区 · 127.0.0.1",
            "detail": "本机回环，不对外暴露。",
            "base_url": "http://127.0.0.1:8011",
        },
    ],
    "modes": [
        {"id": "standard", "label": "标准模式", "detail": "对话式建单"},
        {"id": "demo", "label": "演示模式", "detail": "fixture 数据"},
    ],
    "defaults": {"workspace": "local", "mode": "standard"},
}


def _valid_entry(entry: Any, kind: str) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and entry["id"]
        and isinstance(entry.get("label"), str)
        and entry["label"]
        and (kind == "workspaces" or "detail" in entry)
    )


def _normalise(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    workspaces = raw.get("workspaces")
    modes = raw.get("modes")
    if not isinstance(workspaces, list) or not workspaces:
        return None
    if not isinstance(modes, list) or not modes:
        return None
    if not all(_valid_entry(item, "workspaces") for item in workspaces):
        return None
    if not all(_valid_entry(item, "modes") for item in modes):
        return None

    ids = {item["id"] for item in workspaces}
    mode_ids = {item["id"] for item in modes}
    defaults = raw.get("defaults") or {}
    workspace = defaults.get("workspace") if isinstance(defaults, dict) else None
    mode = defaults.get("mode") if isinstance(defaults, dict) else None

    return {
        "workspaces": workspaces,
        "modes": modes,
        "defaults": {
            "workspace": workspace if workspace in ids else workspaces[0]["id"],
            "mode": mode if mode in mode_ids else modes[0]["id"],
        },
    }


def load_ui_config(source: str | None = None) -> dict[str, Any]:
    """Return ``{workspaces, modes, defaults, source}``."""
    path = source if source is not None else os.environ.get("BINARYREPAIR_UI_CONFIG", "")
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            config = _normalise(data)
            if config is not None:
                config["source"] = f"file:{path}"
                return config
        except (OSError, ValueError):
            pass
    config = _normalise(DEFAULTS)
    assert config is not None
    config["source"] = "builtin"
    return config


def resolve_workspace(config: dict[str, Any], workspace_id: str | None) -> dict[str, Any]:
    for item in config["workspaces"]:
        if item["id"] == workspace_id:
            return item
    for item in config["workspaces"]:
        if item["id"] == config["defaults"]["workspace"]:
            return item
    return config["workspaces"][0]


def resolve_mode(config: dict[str, Any], mode_id: str | None) -> dict[str, Any]:
    for item in config["modes"]:
        if item["id"] == mode_id:
            return item
    for item in config["modes"]:
        if item["id"] == config["defaults"]["mode"]:
            return item
    return config["modes"][0]
