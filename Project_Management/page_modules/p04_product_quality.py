"""④ 产品质量 — 交付物靠不靠谱？"""

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


class P04ProductQuality(BasePageModule):
    PAGE_ID = "p04_product_quality"
    TITLE = "④ 产品质量"
    CORE_QUESTION = "交付物靠不靠谱？"
    DOMAIN_INDEX = 4

    def fetch_data(self, snap: dict) -> dict[str, Any]:
        quality_history = snap.get("quality_history", [])
        return {
            "close_trend": [
                {"label": str(r.get("stat_month", ""))[5:7] + "月",
                 "value": _num(r.get("bug_close_rate")) * 100}
                for r in quality_history
                if r.get("bug_close_rate") is not None
            ],
            "leak_trend": [
                {"label": str(r.get("stat_month", ""))[5:7] + "月",
                 "value": _num(r.get("bug_leak_rate")) * 100}
                for r in quality_history
                if r.get("bug_leak_rate") is not None
            ],
        }

    def build_context(self, data: dict[str, Any], metrics: dict) -> dict[str, Any]:
        defect_metric = metrics.get("defect_escape_rate")
        defect_val = f"{defect_metric.value:.1%}" if defect_metric and defect_metric.value is not None else "暂无数据"
        return {
            "charts": [
                {"id": "closeChart", "title": "Bug 关闭率趋势", "type": "line", "data": data["close_trend"]},
                {"id": "leakChart", "title": "缺陷逃逸率趋势", "type": "line", "data": data["leak_trend"]},
            ],
            "conclusion": f"当前缺陷逃逸率 {defect_val}；回归测试通过率尚未接入。",
            "action": "接入测试平台的回归用例总数及通过数，"
                      "将『回归测试通过率』与缺陷逃逸率放在同一质量判断链路中。",
            "decision_brief": f"缺陷逃逸率 {defect_val}；回归测试通过率未接入。",
        }
