"""① 产研精细化管理 — 交付节奏是否可控？"""

from __future__ import annotations

from typing import Any

from page_modules.base_module import BasePageModule


class P01FineManagement(BasePageModule):
    PAGE_ID = "p01_fine_management"
    TITLE = "① 产研精细化管理"
    CORE_QUESTION = "交付节奏是否可控？"
    DOMAIN_INDEX = 1

    def fetch_data(self, snap: dict) -> dict[str, Any]:
        proj = snap.get("project", {})
        quality_history = snap.get("quality_history", [])
        return {
            "task_count": proj.get("task_count"),
            "task_finish_count": proj.get("task_finish_count"),
            "task_delay_count": proj.get("task_delay_count"),
            "high_difficulty_task_count": proj.get("high_difficulty_task_count"),
            "quality_history": quality_history,
            "stat_month": proj.get("stat_month"),
        }

    def build_context(self, data: dict[str, Any], metrics: dict) -> dict[str, Any]:
        task_done = data.get("task_finish_count") or 0
        task_total = data.get("task_count") or 0
        task_delay = data.get("task_delay_count") or 0
        sprint_metric = metrics.get("sprint_completion_rate")
        sprint_val = f"{sprint_metric.value:.1%}" if sprint_metric and sprint_metric.value else "暂无数据"
        return {
            "charts": [
                {"id": "taskChart", "title": "任务完成情况", "type": "bar",
                 "data": [
                     {"label": "任务总数", "value": task_total},
                     {"label": "已完成", "value": task_done},
                     {"label": "延期任务", "value": task_delay},
                     {"label": "高难任务", "value": data.get("high_difficulty_task_count") or 0},
                 ]},
            ],
            "conclusion": f"Sprint 完成率 {sprint_val}，交付结果"
                          f"{'正常' if sprint_metric and sprint_metric.warning_level == 'normal' else '需关注'}；"
                          f"延期任务 {task_delay} 项需排查计划质量。",
            "action": "下月按任务类型拆分延期原因（需求变更、资源不足、技术阻塞），"
                      "将『完成率』与『延期率』同时纳入例会复盘。",
            "decision_brief": f"完成 {task_done:.0f}/{task_total:.0f} 项任务，延期 {task_delay:.0f} 项需排查。",
        }
