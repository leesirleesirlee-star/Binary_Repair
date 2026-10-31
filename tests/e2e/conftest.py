"""Shared fixtures for the browser end-to-end suite (Edge + DevTools protocol).

Each test drives the real FastAPI app in a subprocess and a real headless Edge
browser, so the acceptance evidence mirrors what an operator does:
setup -> start -> compare -> evidence -> export.

No Playwright or browser download is required: Microsoft Edge is already on the
machine and is driven over the DevTools protocol.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cdp import find_edge, launch_browser, stop_browser
from config import SESSION_TOKEN

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(url: str, process: subprocess.Popen, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("GUI server exited before becoming ready")
        try:
            urllib.request.urlopen(url + "/healthz", timeout=1)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.2)
    process.terminate()
    raise RuntimeError("GUI server did not become ready in time")


def start_app(module: str, env_extra: dict[str, str]) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    env = os.environ.copy()
    env.update(env_extra)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            module,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_ready(base_url, process)
    return process, base_url


def start_gui(env_extra: dict[str, str]) -> tuple[subprocess.Popen, str]:
    return start_app("app.main:app", env_extra)


def stop_gui(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="session")
def mock_server():
    process, base_url = start_gui(
        {"BINARYREPAIR_BACKEND": "mock", "BINARYREPAIR_SESSION_TOKEN": SESSION_TOKEN}
    )
    try:
        yield base_url
    finally:
        stop_gui(process)


@pytest.fixture()
def local_server(tmp_path):
    process, base_url = start_gui(
        {
            "BINARYREPAIR_BACKEND": "local",
            "BINARYREPAIR_DATA_DIR": str(tmp_path),
            "BINARYREPAIR_SESSION_TOKEN": SESSION_TOKEN,
        }
    )
    try:
        yield base_url, tmp_path
    finally:
        stop_gui(process)


@pytest.fixture()
def controller_server(tmp_path):
    """Real-store controller bridge (demo:false, execution not implemented)."""
    data_dir = tmp_path / "controller-data"
    process, base_url = start_app(
        "tools.controller_service:app",
        {"BINARYREPAIR_CONTROLLER_DATA": str(data_dir)},
    )
    try:
        yield base_url, data_dir
    finally:
        stop_gui(process)


@pytest.fixture()
def http_server(controller_server):
    """GUI in http mode pointed at the real-store controller bridge."""
    controller_url, _data_dir = controller_server
    process, base_url = start_gui(
        {
            "BINARYREPAIR_BACKEND": "http",
            "BINARYREPAIR_API_BASE": controller_url,
            "BINARYREPAIR_SESSION_TOKEN": SESSION_TOKEN,
        }
    )
    try:
        yield base_url, controller_url
    finally:
        stop_gui(process)


@pytest.fixture()
def page(tmp_path):
    edge_path = find_edge()
    if edge_path is None:
        pytest.skip("Microsoft Edge is not installed; browser e2e cannot run")
    process, session = launch_browser(edge_path, _free_port(), tmp_path / "edge-profile")
    try:
        yield session
    finally:
        stop_browser(process, session)
