"""BinaryRepair MVP GUI (server-rendered FastAPI app).

Demo mode:
    .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123
Real controller mode: set BINARYREPAIR_BACKEND=http and
BINARYREPAIR_API_BASE=http://127.0.0.1:8000 before starting uvicorn."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import i18n, status
from app.backend import (
    BackendError,
    backend_description,
    backend_mode,
    get_backend,
)

BASE_DIR = Path(__file__).resolve().parent

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

def _load_session_token() -> str:
    """Return the persisted local session token (env override wins).

    The token is written to ``var/session.token`` once and reused across restarts,
    so the browser cookie stays valid and users do not have to re-login every time.
    """
    configured = os.environ.get("BINARYREPAIR_SESSION_TOKEN")
    if configured:
        return configured
    token_file = BASE_DIR.parent / "var" / "session.token"
    try:
        if token_file.exists():
            stored = token_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        token = secrets.token_urlsafe(32)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
        return token
    except OSError:
        return secrets.token_urlsafe(32)


SESSION_TOKEN = _load_session_token()
CSRF_TOKEN = hmac.new(SESSION_TOKEN.encode("utf-8"), b"gui-csrf", hashlib.sha256).hexdigest()
SESSION_COOKIE = "br_session"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

app = FastAPI(title="BinaryRepair MVP - GUI slice")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

templates.env.globals.update(
    phase_labels_zh=i18n.PHASE_LABELS_ZH,
    stage_labels_zh=i18n.STAGE_LABELS_ZH,
    outcome_labels_zh=i18n.OUTCOME_LABELS_ZH,
    state_labels_zh=i18n.STATE_LABELS_ZH,
    value_labels_zh=i18n.VALUE_LABELS_ZH,
    route_names_zh=i18n.ROUTE_NAMES_ZH,
    csrf_token=CSRF_TOKEN,
    display_verdict=status.display_verdict,
    order_obligations=status.order_obligations,
)

BACKEND = get_backend()
BACKEND_MODE = backend_mode()


def _ascii_filename(name: str) -> str:
    """Sanitize an uploaded filename to an ASCII safe name."""
    base = Path(name or "").name
    if base and base.isascii() and _UNSAFE_FILENAME_CHARS.search(base) is None:
        return base
    stem, dot, suffix = base.rpartition(".")
    stem_clean = _UNSAFE_FILENAME_CHARS.sub("_", stem).strip("_-") if stem else ""
    suffix_clean = _UNSAFE_FILENAME_CHARS.sub("", suffix) if dot else ""
    if not stem_clean:
        stem_clean = "uploaded-sample"
    return f"{stem_clean}.{suffix_clean}" if suffix_clean else stem_clean


def _require_english(field: str, value: str) -> None:
    """Reject non-ASCII upload text with 422."""
    if value and any(ord(char) > 127 for char in value):
        raise HTTPException(
            status_code=422,
            detail=f"Field '{field}' must use English/ASCII characters only (no non-ASCII characters).",
        )


@app.exception_handler(BackendError)
async def backend_error_handler(request: Request, exc: BackendError) -> JSONResponse:
    """Render backend failures as English JSON errors."""
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.middleware("http")
async def security_gate(request: Request, call_next):
    """Enforce the local-only session/CSRF security boundary."""
    host = (request.headers.get("host") or "").split(":")[0].strip().lower()
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Local access only (host check failed)."}, status_code=403)

    path = request.url.path
    if path.startswith("/static/") or path in {"/healthz", "/favicon.ico"}:
        return await call_next(request)

    token = request.query_params.get("token")
    if token and secrets.compare_digest(token, SESSION_TOKEN):
        response = RedirectResponse(url=path or "/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            SESSION_TOKEN,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    session = request.cookies.get(SESSION_COOKIE)
    if not session or not secrets.compare_digest(session, SESSION_TOKEN):
        if request.method == "GET" and "text/html" in (request.headers.get("accept") or ""):
            return HTMLResponse(_auth_required_page(SESSION_TOKEN), status_code=401)
        return JSONResponse(
            {
                "detail": (
                    "Authentication required. Open the launcher URL containing ?token=... "
                    "to start the local session."
                )
            },
            status_code=401,
        )

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != "http" or (parsed.hostname or "") not in ALLOWED_HOSTS:
                return JSONResponse(
                    {"detail": "Cross-origin request rejected."}, status_code=403
                )

    return await call_next(request)


def _require_csrf(token: str) -> None:
    """Validate the CSRF token rendered into every POST form."""

    if not token or not secrets.compare_digest(str(token), CSRF_TOKEN):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")


def _auth_required_page(session_token: str) -> str:
    """Friendly 401 page for humans: one click restores the local session."""

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>需要本地会话 · BinaryRepair</title>
<style>
 body{margin:0;background:#f3ede1;color:#2c2925;font-family:"Trebuchet MS","Segoe UI","Microsoft YaHei",sans-serif;
      background-image:radial-gradient(900px 520px at 15% -5%,rgba(168,100,47,.08),transparent 60%);
      display:flex;align-items:center;justify-content:center;min-height:100vh}
 .card{max-width:600px;padding:30px 34px;background:#faf6ec;border:1.5px dashed #2c2925;border-radius:24px 18px 26px 16px;
       box-shadow:6px 7px 0 rgba(44,41,37,.12)}
 h1{font-family:Georgia,"Songti SC","SimSun",serif;font-size:24px;margin:0 0 10px;color:#2c2925}
 p{font-size:14px;line-height:1.75;color:#4a453d;margin:8px 0}
 code{font-family:"Cascadia Mono",Consolas,monospace;background:#eee6d6;color:#6b4a2c;padding:2px 6px;border-radius:4px}
 .btn{display:inline-flex;align-items:center;justify-content:center;margin:18px 0 6px;padding:12px 26px;
      border-radius:999px;background:#2c2925;color:#faf6ec;border:1.5px solid #2c2925;
      box-shadow:4px 5px 0 rgba(44,41,37,.18);font-weight:700;font-size:14px;text-decoration:none}
 .btn:hover{transform:translateY(-2px)}
 .steps{margin:16px 0 0;padding-left:18px;font-size:13px;color:#4a453d}
 .steps li{margin:6px 0}
 .hint{margin-top:14px;font-size:12px;color:#8a7f6e}
</style></head>
<body><div class="card">
<h1>需要本地会话</h1>
<p>这是本机工作台的访问保护。<strong>点下面的按钮即可登录</strong>，或者双击项目里的 <code>open.bat</code>。</p>
<a class="btn" href="/?token=__TOKEN__">一键登录本机工作台 →</a>
<ol class="steps">
  <li>登录后 token 会从地址栏消失，浏览器会记住会话；</li>
  <li>token 保存在 <code>var/session.token</code>，重启服务不会改变；</li>
  <li>也可以从启动脚本打印的 <code>/?token=...</code> 地址进入。</li>
</ol>
<p class="hint">不要把带 token 的地址发到公开位置；本页面只在本机可访问。</p>
</div></body></html>""".replace("__TOKEN__", session_token)


@app.get("/", response_class=HTMLResponse, name="landing_page")
async def index(request: Request):
    """Render the landing page."""
    return templates.TemplateResponse(
        request,
        "landing.html",
        {"backend_note": backend_description(BACKEND)},
    )


@app.get("/jobs/new", response_class=HTMLResponse, name="setup_page")
async def setup_page(request: Request):
    """Render setup/review (upload + contract)."""
    return templates.TemplateResponse(
        request,
        "setup.html",
        {"demo": BACKEND_MODE == "mock", "job": None, "backend_note": backend_description(BACKEND)},
    )


@app.post("/jobs")
async def create_job(
    binary: UploadFile = File(...),
    defect: str = Form(...),
    valid_tests: str = Form(""),
    environment: str = Form(""),
    valid_domain: str = Form(""),
    forbidden: str = Form(""),
    invalid_outcome: str = Form(""),
    assumptions: str = Form(""),
    csrf: str = Form(""),
):
    """Create a job from the upload form."""
    _require_csrf(csrf)
    raw = await binary.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the 20 MiB limit.")
    if not raw:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    for field_name, field_value in (
        ("defect", defect),
        ("valid_tests", valid_tests),
        ("environment", environment),
        ("valid_domain", valid_domain),
        ("forbidden", forbidden),
        ("invalid_outcome", invalid_outcome),
        ("assumptions", assumptions),
    ):
        _require_english(field_name, field_value)

    job_id = BACKEND.create_job(
        binary_bytes=raw,
        filename=_ascii_filename(binary.filename or "sample.bin"),
        defect=defect,
        valid_tests=valid_tests,
        environment=environment,
        valid_domain=valid_domain,
        forbidden=forbidden,
        invalid_outcome=invalid_outcome,
        assumptions=assumptions,
    )
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


def _comparison_response(
    request: Request,
    job_id: str,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    """Render the comparison screen (also used for explicit 409 conflict errors)."""
    snapshot = BACKEND.get_snapshot(job_id)
    try:
        code_visuals = BACKEND.get_visuals(job_id)
    except BackendError:
        code_visuals = {"available": False, "note": "Code view is unavailable from the controller."}
    return templates.TemplateResponse(
        request,
        "comparison.html",
        {
            "demo": bool(snapshot.get("demo")),
            "job": snapshot,
            "visuals": code_visuals,
            "can_start": BACKEND.supports_start,
            "backend_note": backend_description(BACKEND),
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def comparison_page(request: Request, job_id: str):
    """Render progress/comparison for both routes."""
    return _comparison_response(request, job_id)


@app.post("/jobs/{job_id}/contract")
async def update_contract(
    request: Request,
    job_id: str,
    expected_revision: str = Form(""),
    valid_domain: str = Form(""),
    forbidden: str = Form(""),
    invalid_outcome: str = Form(""),
    assumptions: str = Form(""),
    csrf: str = Form(""),
):
    """Create a new contract revision; a stale ``expected_revision`` returns 409."""

    _require_csrf(csrf)
    for field_name, field_value in (
        ("valid_domain", valid_domain),
        ("forbidden", forbidden),
        ("invalid_outcome", invalid_outcome),
        ("assumptions", assumptions),
    ):
        _require_english(field_name, field_value)
    try:
        BACKEND.update_contract(
            job_id,
            expected_revision=expected_revision,
            valid_domain=valid_domain,
            forbidden=forbidden,
            invalid_outcome=invalid_outcome,
            assumptions=assumptions,
        )
    except BackendError as exc:
        return _comparison_response(request, job_id, error=exc.detail, status_code=exc.status_code)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/approve")
async def approve_contract(
    request: Request,
    job_id: str,
    expected_sha256: str = Form(""),
    reviewer_label: str = Form(""),
    csrf: str = Form(""),
):
    """Approve the current contract revision; a stale hash returns 409."""

    _require_csrf(csrf)
    _require_english("reviewer_label", reviewer_label)
    try:
        BACKEND.approve_contract(
            job_id,
            expected_sha256=expected_sha256,
            reviewer_label=reviewer_label,
        )
    except BackendError as exc:
        return _comparison_response(request, job_id, error=exc.detail, status_code=exc.status_code)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/start")
async def start_run(request: Request, job_id: str, csrf: str = Form("")):
    """Start a run (demo advance or controller request)."""
    _require_csrf(csrf)
    try:
        BACKEND.start(job_id)
    except BackendError as exc:
        return _comparison_response(request, job_id, error=exc.detail, status_code=exc.status_code)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/cancel")
async def cancel_run(request: Request, job_id: str, csrf: str = Form("")):
    """Cancel the current run."""
    _require_csrf(csrf)
    try:
        BACKEND.cancel(job_id)
    except BackendError as exc:
        return _comparison_response(request, job_id, error=exc.detail, status_code=exc.status_code)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/dryrun/route-a", response_class=HTMLResponse)
async def dryrun_route_a(request: Request):
    """Route A interface preview: render host dry-run artifacts only.

    The UI intentionally never derives an outcome here; it renders the raw
    V0-V5 statuses and, when present, the acceptance engine's decision.json.
    """
    base = BASE_DIR / "interfaces" / "route-a"
    bundle = json.loads((base / "result-bundle-DRYRUN.json").read_text(encoding="utf-8"))
    status = json.loads((base / "status-DRYRUN.json").read_text(encoding="utf-8"))
    decision_path = base / "decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8")) if decision_path.exists() else None
    return templates.TemplateResponse(
        request,
        "dryrun.html",
        {"demo": False, "backend_note": "host dry-run fixtures", "bundle": bundle, "status": status, "decision": decision},
    )


@app.get("/jobs/{job_id}/evidence", response_class=HTMLResponse)
async def evidence_page(request: Request, job_id: str):
    """Render evidence/export for both routes."""
    snapshot = BACKEND.get_snapshot(job_id)
    obligations: dict[str, list[dict]] = {}
    for key, route in (snapshot.get("routes") or {}).items():
        group = route.get("obligations")
        if group:
            obligations[key] = group
    if len(obligations) < len(snapshot.get("routes") or {}):
        for key, group in BACKEND.get_obligations(job_id).items():
            obligations.setdefault(key, group)
    return templates.TemplateResponse(
        request,
        "evidence.html",
        {
            "demo": bool(snapshot.get("demo")),
            "job": snapshot,
            "obligations": obligations,
            "artifacts": BACKEND.get_artifacts(job_id),
            "backend_note": backend_description(BACKEND),
        },
    )


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    """Return the polling status JSON."""
    return JSONResponse(BACKEND.get_snapshot(job_id))


@app.get("/api/jobs/{job_id}/export")
async def export_bundle(job_id: str):
    """Export JSON v2 evidence (English ASCII; not production approval)."""
    try:
        bundle = BACKEND.export_bundle(job_id)
    except BackendError as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    job_id_for_file = str(bundle.get("job", {}).get("id") or job_id)
    body = json.dumps(bundle, ensure_ascii=True, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{job_id_for_file}-evidence.json"',
        },
    )


@app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
async def evidence_report(request: Request, job_id: str, download: int = 0):
    """Render the printable English evidence report (same data as JSON v2)."""
    try:
        bundle = BACKEND.export_bundle(job_id)
    except BackendError as exc:
        return HTMLResponse(
            f"<h1>Report unavailable</h1><p>{html.escape(exc.detail)}</p>",
            status_code=exc.status_code,
        )
    sha_lines = [f"{bundle['manifest_sha256']}  manifest.json"]
    for artifact in bundle.get("artifacts", []):
        if artifact.get("sha256"):
            sha_lines.append(f"{artifact['sha256']}  {artifact.get('name')}")
    headers = None
    if download:
        headers = {"Content-Disposition": f'attachment; filename="{job_id}-evidence.html"'}
    return templates.TemplateResponse(
        request,
        "report.html",
        {"bundle": bundle, "sha_lines": sha_lines},
        headers=headers,
    )


@app.get("/healthz")
async def healthz():
    # mock is synthetic; local is the real controller store; http trusts the
    # controller snapshot, so the demo flag is unknown until a job is fetched.
    demo = {"mock": True, "local": False}.get(BACKEND_MODE)
    return {
        "ok": True,
        "backend": BACKEND_MODE,
        "backend_detail": backend_description(BACKEND),
        "demo": demo,
    }
