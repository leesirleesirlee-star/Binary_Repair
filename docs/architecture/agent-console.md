# 修复控制台（/agent）使用说明

> 归属：M2（GUI owner）。对应 Lead 87 号分配的 T7.4 / T7.5 / T7.6 / T8.4，T7.8 部分完成。
> 基线：`origin/post-final/host-importer-broker` @ `1da22ff`，工作分支 `member2/agent-console`。
> 本页只做 UI：不实现 planner、不调模型、不新增写接口、不判定修复结论。

## 一、怎么跑

```bash
# 项目根目录，使用项目虚拟环境
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 然后打开启动时打印的带 token 地址（或先访问 /?token=... 建立会话）
```

| 地址 | 用途 |
|---|---|
| `/agent` | 任务列表（工作台入口）：本机任务卡片 + 一个「新建修复任务」入口 |
| `/agent/<任务ID>` | **单任务对话页**：该任务自己的对话与操作记录，2 秒轮询，页面隐藏时降为 8 秒 |
| `/agent/new` | 新建修复任务：对话式引导，创建成功后跳到 `/agent/<新ID>` |
| `/agent?job=<任务ID>` | 旧链接兼容，307 跳到 `/agent/<任务ID>` |
| `/agent/new?fixture=1` | 全演示模式：占位 plan + 占位快照，**不会创建真实任务**，演示横幅常显 |
| `/agent/new?fixture=1&scenario=pending` | 演示「等待测试机回传」：诊断区是占位，不渲染 `not_run` |
| `/agent/new?fixture=1&scenario=success` | 演示成功：diff、义务全 passed、`llm` 徽章 + 降级横幅 + model/token |
| `/agent/new?fixture=1&scenario=failure` | 演示失败：原因卡 + 建议卡，`not_run` 逐字显示 |
| `/agent/new?plan=fixture` | **引导用占位 plan，但创建任务走真实 `POST /jobs`**，之后真实轮询 |

### 任务列表的来源与边界

| 能力 | 现在的实现 | 诚实的限制 |
|---|---|---|
| 任务排列 | 本机 `localStorage`（`br.jobs.v1`），按最近打开排序，最多 50 条 | 不是 controller 全量列表；换浏览器看不到 |
| 任务状态 | 每条 `GET /api/jobs/<id>` 核对 `phase` / `cancelled` | 404 标「后端已不可查」，不删除记录 |
| 对话历史 | 按任务存 `br.msg.v1.<id>`，最多 120 条消息/工具摘要 | 仅本机；跨设备需 M5 T8.1 持久化 |
| 创建新任务 | `POST /jobs`，成功后写列表并跳 `/agent/<新ID>` | 仍必须用户点击「创建任务（由你确认）」 |

侧栏只有一个「＋ 新建修复内容」；任务本身在列表里切换，不再出现「修复控制台」「新建任务（表单）」这类重复入口。表单页 `/jobs/new` 已下线，仅保留 303 重定向到 `/agent/new`，控制台是唯一建单入口。

## 二、界面结构

```
侧栏：品牌 · ＋新建修复内容 · 本机任务列表（可切换）· 主题 · 工作区 · 连接器 · 边界说明
单任务页：← 全部任务 · 修复任务 <短ID>
顶栏：助手模式徽章 · 任务 id · 轮询心跳
降级横幅：后端给出 fallback，或 llm 模式未接线时出现
视图：对话引导 / 结果与证据；有任务时给「对比页」「证据与导出」
主列：终端流（助手消息、可展开工具块、问题卡、上传卡、复核卡）+ composer
右栏：当前步骤 · 代码修复 · 执行状态 · 阶段 · 契约 · 助手调用元数据 · 产物
```

- **工具块**默认折叠为一行摘要，点击展开看字段与原始 JSON。
- **composer**：`Enter` 发送、`Shift+Enter` 换行；`/skip` 用默认答案、`/status` 看状态、`/restart` 重开引导，演示模式下还可用 `/fixture fresh|pending|success|failure` 切场景。
- **上传**：本机 `FileReader` 读取并显示真实读取进度，`crypto.subtle` 算 SHA-256；不是 ELF 头时给警告但不阻断（演示样本仍可提交）。**真正的上传发生在你点击「创建任务」时**，用 XHR 显示真实上传进度。
- **复核卡**：每一项都能点「修改」回到对应步骤，已填内容保留。
- **代码修复**：粘贴 ```lang 围栏代码（或至少三行、含代码特征的消息）会走
  `POST /api/code/repair`，在对话里生成一张建议卡：问题列表、修复后代码、
  差异、复制与重新分析。规则修复不执行代码；配置了 provider key 时由后端请求
  模型并校验 JSON，失败自动回退规则。卡片只给建议，不创建任务、不写结论。
  详见 [code-repair.md](code-repair.md)。
- **代码块**：围栏代码渲染为带语言标签和复制按钮的代码块；用户与助手消息都
  有头像，代码气泡更宽。

## 二之一、服务端历史与可伸缩面板

- 每个任务的对话/工具/修复卡会写入 `var/gui-history.db`（`GET/PUT
  /api/history/<job-id>`）。打开任务时优先恢复服务端记录，失败回退
  localStorage；徽章显示“服务端历史 / 同步中 / 本地暂存”。草稿也写入固定 `draft` 作用域；
  「新建修复内容」用 `?new=1` 清空草稿后开始空白会话。部署与边界见 [history.md](history.md)。
- 侧栏可收起（`Ctrl/Cmd + \`），状态面板可拖动/方向键调整并双击复位，
  composer 可纵向拉伸并自动增高，长代码块可展开。顶部“专注对话”隐藏页头与两侧面板，
  Esc 退出。
- 侧栏任务支持搜索、置顶、重命名和删除本机记录；工作区菜单切换服务端配置的
  工作区；`Ctrl/Cmd+K` 打开跨页面命令面板。
- Composer 的 `+` 菜单支持多文件代码/文本载入、拖放/粘贴、代码块、漏洞示例、
  审查模板、清空对话与使用说明；二进制样本仍走引导上传步骤。
- 偏好由 `/settings` 写入 `localStorage["br.settings.v1"]`，详见
  [ui-settings.md](../reference/ui-settings.md)。

## 三、诚实性规则（有测试锁死）

1. 结论只读 `verdict`，前端不推断、不聚合；没有 verdict 时显示「尚无 verdict（等待验收引擎）」。
2. 义务结果**逐字**渲染后端取值，`not_run` 不翻译、不美化。
3. `execution.receipt_pending` 为真时，诊断区只显示「等待执行结果回传」占位，**不渲染 `not_run`**。
4. 模式徽章如实显示 `scripted v1` / `llm` / `deterministic`；`llm` 但 client 未接线时显示「llm（未接线）」，不冒充已启用。
5. 发生降级时**必须**出现降级横幅；model/token 没给就显示「未提供」或「未调用」，不编数字。
6. 未登记的 `reason_code` 显示「未知原因 + 请把 status.json 发给 M1/M4」，不猜原因。
7. 创建任务只能由用户点击触发；助手不发起运行、不写 verdict。

## 四、fixtures 的边界

`app/static/js/agent.fixtures.js` 是**开发期占位数据**，文件头已标注：

- 字段名与枚举值以 `docs/reference/interfaces/execution-bridge-v1.md` 和 M5 的 plan schema 为准，本文件不是接口依据。
- 渲染层只消费 `agent.js` 的 `normalize()` 输出；协议到位后只改 `normalize()` 与取数函数。
- 真实接口联调通过后应删除该文件。
- 其中所有 `default` 值都是 ASCII，因为它们会进入提交 payload。

## 四之一、T8.4 assistant 模式 UI（已完成）

| 项 | 实现 |
|---|---|
| 徽章 | `助手模式：脚本 v1（deterministic）` / `助手模式：LLM（llm）`；同时写 `data-plan-mode` 保留原值 |
| 回退横幅 | `plan.fallback=true` 或 llm 未接线时显示，文案带 `fallback_reason`，明确「不代表模型输出」 |
| 元数据 | `model` + `tokens`（兼容 `prompt/completion` 与 `input/output`）；缺失写「未提供」，`partial=true` 标「（部分真实用量）」 |

模式字段来自 `app/domain/assistant.py: AssistantPlan`，M2 不改该 schema。
后端未开通时徽章显示「LLM（未接线）」，不伪装成模型已调用。

## 四之二、T8.2 LLM planner 接缝（`app/services/assistant_llm.py`）

M2 已接管该模块，并接上 M5 的稳定接缝
`app/services/assistant.py: generate_plan(..., client=..., planner=...)`。
不编辑 `assistant.py`，不碰持久化。

| 范围 | 状态 |
|---|---|
| 输入 | `description` + `context`；`context.data_dir` / `context.job_id` 只用于审计路由，不进 prompt；不可 JSON 序列化的字段直接丢弃，不发 Python repr |
| 传输 | `client.chat_json(messages, max_tokens=..., thinking=False)`；兼容旧 `client.complete(messages, model=...)` |
| 输出 | `AssistantPlan`（`app/domain/assistant.py`）——M5 直接 `model_validate` 并落 `assistant_plans` |
| 失败 | 返回确定性引导包装：`mode="llm"`、`fallback=True`、`fallback_reason` 稳定码；**不抛异常、不返回半成品** |
| 账号字段 | `plan_id` 缺省或空白时补 uuid；`model` / `tokens` 只取响应真值，缺失为 `null`，不填 0；`latency_ms` 由客户端 `perf_counter` 实测，不信模型自报 |
| 审计 | 每次尝试写一行 `model_calls`（成功 `accepted_for_assistant`，失败 `fallback`）；token 与 `latency_ms` 分列写；写库失败不影响计划返回 |
| 安全 | 不读 key、不建连接、不记录 prompt/模型原文；schema 错误用 `include_input=False` |

`planner=` 是 M5 给测试注入假计划的 seam：只要传了 `planner`，`assistant.py` 就不会走到
本模块。M2 的 fake transport 同时覆盖 `chat_json` 与 `complete` 两种形状。

`/agent/new` 已接好 `POST /api/assistant/plan` 的同源代理与渲染；controller 路由仍归 M1。
`mock` / `local` 后端在进程内走同一个 planner 接缝（无 provider key 时确定性计划，有 key
时走 LLM），所以默认启动即可拿到计划，页面会把确定性计划标为“未调用模型”。只有后端
明确没有计划路由（http 404/405/501）或 planner 失败时才返回 503，页面写“计划服务未接通
（原因）”，不退回 fixture；`?plan=fixture` 只作本地预览。

## 四之三、M1 交接（`97-m1-review-closeout`）

M1 的 `7984bb6` 与 503 契约 `3fec2c0` 已进入 `canonical/m1-m5-integration`
（tip `1938c31`）；ordered gates / V3 计数在 `m1/t30-acceptance-ab-2026-11-05 @ fdc083d`，
尚未进 canonical。本分支按契约消费，合入后只需联调。
**验收目标：M1 合并 M5 后的最终 head**；canonical `1938c31` 只做基线。

| 契约 | GUI 行为 |
|---|---|
| `obligations.scope` ∈ metadata / binary / replay / model | 优先渲染持久化值，`data-evidence-scope-source="persisted"`；缺失时回退 kind 表并标 `kind` |
| `GET /api/jobs/{id}/attempts` | 代理到同名路径；端点不存在时返回 `available:false`，报告写 `not_available`，**不写成“零次尝试”** |
| `ai_usage = {input_tokens, output_tokens, partial}` 或 `null` | 只显示后端给的数字；`null`/全缺失显示“未提供”，不显示 0；`partial=true` 时在数字后标“（部分真实用量）”，不当作完整值。只消费这三个字段；`controller` 透传归 M1（唯一实现），M2 不自行改 controller；等 canonical 合入后联调 |
| `GET /api/attempts/{id}/evidence` 的 `gates[]` | 代理到同名路径；**按后端数组顺序**渲染 V0..V5，不排序、不补行、不写死样例 |
| 同响应的 `v3 = {gate, kind, result, input_mode, valid, invalid}` | 标题显示 `valid passed/total`；`input_mode="none"` 或 `invalid.total=0` 时 invalid 显示 `N/A`，不显示 `0/0 passed`。`invalid` 定稿为 `{passed:0,total:0}`（不是 `null`）；`invalid_applicable` 只作旧响应兼容 |
| artifact `role` | GUI 不按 `route_a_evidence` / `route_b_evidence` 映射；原样显示后端 `role` |

语义决定（答复 M1）：

- **scope**：接受 V1/V2 = `binary`。scope 只表达“证据覆盖哪一层”，不表达“用什么手段获得”；
  枚举里不设 `static`，否则会出现两套轴。screening 的静态分析产物是另一列（source），不塞进 scope。
- **attempt_no / status**：保持清单字段名。GUI 不依赖 `sequence` / `phase` 这类数据库列名，
  对外 API 字段稳定比跟 DB 列对齐更重要。

## 五、测试

```bash
# 服务端：路由、会话/host 门、诚实性、任务列表/单任务路由、历史键
.venv/Scripts/python.exe -m pytest tests/test_agent_console.py -q

# T8.2 接缝：成功、超时、坏 JSON、schema、无 client
.venv/Scripts/python.exe -m pytest tests/test_assistant_llm.py -q

# 核心回归
.venv/Scripts/python.exe -m pytest tests/test_agent_console.py tests/test_assistant_llm.py tests/test_ui_config.py tests/test_agent_honesty.py tests/test_language_policy.py tests/test_status_honesty.py tests/test_smoke.py tests/test_security.py tests/test_export_bundle.py -q

# 浏览器（本机 Edge + CDP，无需下载 Playwright）
.venv/Scripts/python.exe -m pytest tests/e2e -q

# 截图（T7.8）
.venv/Scripts/python.exe tests/e2e/capture_agent_screens.py
```

本次实测：`test_assistant_llm.py` 46/46；`test_agent_console.py` 43/43（含 gates 顺序、invalid N/A、V3 valid 计数、evidence 代理不可用）；`test_m1_handoff.py` 6/6；`tests/integration/test_assistant_plan.py` 5/5。

**已知预存在失败（与本次改动无关，已用 stash 对照确认）**：`tests/test_evidence_endpoint.py` 5 项在基线上同样失败，报 `attempts/<id>.json` 文件不存在，属后端持久化问题，归 M1/M5，不在 M2 范围。

## 六、带标注截图（docs/assets/screenshots/）

| 文件 | 标注 |
|---|---|
| `agent-guided.png` | 对话引导首屏：终端流 + 上传卡 + composer + 右栏状态面板，演示横幅常显 |
| `agent-pending.png` | 等待回传：诊断区为占位文案，**没有** `not_run` 列表；右栏执行状态为 `awaiting_ingest` |
| `agent-failure.png` | 失败：原因卡（`worker_env_missing`）+ 建议卡（负责方与可否重试），义务 `not_run` 逐字 |
| `agent-success.png` | 成功：`llm` 徽章 + 降级横幅 + model/token，修改前后给 unified diff 与双哈希 |

## 七、还没做 / 等别人

| 事项 | 等谁 |
|---|---|
| 引导计划的 controller 端点 `POST /api/assistant/plan`（无 job_id） | M1；GUI 侧代理、渲染与 503 回退已就绪，等它上线即可联调 |
| 真实 provider client（实现 `chat_json` + key 注入） | M1 在端点内构造；未注入前只有 deterministic/回退计划 |
| 服务端任务列表 `GET /api/jobs`（现在只有本机记录） | M1 |
| 对话历史跨设备 / 服务端持久化 | M5 T8.1 persistence |
| 创建任务时把 `plan_id` 关联到 job | M5/M1：`create_job` 目前不接收 plan |
| 右栏执行状态直读 `execution.status` 标准枚举 | M1 T7.1 + execution-bridge-v1 正文 |
| 时间线接入真实 attempt / run 节点 | M1 T6.2a、M5 T6.1 |
| 结果屏接入真实 `analysis` / `comparison` | M5 T7.2、M1 T6.3 |
| 旧页面重定向到 `/agent`（T7.8 收尾） | 需与 M4 的 e2e 口径一起改，**暂未启用**，避免旧验收断裂 |
| `docs/prototype/agent-console.html` 原型评审（T7.0，11-06 截止） | Lead 提供原型文件 |
