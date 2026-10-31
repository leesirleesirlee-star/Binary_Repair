"""Chinese UI labels.

Only presentation text lives here; upload/API/export data stays English ASCII."""

from __future__ import annotations

PHASE_LABELS_ZH: dict[str, str] = {
    "draft": "草稿",
    "queued": "排队中",
    "running": "执行中",
    "screening": "输入筛查",
    "analyzing": "二进制分析",
    "roundtrip": "C 往返校验",
    "generating": "候选生成",
    "applying": "写入副本",
    "validating": "共享校验",
    "finished": "已完成",
}

STAGE_LABELS_ZH: dict[str, str] = {
    name: label for name, label in PHASE_LABELS_ZH.items() if name != "finished"
}

OUTCOME_LABELS_ZH: dict[str, str] = {
    "success_within_scope": "成功（声明范围内）",
    "unsupported": "不支持",
    "rejected": "被拒",
    "inconclusive": "不确定",
    "error": "错误",
    "cancelled": "已取消",
}

STATE_LABELS_ZH: dict[str, str] = {
    "pending": "待执行",
    "active": "进行中",
    "done": "已完成",
    "cancelled": "已取消",
}

VALUE_LABELS_ZH: dict[str, str] = {
    "not_run": "未运行（not_run）",
    "passed": "通过",
    "failed": "失败",
    "inconclusive": "不确定",
    "unavailable": "不可用",
}

ROUTE_NAMES_ZH: dict[str, str] = {
    "A": "Route A · 直接指令修复",
    "B": "Route B · 恢复 C 后回填",
}
