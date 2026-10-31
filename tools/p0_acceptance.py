r"""One-click P0 acceptance against the live GUI and Member 1 controller.

Checks, in order:
  1. GUI session bootstrap and controller health.
  2. Create a job through the GUI (setup -> real controller).
  3. The job page has no demo banner; controller snapshot is demo:false.
  4. Contract edit through the GUI (wrapped controller response) -> new revision.
  5. Approve the edited contract, start the run, then cancel it.
  6. The page reports the cancelled job as cancelled (not as success).

Usage:
    .\.venv\Scripts\python.exe tools\p0_acceptance.py
    .\.venv\Scripts\python.exe tools\p0_acceptance.py --gui http://127.0.0.1:8123 \
        --controller http://127.0.0.1:8000 --token <session token>
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]

FORM = {
    "defect": "strcpy in stack_buf does not check length; overlong input writes out of bounds",
    "valid_tests": "valid: hello\nboundary: 8 bytes",
    "environment": "Ubuntu x86-64 VM (P0 acceptance)",
    "valid_domain": "Input length 0-64 bytes",
    "forbidden": "Out-of-bounds write to the target buffer",
    "invalid_outcome": "Return error code 1 without out-of-bounds access",
    "assumptions": "Single process\nStack protector disabled",
}
PAYLOAD = b"\x7fELF P0 acceptance sample"


def _csrf(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"gui-csrf", hashlib.sha256).hexdigest()


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
        else:
            self.failed += 1
            print(f"FAIL  {name}" + (f"  [{detail}]" if detail else ""))

    def finish(self) -> int:
        if self.failed:
            print(f"\nP0 ACCEPTANCE FAILED ({self.failed} check(s))")
            return 1
        print("\nP0 ACCEPTANCE PASSED")
        return 0


def _job_id_from(location: str) -> str:
    return location.rstrip("/").rsplit("/", 1)[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="P0 acceptance for the GUI + controller")
    parser.add_argument("--gui", default="http://127.0.0.1:8123")
    parser.add_argument("--controller", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    token = args.token.strip()
    if not token:
        token_file = ROOT / "var" / "session.token"
        if not token_file.exists():
            print(f"FAIL  session token not found: {token_file}")
            return 1
        token = token_file.read_text(encoding="utf-8").strip()
    csrf = _csrf(token)
    checks = Checks()

    with httpx.Client(base_url=args.gui, timeout=20.0, follow_redirects=True) as gui, httpx.Client(
        base_url=args.controller, timeout=20.0
    ) as controller:
        # 1. session + controller health
        page = gui.get("/?token=" + token)
        checks.check("GUI session bootstrap", page.status_code == 200, f"HTTP {page.status_code}")

        health = controller.get("/healthz")
        body = health.json() if health.status_code == 200 else {}
        checks.check(
            "controller healthy",
            health.status_code == 200 and body.get("ok") is True,
            f"HTTP {health.status_code} {body.get('service', '')}",
        )

        # 2. create through the GUI (setup -> real controller store)
        created = gui.post(
            "/jobs",
            data={**FORM, "csrf": csrf},
            files={"binary": ("p0-acceptance.bin", PAYLOAD, "application/octet-stream")},
            follow_redirects=False,
        )
        checks.check("create job via GUI", created.status_code == 303, f"HTTP {created.status_code}")
        if created.status_code != 303:
            print(created.text[:300])
            return checks.finish()
        job_id = _job_id_from(created.headers["location"])

        snapshot = controller.get(f"/api/jobs/{job_id}").json()
        checks.check("job exists in controller", snapshot.get("id") == job_id, job_id)
        checks.check("snapshot demo=false", snapshot.get("demo") is False)
        checks.check(
            "job page has no demo banner",
            "demo-banner" not in gui.get(f"/jobs/{job_id}").text,
        )

        # 3. contract edit through the GUI (controller wraps the contract object)
        edited = gui.post(
            f"/jobs/{job_id}/contract",
            data={
                "expected_revision": "1",
                "valid_domain": "Input length 0-32 bytes",
                "forbidden": FORM["forbidden"],
                "invalid_outcome": FORM["invalid_outcome"],
                "assumptions": FORM["assumptions"],
                "csrf": csrf,
            },
            follow_redirects=False,
        )
        checks.check("contract edit accepted", edited.status_code == 303, f"HTTP {edited.status_code}")
        revised = controller.get(f"/api/jobs/{job_id}").json()
        checks.check("contract revision bumped", revised["contract"]["revision"] == 2)
        checks.check("edited contract not approved", revised["contract"]["approved"] is False)

        # 4. approve + start + cancel
        approved = gui.post(
            f"/jobs/{job_id}/approve",
            data={
                "expected_sha256": revised["contract"]["sha256"],
                "reviewer_label": "p0-acceptance",
                "csrf": csrf,
            },
            follow_redirects=False,
        )
        checks.check("contract approval accepted", approved.status_code == 303, f"HTTP {approved.status_code}")
        approved_snapshot = controller.get(f"/api/jobs/{job_id}").json()
        checks.check("contract approved in controller", approved_snapshot["contract"]["approved"] is True)

        started = gui.post(
            f"/jobs/{job_id}/start",
            data={"csrf": csrf},
            follow_redirects=False,
        )
        checks.check("run start accepted", started.status_code == 303, f"HTTP {started.status_code}")
        started_snapshot = controller.get(f"/api/jobs/{job_id}").json()
        checks.check(
            "run queued by controller",
            started_snapshot.get("phase") in {"queued", "running", "screening", "analyzing"},
            str(started_snapshot.get("phase")),
        )

        cancelled = gui.post(
            f"/jobs/{job_id}/cancel",
            data={"csrf": csrf},
            follow_redirects=False,
        )
        checks.check("cancel accepted", cancelled.status_code == 303, f"HTTP {cancelled.status_code}")
        cancelled_snapshot = controller.get(f"/api/jobs/{job_id}").json()
        checks.check("job cancelled in controller", bool(cancelled_snapshot.get("cancelled")))
        cancelled_page = gui.get(f"/jobs/{job_id}")
        checks.check("cancelled page shows 已取消", "已取消" in cancelled_page.text)
        checks.check(
            "cancelled page is not labelled success",
            "成功（声明范围内）" not in cancelled_page.text,
        )

        print(f"\njob={job_id} controller={args.controller}")
        return checks.finish()


if __name__ == "__main__":
    sys.exit(main())
