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
from urllib.parse import urlencode, urlparse

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
from app.evidence_domains import evidence_domain, scope_display
from app.services.assistant_key_store import clear_key, key_status, save_key
from app.services.code_repair import repair_code
from app.services.connectors import connector_snapshot
from app.services.history import HistoryConflict, HistoryError, HistoryStore
from app.services.evaluation import evaluate as evaluate_evidence
from app.services.assistant_llm import (
    ASSISTANT_MODE_ENV,
    assistant_status,
    client_from_env,
    test_provider_connection,
)
from app.ui_config import load_ui_config, resolve_mode, resolve_workspace

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
for _extra_host in os.environ.get("BINARYREPAIR_ALLOWED_HOSTS", "").split(","):
    if _extra_host.strip():
        ALLOWED_HOSTS.add(_extra_host.strip().lower())


def _host_allowed(host: str) -> bool:
    """Local-only by default; explicit env opts in LAN/temporary tunnel hosts."""
    host = (host or "").split(":")[0].strip().lower()
    if host in ALLOWED_HOSTS:
        return True
    if os.environ.get("BINARYREPAIR_ALLOW_TUNNEL") == "1":
        return any(host.endswith(suffix) for suffix in (".ngrok-free.app", ".ngrok-free.dev", ".ngrok.app", ".ngrok.dev", ".ngrok.io"))
    return False

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
    nav_ui=load_ui_config,
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
    if not _host_allowed(host):
        return JSONResponse({"detail": "Local access only (host check failed)."}, status_code=403)

    path = request.url.path
    if path.startswith("/static/") or path in {"/healthz", "/favicon.ico"}:
        return await call_next(request)

    token = request.query_params.get("token")
    if token and secrets.compare_digest(token, SESSION_TOKEN):
        # Strip the token from the URL but keep every other query parameter
        # (workspace / mode / q / new / fixture), otherwise a deep link would
        # silently lose its intent on the one redirect after bootstrap.
        kept = [(key, value) for key, value in request.query_params.multi_items() if key != "token"]
        target = path or "/"
        if kept:
            target += "?" + urlencode(kept)
        response = RedirectResponse(url=target, status_code=303)
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
            return HTMLResponse(_auth_required_page(), status_code=401)
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
            if parsed.scheme not in {"http", "https"} or not _host_allowed(parsed.hostname or ""):
                return JSONResponse(
                    {"detail": "Cross-origin request rejected."}, status_code=403
                )

    return await call_next(request)


def _require_csrf(token: str) -> None:
    """Validate the CSRF token rendered into every POST form."""

    if not token or not secrets.compare_digest(str(token), CSRF_TOKEN):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")


def _auth_required_page() -> str:
    """Friendly 401 page for humans.

    It deliberately carries no session token. An unauthenticated response must
    never hand out the credential that unlocks the workspace; the trusted
    launcher prints the one-time login address to its own console instead.
    """

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
 .steps{margin:16px 0 0;padding-left:18px;font-size:13px;color:#4a453d}
 .steps li{margin:6px 0}
 .hint{margin-top:14px;font-size:12px;color:#8a7f6e}
</style></head>
<body><div class="card">
<h1>需要本地会话</h1>
<p>这是本机工作台的访问保护。当前请求没有有效会话，<strong>所以这个页面不会显示登录令牌</strong>。</p>
<ol class="steps">
  <li>双击项目里的 <code>open.bat</code>，或运行 <code>start.ps1</code>；</li>
  <li>使用启动脚本控制台里打印的登录地址进入（令牌只在那一条地址里）；</li>
  <li>令牌保存在本机 <code>var/session.token</code>，只应复制到你自己的浏览器。</li>
</ol>
<p class="hint">不要把登录地址发到公开位置。服务只监听本机回环地址，重启服务不会改变令牌。</p>
</div></body></html>"""


@app.get("/", response_class=HTMLResponse, name="landing_page")
async def index(request: Request):
    """Render the landing page."""
    ui = load_ui_config()
    snapshot = connector_snapshot()
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "backend_note": backend_description(BACKEND),
            "ui": ui,
            "default_workspace": resolve_workspace(ui, None),
            "default_mode": resolve_mode(ui, None),
            "connector_strip": [
                row for row in snapshot["connectors"]
                if row["id"] in {"assistant", "controller", "ghidra", "angr", "frida", "plugins"}
            ],
            "connector_names": CONNECTOR_NAMES_ZH,
            "connector_status": CONNECTOR_STATUS_ZH,
        },
    )


@app.get("/api/ui-config", name="ui_config")
async def ui_config():
    """Expose the workspace/mode catalogue the landing page renders from.

    This is the GUI's own configuration. When a real backend owns it, only
    ``app.ui_config.load_ui_config`` changes; the response shape stays.
    """
    return JSONResponse(load_ui_config())


def _fixture_int(value: str) -> int:
    try:
        return int((value or "0").strip() or "0")
    except (TypeError, ValueError):
        return 0


def _agent_context(
    *,
    job_id: str = "",
    fixture: int = 0,
    workspace: str = "",
    mode: str = "",
    job: dict[str, str] | None = None,
) -> dict:
    """Shared template context for the workbench routes.

    ``assistant`` is the GUI's view of the T8.2 seam. It is deliberately
    conservative: llm mode without a configured client reports
    ``available=false`` so the badge says "未接线" instead of implying a call
    happened.
    """
    ui = load_ui_config()
    selected_workspace = resolve_workspace(ui, workspace or None)
    selected_mode = resolve_mode(ui, mode or None)
    fixture_mode = bool(fixture) or selected_mode["id"] == "demo"
    return {
        "demo": BACKEND_MODE == "mock" or fixture_mode,
        "job": job,
        "job_id": job_id,
        "fixture": fixture_mode,
        "backend_note": backend_description(BACKEND),
        "ui": ui,
        "workspace": selected_workspace,
        "mode": selected_mode,
        "assistant": assistant_status(
            mode=os.environ.get(ASSISTANT_MODE_ENV),
            client_configured=client_from_env() is not None,
        ),
    }


CONNECTOR_NAMES_ZH = {
    "assistant": "计划模型",
    "controller": "控制器",
    "workspace": "工作区",
    "plugins": "插件加载器",
    "mcp": "MCP 桥",
    "git": "Git 交付",
    "webhook": "Webhook",
    "artifacts": "产物归档",
    "ghidra": "Ghidra",
    "rizin": "Rizin / radare2",
    "binutils": "GNU binutils",
    "gdb": "GDB",
    "qemu": "QEMU",
    "frida": "Frida",
    "angr": "angr",
    "triton": "Triton",
    "docker": "沙箱（Docker）",
}
CONNECTOR_DESCS_ZH = {
    "assistant": "生成引导计划与模型补丁；没有 key 时使用确定性 planner。",
    "controller": "GUI 读取任务快照、历史与契约的控制器来源。",
    "workspace": "任务记录、服务端会话历史与本地数据的目录。",
    "plugins": "从插件目录发现只读 JSON 清单，不执行插件代码。",
    "mcp": "把工具调用交给 MCP 客户端的可选桥接点。",
    "git": "把修复证据交付到可审阅的 Git 工作树。",
    "webhook": "将运行与证据事件推送到操作者声明的地址。",
    "artifacts": "导出证据包的可选外部目录。",
    "ghidra": "无头反编译与符号恢复，为二进制修复提供伪代码。",
    "rizin": "反汇编、函数恢复与二进制差分。",
    "binutils": "objdump/readelf 静态证据，Route B 自检依赖它。",
    "gdb": "会话调试与崩溃现场检查。",
    "qemu": "跨架构用户态或系统级模拟，用于复跑与验证。",
    "frida": "动态插桩与运行时 API 追踪。",
    "angr": "符号执行与路径约束求解，用于探索可触发输入。",
    "triton": "动态符号执行与污点分析。",
    "docker": "在容器边界内运行不可信样本与工具。",
}
CONNECTOR_CATEGORY_ZH = {
    "model": "模型与规划",
    "analysis": "二进制分析",
    "workflow": "工作流与交付",
    "storage": "存储与运行时",
    "plugin": "插件",
}
CONNECTOR_CATEGORY_DESC_ZH = {
    "model": "计划生成与补丁建议；密钥只保存在服务端。",
    "analysis": "从静态、符号、动态到模拟：修复前后都需要可复现证据。",
    "workflow": "把修复接入任务、审阅、事件与沙箱边界。",
    "storage": "任务、历史、证据包与产物的落点。",
    "plugin": "声明式清单，只被发现与展示，不被执行。",
}
CONNECTOR_STATUS_ZH = {
    "connected": "已连接",
    "ready": "已就绪",
    "unconfigured": "未配置",
    "unavailable": "未检测到",
}
CONNECTOR_KIND_ZH = {
    "llm": "模型",
    "controller": "控制器",
    "bridge": "桥接",
    "delivery": "交付",
    "event": "事件",
    "runtime": "运行时",
    "storage": "存储",
    "workspace": "工作区",
    "tool": "分析工具",
    "plugin": "插件",
}


@app.get("/settings", response_class=HTMLResponse, name="settings_page")
async def settings_page(request: Request):
    """Local operator preferences; values stay in the browser."""
    ui = load_ui_config()
    snapshot = connector_snapshot()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "ui": ui,
            "workspace": resolve_workspace(ui, None),
            "mode": resolve_mode(ui, None),
            "connector_counts": snapshot["counts"],
            "plugin_total": snapshot["counts"]["plugins"],
            "demo": BACKEND_MODE == "mock",
        },
    )


@app.get("/guide", response_class=HTMLResponse, name="guide_page")
async def guide_page(request: Request):
    """Full operator manual, linked from the fixed sidebar of every page."""
    return templates.TemplateResponse(
        request,
        "guide.html",
        {"demo": BACKEND_MODE == "mock"},
    )


@app.get("/methods", response_class=HTMLResponse, name="methods_page")
async def methods_page(request: Request):
    """Research-backed method map for binary repair and verification."""
    return templates.TemplateResponse(
        request,
        "methods.html",
        {"demo": BACKEND_MODE == "mock"},
    )


@app.get("/connectors", response_class=HTMLResponse, name="connectors_page")
async def connectors_page(request: Request):
    """Render the read-only connector inventory and the separate plugin area."""
    snapshot = connector_snapshot()
    loader = next((row for row in snapshot["connectors"] if row["id"] == "plugins"), None)
    sections = []
    for key in ("model", "analysis", "workflow", "storage"):
        rows = [
            row for row in snapshot["connectors"]
            if row["category"] == key and row["id"] != "plugins"
        ]
        if rows:
            sections.append({
                "key": key,
                "label": CONNECTOR_CATEGORY_ZH[key],
                "desc": CONNECTOR_CATEGORY_DESC_ZH[key],
                "rows": rows,
            })
    return templates.TemplateResponse(
        request,
        "connectors.html",
        {
            "snapshot": snapshot,
            "sections": sections,
            "loader": loader,
            "names": CONNECTOR_NAMES_ZH,
            "descs": CONNECTOR_DESCS_ZH,
            "statuses": CONNECTOR_STATUS_ZH,
            "categories": CONNECTOR_CATEGORY_ZH,
            "category_desc": CONNECTOR_CATEGORY_DESC_ZH,
            "kinds": CONNECTOR_KIND_ZH,
            "demo": BACKEND_MODE == "mock",
        },
    )


@app.get("/api/connectors", name="connectors_api")
async def connectors_api():
    """Expose the connector inventory as English ASCII JSON."""
    return JSONResponse(connector_snapshot())


@app.get("/agent", response_class=HTMLResponse, name="agent_index")
async def agent_index(
    request: Request,
    job: str = "",
    fixture: str = "",
    workspace: str = "",
    mode: str = "",
):
    """Workbench entry: the task list, with one action to start a new repair.

    Older links used ``/agent?job=<id>``; they keep working by redirecting to the
    per-task conversation route, so bookmarks and the landing CTA do not break.
    """
    if job:
        # Older links carry a job id; anything that is not id-shaped is not a
        # job. In particular ``../x`` would make the browser normalise
        # ``/agent/../x`` into another site path, so it must never redirect.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job):
            raise HTTPException(status_code=404, detail="unknown job")
        fixture_flag = _fixture_int(fixture)
        query: dict[str, str] = {}
        if fixture_flag:
            query["fixture"] = str(fixture_flag)
        if workspace:
            query["workspace"] = workspace
        if mode:
            query["mode"] = mode
        target = f"/agent/{job}"
        if query:
            target += "?" + urlencode(query)
        return RedirectResponse(url=target, status_code=307)

    context = _agent_context(fixture=_fixture_int(fixture), workspace=workspace, mode=mode)
    snapshot = connector_snapshot()
    try:
        history_tasks = HistoryStore().count()
    except Exception:  # noqa: BLE001 - a broken history DB must not break the page
        history_tasks = 0
    context.update({
        "connector_counts": snapshot["counts"],
        "plugin_total": snapshot["counts"]["plugins"],
        "history_tasks": history_tasks,
        "connector_names": CONNECTOR_NAMES_ZH,
    })
    return templates.TemplateResponse(request, "agent_index.html", context)


@app.get("/agent/new", response_class=HTMLResponse, name="agent_new")
async def agent_new(
    request: Request,
    fixture: str = "",
    workspace: str = "",
    mode: str = "",
):
    """Start a brand-new repair: the guided conversation with no job attached."""
    return templates.TemplateResponse(
        request,
        "agent.html",
        _agent_context(fixture=_fixture_int(fixture), workspace=workspace, mode=mode),
    )


@app.get("/agent/{job_id}", response_class=HTMLResponse, name="agent_console")
async def agent_console(
    request: Request,
    job_id: str,
    fixture: str = "",
    workspace: str = "",
    mode: str = "",
):
    """One repair task = one conversation, with its own local history.

    UI only: no planner, no model calls, no new write endpoints. Until M1's
    agent/execution fields and M5's assistant plan land, ?fixture=1 drives the
    shell with clearly labelled placeholder data, and the demo banner stays on.
    """
    return templates.TemplateResponse(
        request,
        "agent.html",
        _agent_context(
            job_id=job_id,
            fixture=_fixture_int(fixture),
            workspace=workspace,
            mode=mode,
            job={"id": job_id},
        ),
    )


@app.get("/jobs/new", name="setup_page")
async def setup_page(request: Request):
    """Retired form intake: send old links to the one remaining intake.

    The console is now the only way to create a repair job. A redirect keeps
    bookmarks, scripts and the pre-cut landing links working without reviving a
    second input path.
    """
    return RedirectResponse("/agent/new?new=1", status_code=303)


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
    """Create a job from the console's multipart payload."""
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


COMPARISON_ROLE = "behavior_comparison"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _comparison_invalid(job_id: str, detail: str, artifacts: list[dict] | None = None) -> dict:
    return {
        "job_id": job_id,
        "status": "invalid",
        "detail": detail,
        "comparison": None,
        "artifacts": artifacts or [],
    }


def _comparison_validate(job_id: str, payload: Any) -> dict:
    """Strict enough for the GUI; M5 owns the final validation vocabulary.

    Unknown/ambiguous cases are invalid here, not guessed: this is a route-level
    fail-closed check, not the acceptance engine.
    """
    if not isinstance(payload, dict):
        return _comparison_invalid(job_id, "comparison payload is not an object")
    if payload.get("schema_version") != 1:
        return _comparison_invalid(job_id, "schema_version must be 1")
    for key in ("original_sha256", "candidate_sha256"):
        value = payload.get(key)
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            return _comparison_invalid(job_id, f"{key} must be 64 lowercase hex")
    if payload.get("scope") != "behavior_only":
        return _comparison_invalid(job_id, "scope must be behavior_only")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return _comparison_invalid(job_id, "cases must be a non-empty list")
    ids: set[str] = set()
    counts = {"same": 0, "different": 0, "not_run": 0, "unknown": 0}
    allowed_kinds = {"known_good", "known_bad", "valid", "invalid"}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            return _comparison_invalid(job_id, f"case[{index}] is not an object")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in ids:
            return _comparison_invalid(job_id, f"case[{index}] id missing or duplicate")
        ids.add(cid)
        kind = case.get("kind")
        if kind not in allowed_kinds:
            return _comparison_invalid(job_id, f"case {cid}: kind is not supported yet")
        verdict = case.get("verdict")
        if verdict not in {"same", "different", "not_run"}:
            return _comparison_invalid(job_id, f"case {cid}: verdict is not supported yet")
        detail = case.get("detail")
        if not isinstance(detail, str):
            return _comparison_invalid(job_id, f"case {cid}: detail must be a string")
        input_sha = case.get("input_sha256")
        if input_sha is not None and (not isinstance(input_sha, str) or not _HEX64.fullmatch(input_sha)):
            return _comparison_invalid(job_id, f"case {cid}: input_sha256 is invalid")
        for side in ("original", "candidate"):
            output = case.get(side)
            if not isinstance(output, dict):
                return _comparison_invalid(job_id, f"case {cid}: {side} output missing")
            rc = output.get("returncode")
            if rc is not None and (not isinstance(rc, int) or isinstance(rc, bool)):
                return _comparison_invalid(job_id, f"case {cid}: {side}.returncode must be int or null")
            if not isinstance(output.get("timed_out"), bool):
                return _comparison_invalid(job_id, f"case {cid}: {side}.timed_out must be bool")
            for key in ("stdout_sha256", "stderr_sha256"):
                value = output.get(key)
                if value is not None and (not isinstance(value, str) or not _HEX64.fullmatch(value)):
                    return _comparison_invalid(job_id, f"case {cid}: {side}.{key} is invalid")
            if verdict in {"same", "different"}:
                if rc is None or output.get("stdout_sha256") is None or output.get("stderr_sha256") is None:
                    return _comparison_invalid(job_id, f"case {cid}: {side} output incomplete for {verdict}")
        counts[verdict] += 1
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return _comparison_invalid(job_id, "summary is missing")
    if any(not isinstance(summary.get(key), int) for key in ("total", "same", "different", "not_run", "unknown")):
        return _comparison_invalid(job_id, "summary counts must be integers")
    if summary["total"] != len(cases) or any(summary[k] != counts[k] for k in counts):
        return _comparison_invalid(job_id, "summary does not match cases")
    not_claimed = payload.get("not_claimed")
    if not isinstance(not_claimed, list) or not not_claimed or not all(isinstance(x, str) and x for x in not_claimed):
        return _comparison_invalid(job_id, "not_claimed must be a non-empty string list")
    return {"job_id": job_id, "status": "available", "detail": "", "comparison": payload, "artifacts": []}


@app.get("/api/jobs/{job_id}/comparison")
async def job_comparison(job_id: str):
    """JSON comparison route used by the agent result screen.

    Missing artifact is an honest 200/not_provided. A present but unreadable,
    hash-mismatched or schema-invalid artifact is 200/invalid. Unknown job is 404.
    """
    try:
        BACKEND.get_snapshot(job_id)
    except BackendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    artifacts = BACKEND.get_artifacts(job_id)
    rows = []
    for key in ("A", "B"):
        value = artifacts.get(key) if isinstance(artifacts, dict) else None
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    row = next((item for item in rows if item.get("role") == COMPARISON_ROLE), None)
    if row is None:
        return JSONResponse({
            "job_id": job_id,
            "status": "not_provided",
            "detail": "no behavior-comparison artifact is registered for this job",
            "comparison": None,
            "artifacts": [],
        })
    if not row.get("available"):
        return JSONResponse(_comparison_invalid(job_id, "comparison artifact bytes are unavailable", [row]))
    getter = getattr(BACKEND, "get_artifact_bytes", None)
    if getter is None:
        return JSONResponse(_comparison_invalid(job_id, "artifact bytes are not readable in this backend", [row]))
    try:
        content, _media_type, _filename = getter(str(row.get("id") or ""))
        if hashlib.sha256(content).hexdigest() != row.get("sha256"):
            return JSONResponse(_comparison_invalid(job_id, "comparison artifact hash mismatch", [row]))
        payload = json.loads(content.decode("utf-8"))
    except (BackendError, OSError, ValueError, UnicodeDecodeError) as exc:
        return JSONResponse(_comparison_invalid(job_id, f"comparison artifact is unreadable: {type(exc).__name__}", [row]))
    result = _comparison_validate(job_id, payload)
    if result["status"] == "available":
        result["artifacts"] = [row]
    return JSONResponse(result)


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
    getter = getattr(BACKEND, "get_attempts", None)
    if getter is None:
        attempts = {"available": False, "attempts": []}
    else:
        try:
            attempts = getter(job_id)
        except BackendError:
            attempts = None
        if attempts is None:
            attempts = {"available": False, "attempts": []}
    return templates.TemplateResponse(
        request,
        "evidence.html",
        {
            "demo": bool(snapshot.get("demo")),
            "job": snapshot,
            "obligations": obligations,
            "artifacts": BACKEND.get_artifacts(job_id),
            "attempts": attempts,
            "backend_note": backend_description(BACKEND),
            "evidence_domain": evidence_domain,
            "scope_display": scope_display,
        },
    )


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    """Return the polling status JSON."""
    return JSONResponse(BACKEND.get_snapshot(job_id))


@app.get("/api/jobs/{job_id}/evaluation")
async def job_evaluation(job_id: str):
    """Read-only evidence observability scorecard.

    This endpoint never derives a verdict. It reports coverage, missing
    denominators and bottlenecks over whatever the controller returned.
    """
    try:
        snapshot = BACKEND.get_snapshot(job_id)
    except BackendError as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001 - backend boundary is untrusted
        return JSONResponse({"detail": f"snapshot unavailable: {type(exc).__name__}"}, status_code=502)

    attempts = {"available": False, "attempts": []}
    getter = getattr(BACKEND, "get_attempts", None)
    if getter is not None:
        try:
            payload = getter(job_id)
            if isinstance(payload, dict):
                attempts = payload
        except Exception:  # noqa: BLE001
            attempts = {"available": False, "attempts": []}

    evidence_by_attempt: dict[str, object] = {}
    evidence_getter = getattr(BACKEND, "get_attempt_evidence", None)
    if evidence_getter is not None:
        for item in attempts.get("attempts", []) if isinstance(attempts, dict) else []:
            attempt_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(attempt_id, str) or not attempt_id:
                continue
            try:
                evidence = evidence_getter(attempt_id)
            except Exception:  # noqa: BLE001
                continue
            if evidence:
                evidence_by_attempt[attempt_id] = evidence

    try:
        artifacts = BACKEND.get_artifacts(job_id)
    except Exception:  # noqa: BLE001
        artifacts = []
    artifact_rows: list[dict] = []
    if isinstance(artifacts, dict):
        for value in artifacts.values():
            if isinstance(value, list):
                artifact_rows.extend(item for item in value if isinstance(item, dict))
    elif isinstance(artifacts, list):
        artifact_rows = [item for item in artifacts if isinstance(item, dict)]
    comparison_available = any(
        str(item.get("role") or "") == "behavior_comparison" for item in artifact_rows
    )

    payload = evaluate_evidence(
        snapshot,
        attempts=attempts if isinstance(attempts, dict) else None,
        artifacts=artifacts,
        evidence_by_attempt=evidence_by_attempt,
        comparison_available=comparison_available,
    )
    payload["available"] = True
    return JSONResponse(payload)


@app.get("/api/jobs/{job_id}/attempts")
async def job_attempts(job_id: str):
    """Proxy M1's per-attempt history.

    ``available: false`` means the controller does not expose the endpoint yet;
    it is not the same as "this job has no attempts", and the UI says so.
    """
    getter = getattr(BACKEND, "get_attempts", None)
    payload = getter(job_id) if getter is not None else None
    if payload is None:
        return JSONResponse(
            {
                "attempts": [],
                "available": False,
                "note": "Controller does not expose GET /api/jobs/{id}/attempts yet.",
            }
        )
    payload = dict(payload)
    payload.setdefault("available", True)
    return JSONResponse(payload)


@app.get("/api/attempts/{attempt_id}/evidence")
async def attempt_evidence(attempt_id: str):
    """Proxy M1's attempt evidence (ordered gates + V3 summary) unchanged.

    ``available: false`` means the controller does not expose the route yet;
    the result screen then shows no gate rows instead of inventing them.
    """
    getter = getattr(BACKEND, "get_attempt_evidence", None)
    payload = getter(attempt_id) if getter is not None else None
    if payload is None:
        return JSONResponse(
            {
                "available": False,
                "note": "Controller does not expose GET /api/attempts/{id}/evidence yet.",
            }
        )
    payload = dict(payload)
    payload.setdefault("available", True)
    return JSONResponse(payload)


@app.post("/api/assistant/plan")
async def assistant_plan(request: Request) -> JSONResponse:
    """Proxy M1's job-less planning route.

    The GUI never invents a plan: when the controller has not shipped the route,
    this answers 503 and ``/agent/new`` shows "not connected" instead of falling
    back to fixture data.
    """
    _require_csrf(request.headers.get("x-csrf-token", ""))
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {"plan": None, "error": "invalid_request", "detail": "JSON body required."},
            status_code=422,
        )
    if not isinstance(body, dict):
        return JSONResponse(
            {"plan": None, "error": "invalid_request", "detail": "JSON object required."},
            status_code=422,
        )
    description = body.get("description")
    if not isinstance(description, str) or not description.strip():
        return JSONResponse(
            {"plan": None, "error": "invalid_request", "detail": "'description' is required."},
            status_code=422,
        )
    context = body.get("context")
    if context is not None and not isinstance(context, dict):
        return JSONResponse(
            {"plan": None, "error": "invalid_request", "detail": "'context' must be an object."},
            status_code=422,
        )

    getter = getattr(BACKEND, "get_plan", None)
    if getter is None:
        return JSONResponse(
            {
                "plan": None,
                "error": "assistant_unavailable",
                "fallback_reason": "llm_client_not_configured",
            },
            status_code=503,
        )
    try:
        plan = getter(description.strip(), context)
    except BackendError as exc:
        return JSONResponse(
            {"plan": None, "error": "assistant_unavailable", "detail": exc.detail},
            status_code=503,
        )
    if plan is None:
        return JSONResponse(
            {
                "plan": None,
                "error": "assistant_unavailable",
                "fallback_reason": "assistant_endpoint_missing",
            },
            status_code=503,
        )
    return JSONResponse({"plan": plan})


@app.delete("/api/history")
async def history_clear_all(request: Request) -> JSONResponse:
    """Clear every server-side task/draft conversation (settings only)."""
    _require_csrf(request.headers.get("x-csrf-token", ""))
    return JSONResponse({"deleted": HistoryStore().clear_all()})


@app.delete("/api/history/{scope}")
async def history_delete(scope: str, request: Request) -> JSONResponse:
    """Clear one task/draft conversation."""
    _require_csrf(request.headers.get("x-csrf-token", ""))
    try:
        payload = HistoryStore().delete(scope)
    except HistoryError:
        raise HTTPException(status_code=404, detail="unknown history scope")
    return JSONResponse(payload)


@app.get("/api/history/{scope}")
async def history_get(scope: str) -> JSONResponse:
    """Read one task's server-side conversation history."""
    try:
        payload = HistoryStore().load(scope)
    except HistoryError:
        raise HTTPException(status_code=404, detail="unknown history scope")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.put("/api/history/{scope}")
async def history_put(scope: str, request: Request) -> JSONResponse:
    """Replace one task's history with the browser's current entries."""
    _require_csrf(request.headers.get("x-csrf-token", ""))
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"detail": "JSON body required."}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "JSON object required."}, status_code=422)
    generation = body.get("generation")
    if generation is not None and (not isinstance(generation, int) or isinstance(generation, bool)):
        return JSONResponse({"detail": "'generation' must be an integer."}, status_code=422)
    try:
        payload = HistoryStore().save(scope, body.get("entries"), generation)
    except HistoryConflict as exc:
        return JSONResponse({"detail": str(exc), "generation": HistoryStore().load(scope)["generation"]},
                            status_code=409)
    except HistoryError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/api/code/repair")
async def code_repair_route(request: Request) -> JSONResponse:
    """Turn a pasted snippet into a static, reviewable repair suggestion.

    The code is never executed. With a provider key the model proposes the
    patch; without one, or on any model failure, deterministic rules answer.
    """
    _require_csrf(request.headers.get("x-csrf-token", ""))
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {"detail": "JSON body required."}, status_code=422
        )
    if not isinstance(body, dict):
        return JSONResponse({"detail": "JSON object required."}, status_code=422)
    code = body.get("code")
    language = body.get("language")
    if not isinstance(code, str) or not code.strip():
        return JSONResponse({"detail": "'code' is required."}, status_code=422)
    if language is not None and not isinstance(language, str):
        return JSONResponse({"detail": "'language' must be a string."}, status_code=422)
    try:
        result = repair_code(code, language, client=client_from_env())
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse({"repair": result})


@app.get("/api/assistant/key")
async def assistant_key_status() -> JSONResponse:
    """Return masked key status; never return the key itself."""
    return JSONResponse({"key": key_status(), "assistant": assistant_status()})


@app.post("/api/assistant/key")
async def assistant_key_save(request: Request) -> JSONResponse:
    """Save the operator-supplied provider key server-side.

    The key is accepted only over the local session, is never echoed, and is
    persisted by ``assistant_key_store`` (DPAPI on Windows).
    """
    _require_csrf(request.headers.get("x-csrf-token", ""))
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"detail": "JSON body required."}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "JSON object required."}, status_code=422)
    candidate = body.get("key")
    if not isinstance(candidate, str) or not candidate.strip():
        return JSONResponse({"detail": "key is required."}, status_code=422)
    username = body.get("username")
    if not isinstance(username, str) or not username.strip():
        return JSONResponse({"detail": "username is required."}, status_code=422)
    current = key_status()
    if current["source"] == "environment":
        return JSONResponse(
            {"detail": "A provider key is already configured through the environment."},
            status_code=409,
        )
    try:
        saved = save_key(candidate, username)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except OSError:
        return JSONResponse({"detail": "could not persist the key."}, status_code=500)
    # Make the current process usable immediately; an explicit env value wins.
    os.environ.setdefault(ASSISTANT_MODE_ENV, "llm")
    if (os.environ.get(ASSISTANT_MODE_ENV) or "").strip().lower() != "llm":
        os.environ[ASSISTANT_MODE_ENV] = "llm"
    return JSONResponse(
        {
            "saved": saved,
            "key": key_status(),
            "assistant": assistant_status(mode="llm"),
        }
    )


@app.post("/api/assistant/key/test")
async def assistant_key_test(request: Request) -> JSONResponse:
    """Make one small real provider call to verify the configured key."""
    _require_csrf(request.headers.get("x-csrf-token", ""))
    result = test_provider_connection()
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


@app.delete("/api/assistant/key")
async def assistant_key_clear(request: Request) -> JSONResponse:
    """Delete the stored key; an environment key cannot be cleared from the UI."""
    _require_csrf(request.headers.get("x-csrf-token", ""))
    current = key_status()
    if current["source"] == "environment":
        return JSONResponse(
            {"detail": "The provider key is managed by the environment."},
            status_code=409,
        )
    clear_key()
    os.environ[ASSISTANT_MODE_ENV] = "deterministic"
    return JSONResponse({"key": key_status(), "assistant": assistant_status(mode="deterministic")})


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


@app.get("/api/jobs/{job_id}/artifacts")
async def job_artifacts(job_id: str):
    """Proxy the job artifact list so the result screen can link deliverables."""
    return JSONResponse(BACKEND.get_artifacts(job_id))


@app.post("/api/jobs/{job_id}/deliverables", status_code=201)
async def upload_deliverable(request: Request, job_id: str, file: UploadFile = File(...)):
    """Store an operator-supplied repaired ELF for download only.

    It does not create an obligation or change any verdict. The controller
    validates the ELF magic, hashes the bytes and registers an
    ``operator_deliverable`` artifact.
    """
    _require_csrf(request.headers.get("x-csrf-token", ""))
    uploader = getattr(BACKEND, "upload_deliverable", None)
    if uploader is None:
        raise HTTPException(
            status_code=404,
            detail="Deliverable upload is only available in controller (http) mode.",
        )
    payload = await file.read()
    try:
        result = uploader(job_id, filename=file.filename or "repaired.elf", content=payload)
    except BackendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return JSONResponse(result, status_code=201)


@app.get("/api/artifacts/{artifact_id}")
async def artifact_metadata(artifact_id: str):
    """Proxy artifact metadata so evidence pages work against the controller."""
    getter = getattr(BACKEND, "get_artifact_metadata", None)
    if getter is None:
        raise HTTPException(
            status_code=404,
            detail="Artifact metadata is only available in controller (http) mode.",
        )
    return JSONResponse(getter(artifact_id))


@app.get("/api/artifacts/{artifact_id}/download")
async def artifact_download(artifact_id: str):
    """Proxy registered artifact bytes; a missing byte stream is a 404, not an empty file."""
    getter = getattr(BACKEND, "get_artifact_bytes", None)
    if getter is None:
        raise HTTPException(
            status_code=404,
            detail="Artifact downloads are only available in controller (http) mode.",
        )
    content, media_type, filename = getter(artifact_id)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or f"{artifact_id}.bin"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
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
    getter = getattr(BACKEND, "get_attempts", None)
    if getter is None:
        attempts = {"available": False, "attempts": []}
    else:
        try:
            attempts = getter(job_id)
        except BackendError:
            attempts = None
        if attempts is None:
            attempts = {"available": False, "attempts": []}
    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "bundle": bundle,
            "sha_lines": sha_lines,
            "evidence_domain": evidence_domain,
            "scope_display": scope_display,
            "attempts": attempts,
        },
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
