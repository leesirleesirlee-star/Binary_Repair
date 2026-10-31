# Schema changelog

只记录**契约层**的变更；数据库变更见 `db/migrations/`。

## v1 — 2026-10-18

首个冻结候选契约。

**同日与 verification 骨架对齐（`verification-design` §4.3）：**

- `route` 的 JSON 取值改为 `route_a` / `route_b`（与 `tests/integration/test_ai_boundary.py` 一致）；
  文档里的 "Route A/B" 只是叙述，不再出现在数据里。
- 自由文本上限：单条 512 字符、列表 32 条；指令样文本（ignore previous / run: / rm -rf / URL）
  整份拒绝。
- 新增公共入口 `app.domain.candidates`：`validate_candidate(payload)`、
  `may_leave_host(classification, derived=False)`，返回 `CandidateV1`，失败抛 `CandidateRejected`。
- 新增 `app.services.provider.ProviderFailure(FailureReason)`，映射 timeout / quota / empty /
  malformed JSON 四类失败。

- `candidate.v1.json`：Route A（`template_id` + `parameters`）与 Route B
  （`recovered_function_sha256` + `replacement_c`）的封闭对象，`additionalProperties: false`。
- `context.v1.json`：服务端允许发送的上下文（身份三元组、契约摘录、模板闭集、
  恢复函数、调用白名单）。
- 语义规则（Pydantic + `check_candidate`）：身份 hash 必须匹配；`target_entry` 必须在范围内；
  `evidence_ids` 必须来自服务端；Route A 参数必须完全匹配模板并在区间内；
  Route B 必须定义目标函数、禁用 `#include`/`asm`/`system` 等构造、只调用白名单函数。
- 明确不做：JSON Schema 不表达跨字段路由规则（由 Pydantic 负责），也不承诺模型一定遵守。

变更人：Role 5（AI / Route B / schema）
评审人：GUI（2 号）、验证（4 号）——待签
