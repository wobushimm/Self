"""⑥ 需求价值交付 — 做的东西有没有用？"""

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


class P06ValueDelivery(BasePageModule):
    PAGE_ID = "p06_value_delivery"
    TITLE = "⑥ 需求价值交付"
    CORE_QUESTION = "做的东西有没有用？"
    DOMAIN_INDEX = 6

    def fetch_data(self, snap: dict) -> dict[str, Any]:
        proj = snap.get("project", {})
        return {
            "actual_rd_man_hour": _num(proj.get("actual_rd_man_hour")),
            "actual_test_man_hour": _num(proj.get("actual_test_man_hour")),
            "task_finish_count": _num(proj.get("task_finish_count")),
            "actual_total_man_hour": _num(proj.get("actual_total_man_hour")),
        }

    def build_context(self, data: dict[str, Any], metrics: dict) -> dict[str, Any]:
        req_cost = metrics.get("requirement_cost")
        rd_ratio = metrics.get("rd_resource_input_ratio")
        cost_val = "暂无数据"
        if req_cost and req_cost.display_value:
            cost_val = req_cost.display_value
        elif req_cost and req_cost.value is not None:
            cost_val = f"{req_cost.value:.2f} 人天/项"
        ratio_val = "暂无数据"
        if rd_ratio and rd_ratio.display_value:
            ratio_val = rd_ratio.display_value
        elif rd_ratio and rd_ratio.value is not None:
            ratio_val = f"{rd_ratio.value:.1%}"
        return {
            "charts": [
                {"id": "valueChart", "title": "研发与测试工时投入结构", "type": "donut",
                 "data": [
                     {"label": "研发工时", "value": data["actual_rd_man_hour"]},
                     {"label": "测试工时", "value": data["actual_test_man_hour"]},
                 ]},
            ],
            "conclusion": f"需求成本 {cost_val}；研发资源投入占比 {ratio_val}。"
                          f"当前以完成任务数代替完成需求数，须在接入需求系统后校正。",
            "action": "建议接入需求验收、活跃使用、客户/业务收益、目标达成字段，"
                      "届时按需求建立『投入-交付-验证-收益』链路。",
            "decision_brief": f"需求成本 {cost_val}；研发投入占比 {ratio_val}。",
        }
