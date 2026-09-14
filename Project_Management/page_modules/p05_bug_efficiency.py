"""⑤ Bug 修复效率 — 问题解决得快不快？"""

from __future__ import annotations

from typing import Any

from page_modules.base_module import BasePageModule


def _num(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class P05BugEfficiency(BasePageModule):
    PAGE_ID = "p05_bug_efficiency"
    TITLE = "⑤ Bug 修复效率"
    CORE_QUESTION = "问题解决得快不快？"
    DOMAIN_INDEX = 5

    def fetch_data(self, snap: dict) -> dict[str, Any]:
        proj = snap.get("project", {})
        bug_detail = snap.get("bug_detail", {})
        closed = bug_detail.get("bug_closed_count", 0) or 0
        total = bug_detail.get("bug_total_count", 0) or 0
        resolve_duration = _num(proj.get("bug_resolve_duration"))
        mttr = resolve_duration / max(closed, 1) / 3600
        return {
            "closed_bug_count": closed,
            "total_bug_count": total,
            "mttr_hours": mttr,
            "resolve_duration_seconds": resolve_duration,
        }

    def build_context(self, data: dict[str, Any], metrics: dict) -> dict[str, Any]:
        closed = data["closed_bug_count"]
        total = data["total_bug_count"]
        mttr = data["mttr_hours"]
        return {
            "charts": [
                {"id": "bugCloseChart", "title": "本期 Bug 关闭情况", "type": "donut",
                 "data": [
                     {"label": "已关闭", "value": closed},
                     {"label": "未关闭", "value": max(total - closed, 0)},
                 ]},
                {"id": "mttrChart", "title": "平均修复时长", "type": "bar",
                 "data": [{"label": "本期 MTTR（小时）", "value": round(mttr, 2)}]},
            ],
            "conclusion": f"累计 Bug 修复时长 {data['resolve_duration_seconds']:,.0f} 秒 ÷ "
                          f"已关闭 Bug {closed} 个 ÷ 3600 = {mttr:.2f} 小时。",
            "action": "接入每个 Bug 的创建、关闭、严重级别和状态，"
                      "才能计算分级 MTTR、Bug 年龄中位数与月度 MTTR 趋势。",
            "decision_brief": f"已关闭 {closed}/{total} 个 Bug，MTTR {mttr:.2f} 小时。",
        }
