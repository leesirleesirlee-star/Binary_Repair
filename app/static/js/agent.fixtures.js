/* ============================================================================
 * 占位数据（FIXTURE ONLY）—— 属于 M2 开发期脚手架，不是对外契约。
 *
 * 为什么存在：Lead 87 号分配允许 M2 "can start against fixtures"。
 * M1 的 T7.1（agent.current_step / execution.status）、M5 的 T8.1
 * （AssistantPlan / deterministic planner）尚未就绪，这里用占位数据驱动 UI。
 *
 * 纪律：
 *  1. 字段名与枚举值全部以 docs/interfaces/execution-bridge-v1.md 和
 *     M5 的 plan schema 为准；本文件不作为任何一方的接口依据。
 *  2. 渲染层不直接读本文件，必须经过 agent.js 的 normalize()，协议到位后
 *     只改 normalize() 与数据源，渲染层不动。
 *  3. 真实接口联调通过后，本文件应删除。
 *  4. 所有 default / value 均为 ASCII：它们会进入提交 payload，界面文案才是中文。
 * ========================================================================== */
(function () {
  "use strict";

  var PLAN = {
    plan_id: "fixture-plan-0001",
    mode: "scripted v1",
    fallback: false,
    model: null,
    tokens: null,
    steps: [
      {
        id: "sample",
        kind: "upload",
        title: "第 1 步 · 样本",
        prompt: "拖入二进制；本机读取并核对大小与 SHA-256；点击「创建任务」后才真正上传。"
      },
      {
        id: "defect",
        kind: "question",
        title: "第 2 步 · 缺陷",
        prompt: "用一句话说明缺陷；提交字段必须是英文 ASCII。",
        questions: [
          {
            key: "defect",
            label: "缺陷描述（英文 ASCII）",
            input: "textarea",
            required: true,
            ascii: true,
            default: "strcpy in stack_buf does not check length; overlong input writes out of bounds"
          }
        ]
      },
      {
        id: "tests",
        kind: "question",
        title: "第 3 步 · 用例与环境",
        prompt: "至少给一条合法用例，最好再加边界和越界用例；每行一条。",
        questions: [
          {
            key: "valid_tests",
            label: "合法 / 边界 / 越界用例（每行一条，英文 ASCII）",
            input: "textarea",
            required: true,
            ascii: true,
            default: "valid: hello\nboundary: 8 bytes\ninvalid: 512 bytes"
          },
          {
            key: "environment",
            label: "执行环境",
            input: "text",
            required: false,
            ascii: true,
            default: "Ubuntu x86-64 VM (test: execution disabled)"
          }
        ]
      },
      {
        id: "contract",
        kind: "question",
        title: "第 4 步 · 安全契约",
        prompt: "契约决定验收规则；可先用默认值，再逐条修改。",
        questions: [
          {
            key: "valid_domain",
            label: "合法输入域",
            input: "text",
            required: true,
            ascii: true,
            default: "Input length 0-64 bytes"
          },
          {
            key: "forbidden",
            label: "禁止行为",
            input: "text",
            required: true,
            ascii: true,
            default: "Out-of-bounds write to the target buffer"
          },
          {
            key: "invalid_outcome",
            label: "非法输入时的期望结果",
            input: "text",
            required: true,
            ascii: true,
            default: "Return error code 1 without out-of-bounds access"
          },
          {
            key: "assumptions",
            label: "假设条件（每行一条）",
            input: "textarea",
            required: false,
            ascii: true,
            default: "Single process\nStack protector disabled"
          }
        ]
      },
      {
        id: "review",
        kind: "review",
        title: "第 5 步 · 复核并创建",
        prompt: "核对任务单；可点「修改」返回；确认后由你点击「创建任务」。"
      }
    ]
  };

  function baseSnapshot(jobId) {
    return {
      job_id: jobId,
      demo: true,
      phase: "draft",
      cancelled: false,
      original_sha256: "208574ab5b7753fab6f28860830069770622fb7b1fcb8d181fa50453f8656717",
      contract: { sha256: "24bfe5680000000000000000000000000000000000000000000000000000dead", approved: false },
      verdict: null,
      routes: {}
    };
  }

  var PENDING = baseSnapshot("fixture-pending");
  PENDING.phase = "running";
  PENDING.contract.approved = true;
  PENDING.agent = {
    current_step: "awaiting_execution",
    mode: "scripted v1",
    fallback: false,
    model: null,
    tokens: null
  };
  PENDING.execution = {
    status: "awaiting_ingest",
    status_label: "等待测试机回传",
    receipt_pending: true,
    last_heartbeat: "2026-11-04T02:11:03Z"
  };
  PENDING.artifacts = [];

  var SUCCESS = baseSnapshot("fixture-success");
  SUCCESS.phase = "finished";
  SUCCESS.contract.approved = true;
  SUCCESS.agent = {
    current_step: "result",
    mode: "llm",
    fallback: true,
    fallback_reason: "fixture: model channel unavailable",
    model: "fixture-model-name",
    tokens: { prompt: 1284, completion: 356 }
  };
  SUCCESS.execution = { status: "verified", status_label: "回传包已校验", receipt_pending: false };
  SUCCESS.verdict = { outcome: "success_within_scope", reason: "all obligations passed", failing_obligation: null };
  SUCCESS.candidate = {
    sha256: "4aa8f49fbe02a2f5f5d4c1f5e34b2f0be2c6f8d9e5bb1f2c1e2f6e3c0f0a1b2c",
    route: "B",
    diff: [
      "--- a/juliet_badSink.normalized.c",
      "+++ b/juliet_badSink.replacement.c",
      "@@ -12,7 +12,10 @@",
      " void badSink(char *data)",
      " {",
      "     char stack_buf[8];",
      "-    strcpy(stack_buf, data);",
      "+    if (strlen(data) >= sizeof(stack_buf)) {",
      "+        return 1;",
      "+    }",
      "+    memcpy(stack_buf, data, strlen(data) + 1);",
      "     printf(\"%s\\n\", stack_buf);",
      " }"
    ].join("\n")
  };
  SUCCESS.artifacts = [
    { id: "fixture-art-status", kind: "status", role: "status", label: "status.json" },
    { id: "fixture-art-candidate", kind: "candidate", role: "candidate", label: "candidate.elf" },
    { id: "fixture-art-audit", kind: "audit", role: "audit", label: "audit.json" }
  ];
  SUCCESS.routes = {
    B: {
      phase: "finished",
      outcome: "success_within_scope",
      stages: [
        { name: "analyzing", state: "done", at: "2026-11-04T02:04:11Z" },
        { name: "generating", state: "done", at: "2026-11-04T02:06:40Z" },
        { name: "validating", state: "done", at: "2026-11-04T02:09:55Z" }
      ],
      tests: { passed: 3, total: 3 },
      verification: "passed",
      compatibility: "passed",
      elapsed_s: 344,
      manual_interventions: 0,
      ai_usage: "fixture",
      outcome_reason: "all obligations passed",
      obligations: [
        { id: "V0", kind: "identity", result: "passed" },
        { id: "V1", kind: "structure", result: "passed" },
        { id: "V2", kind: "interface", result: "passed" },
        { id: "V3", kind: "behaviour", result: "passed" },
        { id: "V4", kind: "symbolic", result: "passed" },
        { id: "V5", kind: "reproducibility", result: "passed" }
      ],
      changed_regions: [
        { address: "0x4011a0", kind: "text", before: "e8 3b fe ff ff", after: "e8 9c fe ff ff" }
      ]
    }
  };

  var FAILURE = baseSnapshot("fixture-failure");
  FAILURE.phase = "finished";
  FAILURE.contract.approved = true;
  FAILURE.agent = { current_step: "result", mode: "scripted v1", fallback: false, model: null, tokens: null };
  FAILURE.execution = { status: "verified", status_label: "回传包已校验", receipt_pending: false };
  FAILURE.verdict = {
    outcome: "rejected",
    reason: "worker_env_missing",
    failing_obligation: "V3",
    detail: "executor could not read BINARYREPAIR_ORIGINAL"
  };
  FAILURE.artifacts = [{ id: "fixture-art-status-f", kind: "status", role: "status", label: "status.json" }];
  FAILURE.routes = {
    A: {
      phase: "finished",
      outcome: "rejected",
      stages: [
        { name: "analyzing", state: "done", at: "2026-11-04T01:12:00Z" },
        { name: "applying", state: "failed", at: "2026-11-04T01:12:04Z" }
      ],
      tests: { passed: 0, total: 3 },
      verification: "not_run",
      compatibility: "not_run",
      elapsed_s: 4,
      manual_interventions: 0,
      ai_usage: "none",
      outcome_reason: "worker_env_missing",
      obligations: [
        { id: "V0", kind: "identity", result: "passed" },
        { id: "V1", kind: "structure", result: "not_run", reason: "executor did not run" },
        { id: "V2", kind: "interface", result: "not_run" },
        { id: "V3", kind: "behaviour", result: "not_run", reason: "worker_env_missing" },
        { id: "V4", kind: "symbolic", result: "not_run" },
        { id: "V5", kind: "reproducibility", result: "not_run" }
      ],
      changed_regions: []
    }
  };

  window.AGENT_FIXTURES = {
    meta: {
      fixture_only: true,
      note: "Placeholder data for M2 UI development. Not an interface contract.",
      contract_reference: "docs/interfaces/execution-bridge-v1.md (pending from Lead)"
    },
    plan: PLAN,
    snapshots: { fresh: null, pending: PENDING, success: SUCCESS, failure: FAILURE }
  };
})();
