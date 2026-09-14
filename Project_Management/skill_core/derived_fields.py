"""
派生字段计算器
==============
为 PMO 指标自动计算同比、完成率、偏差率等派生值。

计算类型：
  - yoy: 同比变化率 = (本期 - 上期) / |上期|
  - completion_rate: 预算完成率 = 实际 / 预算
  - deviation_rate: 偏差率 = (实际 - 预算) / 预算
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def calc_yoy(current: Optional[float], last: Optional[float]) -> Optional[float]:
    """计算同比变化率。返回小数（0.15 = 15%），None 表示不可计算。"""
    if current is None or last is None:
        return None
    if last == 0:
        return None
    return (current - last) / abs(last)


def calc_completion_rate(actual: Optional[float],
                         budget: Optional[float]) -> Optional[float]:
    """计算预算完成率。返回小数（1.25 = 125%），None 表示不可计算。"""
    if actual is None or budget is None or budget == 0:
        return None
    return actual / budget


def calc_deviation_rate(actual: Optional[float],
                        budget: Optional[float]) -> Optional[float]:
    """计算偏差率。返回小数（0.10 = 10%），None 表示不可计算。"""
    if actual is None or budget is None or budget == 0:
        return None
    return (actual - budget) / abs(budget)


def format_pct(value: Optional[float], signed: bool = False) -> str:
    """格式化百分比字符串。signed=True 时正数加 + 号。"""
    if value is None:
        return "—"
    pct = value * 100
    sign = "+" if signed and pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def format_amount(value: Optional[float], unit: str = "") -> str:
    """格式化金额。"""
    if value is None:
        return "—"
    if unit == "万元":
        return f"{value:,.2f}万元"
    if unit == "小时":
        return f"{value:,.1f}h"
    return f"{value:,.2f}"


# PMO 指标 → 派生计算配置
# key: 指标 code, value: (派生类型, 源字段列表)
DERIVED_CONFIG = {
    "workhour_deviation_rate": ("deviation", ["actual_total_man_hour", "plan_total_man_hour"]),
    "budget_deviation_rate": ("deviation", ["actual_dev_cost", "plan_dev_cost"]),
    "staff_cost_deviation": ("deviation", ["actual_staff_cost", "plan_staff_cost"]),
}


def add_derived_metrics(metrics: List[Dict[str, Any]],
                        snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    为指标列表添加派生字段（同比、完成率、偏差率）。

    Args:
        metrics: 原始指标列表 [{"code": ..., "value": ..., "name": ...}, ...]
        snapshot: 完整快照数据（用于查找历史值）

    Returns:
        添加了派生字段的指标列表（原地修改）
    """
    proj = snapshot.get("project", {})
    quality_history = snapshot.get("quality_history", [])

    for item in metrics:
        code = item.get("code", "")
        config = DERIVED_CONFIG.get(code)
        if not config:
            continue

        calc_type, fields = config
        if calc_type == "deviation" and len(fields) == 2:
            actual = proj.get(fields[0])
            budget = proj.get(fields[1])
            if actual is not None and budget is not None and budget != 0:
                deviation = (actual - budget) / abs(budget)
                item["deviation_rate"] = deviation
                item["deviation_display"] = format_pct(deviation, signed=True)

    return metrics
