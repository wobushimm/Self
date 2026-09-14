"""② 项目投入偏差 — 钱和时间花在哪了？偏了多少？"""

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


class P02InvestmentDeviation(BasePageModule):
    PAGE_ID = "p02_investment_deviation"
    TITLE = "② 项目投入偏差"
    CORE_QUESTION = "钱和时间花在哪了？偏了多少？"
    DOMAIN_INDEX = 2

    def fetch_data(self, snap: dict) -> dict[str, Any]:
        proj = snap.get("project", {})
        quality_history = snap.get("quality_history", [])
        stat_month = proj.get("stat_month", "")
        history = []
        for rec in quality_history:
            row = dict(rec)
            # 月份字段兼容 settlement_month / stat_month
            row["statMonth"] = str(rec.get("settlement_month") or rec.get("stat_month", ""))
            is_current = row["statMonth"] == stat_month
            # 优先使用历史记录自身的字段，回退到 proj（仅当月）
            row["actualWorkhourMonth"] = _num(rec.get("actual_workhour_month")) or (
                _num(proj.get("actual_total_man_hour")) if is_current else 0)
            row["planWorkhourMonth"] = _num(rec.get("estimated_workhour_month")) or (
                _num(proj.get("plan_total_man_hour")) if is_current else 0)
            row["rdActualCostMonth"] = _num(rec.get("rd_actual_cost_month")) or (
                _num(proj.get("actual_dev_cost")) if is_current else 0)
            row["rdBudgetCostMonth"] = _num(rec.get("rd_budget_cost_month")) or (
                _num(proj.get("plan_dev_cost")) if is_current else 0)
            row["staffActualCostMonth"] = _num(rec.get("staff_actual_cost_month")) or (
                _num(proj.get("actual_staff_cost")) if is_current else 0)
            row["staffBudgetCostMonth"] = _num(rec.get("staff_budget_cost_month")) or (
                _num(proj.get("plan_staff_cost")) if is_current else 0)
            history.append(row)
        return {
            "actual_total_man_hour": _num(proj.get("actual_total_man_hour")),
            "plan_total_man_hour": _num(proj.get("plan_total_man_hour")),
            "total_cost_actual": _num(proj.get("actual_dev_cost")) + _num(proj.get("actual_staff_cost")),
            "total_cost_budget": _num(proj.get("plan_dev_cost")) + _num(proj.get("plan_staff_cost")),
            "history": history,
        }

    def build_context(self, data: dict[str, Any], metrics: dict) -> dict[str, Any]:
        history = data["history"]
        costs = [
            {
                "label": str(r.get("statMonth", ""))[5:7] + "月",
                "研发实际": _num(r.get("rdActualCostMonth")),
                "研发预算": _num(r.get("rdBudgetCostMonth")),
                "人力实际": _num(r.get("staffActualCostMonth")),
                "人力预算": _num(r.get("staffBudgetCostMonth")),
            }
            for r in history
        ]
        workhours = [
            {
                "label": str(r.get("statMonth", ""))[5:7] + "月",
                "实际": _num(r.get("actualWorkhourMonth")),
                "计划": _num(r.get("planWorkhourMonth")),
            }
            for r in history
        ]
        wh_metric = metrics.get("workhour_deviation_rate")
        bg_metric = metrics.get("budget_deviation_rate")
        wh_val = f"{wh_metric.value:.1%}" if wh_metric and wh_metric.value is not None else "暂无"
        bg_val = f"{bg_metric.value:.1%}" if bg_metric and bg_metric.value is not None else "暂无"
        return {
            "charts": [
                {"id": "costChart", "title": "月度成本：实际与预算对比", "type": "grouped", "data": costs},
                {"id": "workhourChart", "title": "月度工时：实际与计划对比", "type": "grouped", "data": workhours},
            ],
            "conclusion": f"工时偏差率 {wh_val}，预算偏差率 {bg_val}；以产品线看板为本期权威源。",
            "action": "按『新增需求、返工、资源价格、外包/人力』拆解成本偏差；"
                      "确认计划工时是否仍反映当前范围。",
            "decision_brief": f"实际 {data['actual_total_man_hour']:.0f}h / 计划 {data['plan_total_man_hour']:.0f}h；"
                              f"成本 {data['total_cost_actual']:,.0f} / 预算 {data['total_cost_budget']:,.0f}",
        }
