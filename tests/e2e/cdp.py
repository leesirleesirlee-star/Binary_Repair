"""Minimal browser automation over the Chrome DevTools Protocol (CDP).

The e2e suite needs a real browser but must not require Playwright downloads:
Microsoft Edge is already installed, so we launch it headless with
``--remote-debugging-port`` and drive the page over a WebSocket.

Only the small command subset the tests need is implemented:
navigate, JS evaluate, DOM queries, file-input injection, and reload.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_edge() -> str | None:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


class CDPSession:
    """One page target driven over the DevTools WebSocket."""

    def __init__(self, ws_url: str) -> None:
        from websockets.sync.client import connect  # websockets ships with uvicorn[standard]

        self._ws = connect(ws_url, max_size=None, open_timeout=10, legacy=True)
        self._next_id = 0

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    def send(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        message_id = self._next_id
        self._ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})

    # ---------- helpers ----------

    def navigate(self, url: str) -> None:
        self.send("Page.navigate", {"url": url})
        self.wait_js("document.readyState === 'complete'")

    def reload(self) -> None:
        self.send("Page.reload")
        self.wait_js("document.readyState === 'complete'")

    def eval_js(self, expression: str):
        result = self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")

    def wait_js(self, expression: str, timeout: float = 30.0, interval: float = 0.2):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self.eval_js(expression)
            except RuntimeError:
                last = None
            if last:
                return last
            time.sleep(interval)
        raise AssertionError(f"timed out waiting for JS expression: {expression} (last={last!r})")

    def text(self, selector: str) -> str:
        return self.eval_js(
            f"(document.querySelector({json.dumps(selector)})?.textContent || '').trim()"
        ) or ""

    def location(self) -> str:
        return self.eval_js("location.pathname") or ""

    def click(self, selector: str) -> None:
        self.eval_js(
            "(() => { const el = document.querySelector(%s);"
            " if (!el) { throw new Error('missing element: %s'); }"
            " el.click(); return true; })()" % (json.dumps(selector), selector)
        )

    def set_value(self, selector: str, value: str) -> None:
        self.eval_js(
            "(() => { const el = document.querySelector(%s);"
            " if (!el) { throw new Error('missing element: %s'); }"
            " el.value = %s;"
            " el.dispatchEvent(new Event('input', { bubbles: true }));"
            " return true; })()" % (json.dumps(selector), selector, json.dumps(value))
        )

    def set_file_input(self, selector: str, path: str) -> None:
        self.send("DOM.enable")
        document = self.send("DOM.getDocument")
        node = self.send(
            "DOM.querySelector",
            {"nodeId": document["root"]["nodeId"], "selector": selector},
        )
        if not node.get("nodeId"):
            raise AssertionError(f"file input not found: {selector}")
        self.send("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [path]})


def _wait_target(port: int, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/list"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                targets = json.loads(response.read().decode("utf-8"))
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return target["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("Edge DevTools endpoint did not become ready")


def launch_browser(edge_path: str, cdp_port: int, user_data_dir: Path) -> tuple[subprocess.Popen, CDPSession]:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            edge_path,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--disable-extensions",
            "--disable-background-networking",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={user_data_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    session = CDPSession(_wait_target(cdp_port))
    session.send("Page.enable")
    session.send("Runtime.enable")
    return process, session


def stop_browser(process: subprocess.Popen, session: CDPSession) -> None:
    session.close()
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
