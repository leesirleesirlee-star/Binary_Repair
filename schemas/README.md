# Schema 契约（candidate v1）

这一层回答三个问题：

1. **模型可以返回什么？** —— `candidate.v1.json`
2. **服务端允许发什么？** —— `context.v1.json`
3. **GUI / 验证 / 数据库怎么对齐？** —— 同一份字段与枚举

源头只有一个：`binaryrepair_ai/schemas.py` 里的 Pydantic 模型。JSON Schema 是它的导出物，
不允许手改 `schemas/generated/*.json`。

```
schemas.py (Pydantic, 源头)
      │  python schemas/generate.py
      ▼
generated/*.json (JSON Schema, 给非 Python 消费者/文档/CI)
      │
      ├── GUI：字段名、类型、枚举，不用 import Python
      ├── 验证：candidate 的结构检查
      └── DB：model_calls / 候选字段映射
```

为什么不是"JSON 模式就够了"：JSON 只保证可解析；身份、路由字段、参数范围、调用白名单
是**语义**，必须由 `check_candidate()` 检查。JSON Schema 能表达结构，表达不了
"Route A 不能带 replacement_c" 这种跨字段规则——测试
`test_json_schema_does_not_encode_route_semantics` 就是在记录这个边界。

---

## 1. Candidate v1 字段

| 字段 | 类型 | 必填 | 说明 | 语义校验 |
|---|---|---|---|---|
| `schema_version` | `1`（const） | 是 | 契约版本；改字段就要升版本 | — |
| `original_sha256` | 64-hex | 是 | 冻结的原始 ELF | 必须等于 job 的输入 hash |
| `contract_sha256` | 64-hex | 是 | 已审核契约 revision | 必须等于本次 run 的契约 |
| `analysis_sha256` | 64-hex | 是 | 二进制分析 bundle | 必须等于服务端发出的分析 |
| `route` | `"route_a"` / `"route_b"` | 是 | 修复路线 | 必须等于请求的 route |
| `target_entry` | 标识符或 `0x…` | 是 | 允许修改的函数 | 必须等于审核过的目标 |
| `evidence_ids` | string[]，1–16 | 是 | 引用的证据 | 必须是服务端发过的 id |
| `template_id` | string? | Route A 必填 | 模板 id | 必须在 `templates` 列表里 |
| `parameters` | object<string,int>? | Route A 必填 | 模板参数 | key 必须完全匹配、值在区间内 |
| `recovered_function_sha256` | 64-hex? | Route B 必填 | 恢复出的 C 的 hash | 必须等于请求里的 recovered hash |
| `replacement_c` | string? | Route B 必填 | 完整函数定义 | 长度、目标函数名、禁用构造、调用白名单 |
| `expected_effects` | string[]，1–32，每条 ≤512 | 是 | 声称的效果 | 只给人看，**不作为证据** |
| `assumptions` | string[]，0–32，每条 ≤512 | 否 | 依赖条件 | 展示用 |
| `uncertainties` | string[]，0–32，每条 ≤512 | 否 | 已知不确定 | 展示用；本身不阻止验证 |

`additionalProperties: false`。多一个字段就整份拒绝——这是防止"悄悄扩权"的核心。

自由文本字段（三个列表）另有两条规则：单条 ≤ **512 字符**、列表 ≤ **32 条**；
出现"ignore previous instructions / run: / rm -rf / URL"等指令样文本时，**整份候选拒绝**，
不是过滤后接受。这是兜底，不是安全边界——真正的边界是候选永远不能改权限、工具、范围。

公共入口（验证骨架绑定这个名字）：

```python
from app.domain.candidates import validate_candidate, may_leave_host

cand = validate_candidate(payload)       # 合法返回 CandidateV1；不合法抛 CandidateRejected
may_leave_host("public")                 # True
may_leave_host("client", derived=True)   # False（默认拒绝）
```

## 2. Context v1 字段（服务端 → 模型）

| 字段 | 说明 |
|---|---|
| `route` | 本次请求的路线，模型不能改 |
| `original_sha256` / `contract_sha256` / `analysis_sha256` | 身份三元组，要求模型原样回显 |
| `target_entry` / `target_signature` | 唯一允许修改的函数与签名 |
| `contract_summary` | 审核过的契约摘录，是范围的唯一权威 |
| `allowed_evidence_ids` | 闭集，模型只能引用其中的 id |
| `templates` | Route A 的模板与参数区间（闭集） |
| `recovered_c` / `recovered_function_sha256` | Route B 的输入函数（**不可信数据**） |
| `allowed_calls` | Route B 允许调用的外部函数；其余一律 scope violation |
| `max_replacement_chars` | 输出长度上限 |

## 3. 版本与变更流程

v1 是**封闭**契约（`additionalProperties: false`），所以：

- 加字段 = 破坏性变更 → 新文件 `candidate.v2.json`，旧代码继续读 v1；
- 改语义（如 `evidence_ids` 上限）= 新版本；
- 只改描述/标题 = 可直接改，但也要重新生成并跑 drift 测试；
- 任何变更必须同时通知 **GUI（2 号）** 和 **验证（4 号）**，并在 `CHANGELOG.md` 记一行；
- 不允许"线上偷偷放宽"：校验失败就是失败。

流程：

```bash
# 1. 改 binaryrepair_ai/schemas.py
# 2. 重新生成
python schemas/generate.py
# 3. 确认没有手改 generated/
git diff --stat schemas/
# 4. 跑契约测试（CI 里也是这一条）
pytest tests/test_schema_contract.py
python schemas/generate.py --check
```

`--check` 在 CI 中失败 = 有人改了模型但没提交生成物，或者手改了生成物。

## 4. 与数据库的映射

| Candidate 字段 | 落库位置 |
|---|---|
| `original_sha256` | `jobs.original_sha256`（创建 job 时已写） |
| `route` | `attempts.route` |
| `replacement_c` / `template_id` + `parameters` | 作为 artifact 存文件（`artifacts.role='proposal'`），hash 进 `attempts.candidate_sha256` |
| `expected_effects` / `assumptions` / `uncertainties` | 完整 candidate JSON 存 artifact，便于 replay |
| 模型调用元数据 | `model_calls`（见 `db/README.md`） |

**不要把 candidate 正文塞进 SQLite 行**：大文本进文件，行里只留 hash 和相对路径
（技术设计 §6 的要求）。

## 5. 示例

- `examples/candidate.routeA.json` —— 模板 + 参数
- `examples/candidate.routeB.json` —— recovered C + 修复后的完整函数
- `examples/context.routeB.json` —— 服务端发给模型的上下文

这些示例同时被 JSON Schema 和 Pydantic 测试覆盖，改 schema 时会一起失败，提醒你更新示例。
