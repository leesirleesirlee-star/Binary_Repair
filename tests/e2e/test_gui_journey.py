"""Browser end-to-end acceptance tests for the GUI (Technical Design section 13).

Journeys:
1. Full operator journey in demo mode: setup -> start -> comparison -> evidence -> export.
2. Refresh during a run keeps state and shows unfinished stages.
3. Cancellation produces the `cancelled` outcome and keeps evidence.
4. Local-controller mode is honest: no start action, real job persisted, `not_started`.

The suite launches headless Microsoft Edge and drives it over the DevTools
protocol, so no browser or Playwright download is required. It skips with a clear
message when Edge is not installed.
"""

from __future__ import annotations

import json
import re
import time

import httpx

from config import SESSION_TOKEN

CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")

DEFECT = "strcpy in stack_buf does not check length; overlong input writes out of bounds"
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _wait_location(page, prefix: str, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        location = page.location()
        if location.startswith(prefix):
            return location
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for location {prefix!r}; current={page.location()!r}")


def _wait_job_location(page, timeout: float = 20.0) -> str:
    """Wait for a job page: /jobs/<id> (controller ids are hex, mock ids use job-)."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        location = page.location()
        if (
            location.startswith("/jobs/")
            and location != "/jobs/new"
            and not location.rstrip("/").endswith("/evidence")
        ):
            return location
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for a job page; current={page.location()!r}")


def _approve_current_contract(page) -> None:
    """Approve the visible contract revision and wait for the page to reload."""

    page.click('[data-testid="approve-contract"]')
    _wait_job_location(page)
    page.wait_js(
        "document.querySelector('[data-testid=\"contract-approval\"]')?.textContent?.includes('已批准')",
        timeout=10,
    )


def _bootstrap(page, base_url: str) -> None:
    """Start the local session with the one-time token (then cookie-based)."""

    page.navigate(base_url + "/?token=" + SESSION_TOKEN)


def _create_job_via_ui(page, base_url: str, tmp_path) -> str:
    _bootstrap(page, base_url)
    page.navigate(base_url + "/jobs/new")
    page.wait_js("!!document.querySelector('[data-testid=\"choice-input\"]')", timeout=10)
    page.click('[data-testid="choice-input"]')
    page.wait_js(
        "!document.querySelector('[data-view=\"input\"]').classList.contains('is-hidden')",
        timeout=5,
    )
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"\x7fELF e2e sample bytes")
    page.set_file_input('[data-testid="binary-input"]', str(sample))
    page.set_value('[data-testid="defect-input"]', DEFECT)
    page.set_value('[data-testid="tests-input"]', "valid: hello\nboundary: 8 bytes")
    page.set_value('[data-testid="env-input"]', "Ubuntu x86-64 VM (demo: execution disabled)")
    page.click('[data-testid="wizard-next"]')
    page.wait_js(
        "!document.querySelector('[data-view=\"contract\"]').classList.contains('is-hidden')",
        timeout=5,
    )
    page.click('[data-testid="create-job"]')
    location = _wait_job_location(page)
    return location.rstrip("/").rsplit("/", 1)[-1]


def test_http_mode_uses_the_real_controller(http_server, page, tmp_path):
    """P0: setup submits to the real controller store; snapshot is demo:false."""
    base_url, controller_url = http_server
    _bootstrap(page, base_url)
    job_id = _create_job_via_ui(page, base_url, tmp_path)

    page.wait_js("!!document.querySelector('[data-testid=\"contract-approval\"]')", timeout=10)
    assert page.eval_js("!document.querySelector('.demo-banner')") is True

    with httpx.Client(base_url=controller_url, timeout=20.0) as client:
        snapshot = client.get(f"/api/jobs/{job_id}").json()
    assert snapshot["demo"] is False
    assert snapshot["execution"] == "not_started"
    assert snapshot["phase"] == "draft"
    assert snapshot["original_sha256"]

    # Approval works, but execution is honestly reported as not implemented.
    _approve_current_contract(page)
    page.click('[data-testid="start-run"]')
    page.wait_js(
        "document.body.textContent.includes('Execution is not implemented')",
        timeout=10,
    )


def test_landing_hero_leads_to_workbench(mock_server, page):
    _bootstrap(page, mock_server)
    page.navigate(mock_server + "/")
    page.wait_js("!!document.querySelector('.hero-h1')", timeout=10)
    assert "二进制漏洞" in page.text(".hero-h1")
    assert page.eval_js("!document.querySelector('.hero-sub') && !document.querySelector('.hero-meta')") is True

    page.click('[data-testid="landing-cta"]')
    page.wait_js("location.pathname.endsWith('/jobs/new')", timeout=10)


def test_setup_wizard_has_two_choice_pages(mock_server, page):
    _bootstrap(page, mock_server)
    page.navigate(mock_server + "/jobs/new")
    page.wait_js("!!document.querySelector('[data-testid=\"choice-input\"]')", timeout=10)

    cards = page.eval_js("document.querySelectorAll('.choice-card').length")
    assert cards == 2, cards

    # Validation: next without a file keeps the user on the sample page.
    page.click('[data-testid="choice-input"]')
    page.click('[data-testid="wizard-next"]')
    page.wait_js("!document.querySelector('[data-wizard-error]').classList.contains('is-hidden')", timeout=5)
    assert "请先选择二进制样本文件" in page.text('[data-wizard-error]')

    page.click('[data-testid="wizard-back"]')
    page.wait_js("!document.querySelector('[data-view=\"home\"]').classList.contains('is-hidden')", timeout=5)

    page.click('[data-testid="choice-contract"]')
    page.wait_js(
        "!document.querySelector('[data-view=\"contract\"]').classList.contains('is-hidden')",
        timeout=5,
    )
    page.click('[data-testid="wizard-back-contract"]')
    page.wait_js(
        "!document.querySelector('[data-view=\"input\"]').classList.contains('is-hidden')",
        timeout=5,
    )


def _stage_states(page, route: str) -> list[str]:
    return page.eval_js(
        "Array.from(document.querySelectorAll('[data-testid^=\"stage-%s-\"] .stage-state'))"
        ".map(el => el.textContent.trim())" % route
    ) or []


def test_full_journey_setup_to_export(mock_server, page, tmp_path):
    job_id = _create_job_via_ui(page, mock_server, tmp_path)

    sha = page.text('[data-testid="original-sha256"]')
    assert SHA_PATTERN.match(sha), sha

    # Interactive security visualization: vulnerability highlighting and AI candidate.
    page.wait_js("!!document.querySelector('[data-testid=\"code-view\"]')", timeout=10)
    page.click('[data-code-route="A"] .code-tab[data-view="original"]')
    page.click('[data-code-route="A"] .code-line.is-vulnerable')
    page.wait_js(
        "document.querySelector('[data-testid=\"code-detail-A\"]')?.textContent?.includes('Unbounded strcpy')",
        timeout=5,
    )
    page.click('[data-code-route="A"] .code-tab[data-view="repaired"]')
    added = page.eval_js(
        "document.querySelectorAll('[data-code-route=\"A\"] .code-panel[data-panel=\"repaired\"] .code-line.is-added').length"
    )
    assert added >= 2, added
    page.click('[data-code-route="A"] .code-tab[data-view="diff"]')
    changed = page.eval_js(
        "document.querySelectorAll('[data-code-route=\"A\"] .code-panel[data-panel=\"diff\"] .code-line.is-changed').length"
    )
    assert changed >= 1, changed
    page.click('[data-code-route="A"] .code-tab[data-view="candidate"]')
    page.wait_js(
        "document.body.textContent.includes('guard-strcpy-v1')",
        timeout=5,
    )

    _approve_current_contract(page)
    page.click('[data-testid="start-run"]')
    _wait_job_location(page)

    page.wait_js(
        "document.querySelector('[data-testid=\"outcome-A\"]')?.textContent?.includes('被拒')",
        timeout=30,
    )
    page.wait_js(
        "document.querySelector('[data-testid=\"outcome-B\"]')?.textContent?.includes('不确定')",
        timeout=30,
    )
    assert "被拒" in page.text('[data-testid="job-outcome"]')

    page.click('[data-testid="open-evidence"]')
    _wait_location(page, f"/jobs/{job_id}/evidence")
    assert "V0" in page.text("body")

    href = page.eval_js(
        "document.querySelector('[data-testid=\"export-evidence\"]')?.getAttribute('href')"
    )
    assert href and href.endswith(f"/api/jobs/{job_id}/export"), href

    with httpx.Client(base_url=mock_server, timeout=20.0) as client:
        client.get("/?token=" + SESSION_TOKEN)
        response = client.get(href)
    assert response.status_code == 200
    bundle = response.json()
    assert bundle["demo"] is True
    assert bundle["content_language"] == "en"
    assert "not production approval" in bundle["note"]
    assert not CJK.search(json.dumps(bundle, ensure_ascii=False))


def test_refresh_keeps_state_and_shows_unfinished_stages(mock_server, page, tmp_path):
    _create_job_via_ui(page, mock_server, tmp_path)
    _approve_current_contract(page)
    page.click('[data-testid="start-run"]')
    _wait_job_location(page)

    time.sleep(1.5)
    page.reload()
    page.wait_js(
        "!document.querySelector('[data-testid=\"job-phase\"]')?.textContent?.includes('草稿')",
        timeout=10,
    )

    states = _stage_states(page, "A")
    assert any(state in ("待执行", "进行中") for state in states), states

    page.wait_js(
        "document.querySelector('[data-testid=\"outcome-A\"]')?.textContent?.includes('被拒')",
        timeout=30,
    )


def test_cancel_keeps_evidence(mock_server, page, tmp_path):
    job_id = _create_job_via_ui(page, mock_server, tmp_path)
    _approve_current_contract(page)
    page.click('[data-testid="start-run"]')
    _wait_job_location(page)

    time.sleep(1.5)
    page.click('[data-testid="cancel-run"]')
    _wait_job_location(page)

    page.wait_js(
        "document.querySelector('[data-testid=\"outcome-A\"]')?.textContent?.includes('已取消')",
        timeout=10,
    )
    page.wait_js(
        "document.querySelector('[data-testid=\"outcome-B\"]')?.textContent?.includes('已取消')",
        timeout=10,
    )

    page.click('[data-testid="open-evidence"]')
    _wait_location(page, f"/jobs/{job_id}/evidence")
    assert "V0" in page.text("body")


def test_local_mode_is_honest(local_server, page, tmp_path):
    base_url, data_dir = local_server
    _bootstrap(page, base_url)
    page.navigate(base_url + "/jobs/new")
    assert "local controller" in page.text("body")

    job_id = _create_job_via_ui(page, base_url, tmp_path)
    page.wait_js(
        "!!document.querySelector('[data-testid=\"start-unavailable\"]')", timeout=10
    )
    assert "尚未提供修复执行接口" in page.text('[data-testid="start-unavailable"]')

    with httpx.Client(base_url=base_url, timeout=20.0) as client:
        client.get("/?token=" + SESSION_TOKEN)
        status = client.get(f"/api/jobs/{job_id}").json()
    assert status["demo"] is False
    assert status["execution"] == "not_started"
    assert not CJK.search(json.dumps(status, ensure_ascii=False))

    assert (data_dir / "app.db").exists()
    assert (data_dir / "jobs" / job_id / "original.bin").exists()
