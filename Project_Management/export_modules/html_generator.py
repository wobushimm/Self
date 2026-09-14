#!/usr/bin/env python3
"""Build the decision-oriented HTML/PDF PMO monthly report."""

from __future__ import annotations

import html
import json
import logging
import os
import re
from pathlib import Path

from data_modules.metrics_engine import create_metrics, number

logger = logging.getLogger(__name__)
_SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


# ── ECharts JS 内嵌（内网离线可用，找不到本地文件再走 CDN） ──

def _echarts_js_tag() -> str:
    """内嵌 echarts.min.js，内网打开 HTML 也能交互；找不到再走 CDN。"""
    candidates = [
        os.path.join(_SKILL_DIR, "..", "..", "data_report", "scripts",
                     "node_modules", "echarts", "dist", "echarts.min.js"),
        "/app/data/tmp/echarts-ssr/node_modules/echarts/dist/echarts.min.js",
        os.path.join(_SKILL_DIR, "..", "..", "..", "data", "tmp", "echarts-ssr",
                     "node_modules", "echarts", "dist", "echarts.min.js"),
        os.path.join(_SKILL_DIR, "..", "..", "..", "data", "output", "echarts.min.js"),
    ]
    for path in candidates:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                js = f.read()
            if "echarts" not in js[:80] and "echarts" not in js:
                continue
            return "<script>" + js.replace("</", "<\\/") + "</script>"
        except OSError as e:
            logger.debug("读取 echarts.min.js 失败 %s: %s", path, e)
    logger.warning("未找到本地 echarts.min.js，HTML 将尝试 CDN")
    return (
        '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js">'
        "</script>"
    )
from data_modules.analysis_engine import run_analysis


COLORS = {"normal": "green", "concern": "yellow", "intervention": "red", "unconfigured": "gray", "unavailable": "gray"}
LABELS = {"normal": "正常", "concern": "关注", "intervention": "干预", "unconfigured": "参考", "unavailable": "待补"}
TARGETS = {
    "req_verify_rate": "≥85%", "sprint_completion_rate": "≥85%", "workhour_deviation_rate": "±15%",
    "budget_deviation_rate": "±10%", "requirement_cost": "≤3人天",
    "defect_escape_rate": "≤3%", "bug_close_rate": "≥95%",
    "mttr": "P0≤4h P1≤24h", "rd_resource_input_ratio": "研发人力/组织总人数",
}

METRIC_FORMULAS = {
    "req_verify_rate": "验证通过需求数 / 总需求数",
    "sprint_completion_rate": "完成任务数 / 计划任务数",
    "workhour_deviation_rate": "(实际总工时 - 计划总工时) / 计划总工时",
    "budget_deviation_rate": "研发成本执行率(当月) - 1",
    "requirement_cost": "研发工时 / 总需求数（9h/人天）",
    "defect_escape_rate": "线上发现 Bug 数 / 总 Bug 数",
    "bug_close_rate": "已关闭 Bug 数 / 总 Bug 数",
    "mttr": "Bug 修复总时长 / Bug 总数",
    "rd_resource_input_ratio": "申报项目工时人数 / 配置的组织总人数",
}


# ── 页面选择工具 ──────────────────────────────────────

PAGE_ID_ALIASES = {
    "p01": "p01_fine_management",
    "p02": "p02_investment_deviation",
    "p03": "p03_staff_health",
    "p04": "p04_product_quality",
    "p05": "p05_bug_efficiency",
    "p06": "p06_value_delivery",
}

_ALL_PAGE_IDS = [
    "p01_fine_management", "p02_investment_deviation", "p03_staff_health",
    "p04_product_quality", "p05_bug_efficiency", "p06_value_delivery",
]

# 指标 → 所属领域页面映射
METRIC_PAGE_MAP = {
    "sprint_completion_rate": ["p01_fine_management"],
    "req_verify_rate": ["p01_fine_management", "p06_value_delivery"],
    "workhour_deviation_rate": ["p02_investment_deviation"],
    "budget_deviation_rate": ["p02_investment_deviation"],
    "rd_resource_input_ratio": ["p03_staff_health", "p06_value_delivery"],
    "defect_escape_rate": ["p04_product_quality"],
    "bug_close_rate": ["p04_product_quality", "p05_bug_efficiency"],
    "mttr": ["p05_bug_efficiency"],
    "requirement_cost": ["p06_value_delivery"],
}


def resolve_page_ids(page_ids):
    """解析页面 ID 列表，支持简写。返回 None 表示全部包含。"""
    if page_ids is None:
        return None
    if isinstance(page_ids, str):
        page_ids = [page_ids]
    result = []
    for pid in page_ids:
        pid_lower = pid.lower().strip()
        if pid_lower == "all":
            return None
        result.append(PAGE_ID_ALIASES.get(pid_lower, pid_lower))
    return result


def filter_rows_by_focus(data_list, focus, key_fields):
    """按关键词过滤字典列表中的行。"""
    if not focus or not data_list:
        return data_list
    focus_lower = focus.lower()
    return [
        d for d in data_list
        if any(focus_lower in str(d.get(f, "")).lower() for f in key_fields)
        or focus_lower in str(d).lower()
    ]


def filter_columns(data_list, columns):
    """按列名过滤字典列表。"""
    if not columns or not data_list:
        return data_list
    cols_lower = {c.lower() for c in columns}
    return [
        {k: v for k, v in d.items() if k == "label" or k.lower() in cols_lower}
        for d in data_list
    ]


def _num(value, default=0.0):
    parsed = number(value)
    return default if parsed is None else parsed


def _value(metric):
    if metric.display_value:
        return metric.display_value
    if metric.value is None:
        return "待接入"
    if metric.unit == "%":
        return f"{metric.value:.1%}"
    if metric.unit == "个":
        return f"{metric.value:.0f}"
    if metric.unit == "小时":
        return f"{metric.value:.2f} 小时"
    return f"{metric.value:.2f}"


def _state(metric):
    level = metric.warning_level
    return LABELS.get(level, "待补"), COLORS.get(level, "gray")


def _metric_card(metric, extra=""):
    label, color = _state(metric)
    formula = METRIC_FORMULAS.get(metric.code, "")
    return f'''<article class="metric-card {color}" data-metric="{metric.code}">
      <h3>{html.escape(metric.name)}</h3><strong class="metric-value">{html.escape(_value(metric))}</strong>
      <p>目标：{html.escape(TARGETS[metric.code])}<br>公式：{html.escape(formula)}<span class="dot {color}"></span></p>{extra}</article>'''


def _chart(chart_id, title, chart_type, data, *, empty=""):
    """Build an ECharts chart container div with embedded option JSON."""
    if empty:
        body = f'<div class="empty-chart">{html.escape(empty)}</div>'
    else:
        option = _build_echarts_option(chart_id, chart_type, data, title)
        payload = html.escape(json.dumps(option, ensure_ascii=False), quote=True)
        body = f'<div id="{chart_id}" class="chart-canvas" data-option="{payload}"></div>'
    return f'<article class="chart-card"><h3>{html.escape(title)}</h3>{body}</article>'


def _build_echarts_option(chart_id, chart_type, data, title=""):
    """Build ECharts option dict for the given chart type and data."""
    palette = ['#2864f3', '#15a34a', '#f2ae19', '#eb3b3b', '#8b5cf6', '#10b9a7']
    base = {
        "color": palette,
        "tooltip": {"trigger": "axis" if chart_type != "donut" else "item"},
        "grid": {"left": "12%", "right": "8%", "top": "15%", "bottom": "15%"},
    }

    if chart_type == "donut":
        base.update({
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"orient": "vertical", "right": "5%", "top": "center"},
            "series": [{
                "type": "pie", "radius": ["40%", "70%"],
                "center": ["35%", "50%"],
                "label": {"show": False},
                "emphasis": {"label": {"show": True, "fontSize": 14, "fontWeight": "bold"}},
                "data": [{"name": d["label"], "value": d["value"]} for d in data],
            }],
        })

    elif chart_type == "line":
        base.update({
            "xAxis": {"type": "category", "data": [d["label"] for d in data], "boundaryGap": False},
            "yAxis": {"type": "value"},
            "series": [{"type": "line", "data": [d.get("value", 0) for d in data],
                        "smooth": True, "areaStyle": {"opacity": 0.15}, "symbolSize": 8}],
        })

    elif chart_type == "barh":
        base["grid"]["left"] = "25%"
        base.update({
            "xAxis": {"type": "value"},
            "yAxis": {"type": "category", "data": [d["label"] for d in data], "inverse": True},
            "series": [{"type": "bar", "data": [d.get("value", 0) for d in data],
                        "barMaxWidth": 28, "itemStyle": {"borderRadius": [0, 4, 4, 0]}}],
        })

    elif chart_type == "grouped":
        if data:
            keys = [k for k in data[0].keys() if k != "label"]
            cats = [d["label"] for d in data]
            base.update({
                "legend": {"bottom": 0},
                "grid": {"bottom": "18%"},
                "xAxis": {"type": "category", "data": cats},
                "yAxis": {"type": "value"},
                "series": [
                    {"name": k, "type": "bar", "barMaxWidth": 24,
                     "data": [d.get(k, 0) for d in data]} for k in keys
                ],
            })

    else:  # bar
        base.update({
            "xAxis": {"type": "category", "data": [d["label"] for d in data]},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "data": [d.get("value", 0) for d in data],
                        "barMaxWidth": 36, "itemStyle": {"borderRadius": [4, 4, 0, 0]}}],
        })

    # 浏览器交互增强：工具箱（保存图片/还原/数据视图）
    base.setdefault("toolbox", {
        "right": 12,
        "feature": {
            "saveAsImage": {"title": "保存图片", "pixelRatio": 2},
            "restore": {"title": "还原"},
            "dataView": {"title": "数据视图", "readOnly": False,
                         "lang": ["数据视图", "关闭", "刷新"]},
        },
    })
    # 类别 > 8 个时加 dataZoom 滑块
    cats = []
    xa = base.get("xAxis")
    if isinstance(xa, dict):
        cats = xa.get("data") or []
    if len(cats) > 8:
        base.setdefault("dataZoom", [
            {"type": "inside"},
            {"type": "slider", "height": 18, "bottom": 8},
        ])
    return base


def _history_records(quality_cur_history, proj):
    """Build history records from quality_cur_history list, enriched with current month project data."""
    if not quality_cur_history:
        return []
    current_month = str(proj.get("stat_month", ""))
    rows = []
    for rec in quality_cur_history:
        row = dict(rec)
        # 月份字段兼容 settlement_month / stat_month
        row["statMonth"] = str(rec.get("settlement_month") or rec.get("stat_month", ""))
        row["requirementVerifyPassRate"] = rec.get("req_verify_rate")
        row["bugCloseRate"] = rec.get("bug_close_rate")
        row["bugLeakRate"] = rec.get("bug_leak_rate")
        is_current = row["statMonth"] == current_month
        # 优先使用历史记录自身字段，回退到 proj（仅当月）
        row["actualWorkhourMonth"] = number(rec.get("actual_workhour_month")) or (
            number(proj.get("actual_total_man_hour")) or 0 if is_current else 0)
        row["planWorkhourMonth"] = number(rec.get("estimated_workhour_month")) or (
            number(proj.get("plan_total_man_hour")) or 0 if is_current else 0)
        row["rdActualCostMonth"] = number(rec.get("rd_actual_cost_month")) or (
            number(proj.get("actual_dev_cost")) or 0 if is_current else 0)
        row["rdBudgetCostMonth"] = number(rec.get("rd_budget_cost_month")) or (
            number(proj.get("plan_dev_cost")) or 0 if is_current else 0)
        row["staffActualCostMonth"] = number(rec.get("staff_actual_cost_month")) or (
            number(proj.get("actual_staff_cost")) or 0 if is_current else 0)
        row["staffBudgetCostMonth"] = number(rec.get("staff_budget_cost_month")) or (
            number(proj.get("plan_staff_cost")) or 0 if is_current else 0)
        rows.append(row)
    return rows


def build_html_report_from_snapshot(snap, output_path: Path, pdf_filename="",
                                     page_ids=None, focus=None, columns=None,
                                     export_files=None,
                                     custom_templates=None) -> None:
    proj = snap.get("project", {})
    quality_cur = snap.get("quality_current", {})
    bug_detail = snap.get("bug_detail", {})
    staff_list = snap.get("staff_daily_workhours", [])
    quality_history = snap.get("quality_history", [])
    closed_bug_count = bug_detail.get("bug_closed_count") or 0
    total_bug_count = bug_detail.get("bug_total_count") or 0

    metrics, _ = create_metrics(snap)
    items = {item.code: item for item in metrics}
    # MTTR 已在 metrics_engine 中按 mttr_hours 目标计算预警等级，不再强制覆盖

    # ── 按页面选择 / 焦点过滤指标（用于概览卡片和预警表） ──
    resolved_for_filter = resolve_page_ids(page_ids)
    filtered_metrics = list(metrics)
    if resolved_for_filter is not None:
        allowed_codes = set()
        for pid in resolved_for_filter:
            for code, pages in METRIC_PAGE_MAP.items():
                if pid in pages:
                    allowed_codes.add(code)
        filtered_metrics = [m for m in metrics if m.code in allowed_codes]
    # focus 过滤：搜索指标名称、代码、公式，如果都不匹配则保留全部（不隐藏页面）
    if focus:
        focus_lower = focus.lower()
        focus_matched = [
            m for m in filtered_metrics
            if focus_lower in m.name.lower()
            or focus_lower in m.code.lower()
            or focus_lower in m.formula.lower()
        ]
        # 只有匹配到指标时才过滤，否则保留全部指标（不因 focus 隐藏页面）
        if focus_matched:
            filtered_metrics = focus_matched

    history = _history_records(quality_history, proj)
    project_name = str(proj.get("project_name", ""))
    month = str(proj.get("stat_month", ""))
    manager = str(proj.get("manager_name", "待补"))
    project_code = str(proj.get("project_code", proj.get("project_id", "待补")))
    product_line = str(proj.get("product_line") or proj.get("department_name") or "产品线")
    report_title = f"{project_name}_{month}_项目管理月度快报"

    counts = {level: sum(1 for item in filtered_metrics if item.warning_level == level) for level in ("normal", "concern", "intervention")}
    missing_count = sum(1 for item in filtered_metrics if item.warning_level == "unavailable")
    total_cost_actual = _num(proj.get("actual_dev_cost")) + _num(proj.get("actual_staff_cost"))
    total_cost_budget = _num(proj.get("plan_dev_cost")) + _num(proj.get("plan_staff_cost"))
    task_total, task_done, task_delay = (_num(proj.get(key)) for key in ("task_count", "task_finish_count", "task_delay_count"))
    high_difficulty = _num(proj.get("high_difficulty_task_count"))
    staff_sorted = sorted(staff_list, key=lambda r: _num(r.get("allocated_hour")), reverse=True)[:10]
    staff_top = [{"label": r.get("staff_name", "项目成员"), "value": _num(r.get("allocated_hour"))} for r in staff_sorted]

    # 计算团队健康度指标
    manhour_common = snap.get("manhour_common", [])
    attendance_records = snap.get("attendance_records", [])
    from collections import defaultdict
    daily_hours = defaultdict(float)
    for r in manhour_common:
        staff = r.get("staffName", "")
        date = r.get("reportDate", "")
        hours = _num(r.get("reportManHour"))
        if staff and date:
            daily_hours[(staff, date)] += hours
    overtime_hours = sum(max(0, total - 9) for total in daily_hours.values())
    total_available_hours = sum(_num(r.get("attendanceHour")) for r in attendance_records)
    proj_name_for_health = str(proj.get("project_name", ""))
    # 跨项目投入：(参与项目的人员总工时 - 投入到本项目的工时) / 参与项目的人员总工时
    staff_in_proj = set(
        r.get("staffName") for r in manhour_common
        if r.get("projectName") == proj_name_for_health and r.get("staffName")
    )
    total_staff_all_hours = sum(
        _num(r.get("reportManHour")) for r in manhour_common
        if r.get("staffName") in staff_in_proj
    )
    proj_hours = sum(
        _num(r.get("reportManHour")) for r in manhour_common
        if r.get("projectName") == proj_name_for_health
    )
    if total_staff_all_hours > 0:
        cross_project_ratio = (total_staff_all_hours - proj_hours) / total_staff_all_hours * 100
    else:
        cross_project_ratio = 0
    member_coverage = 100 if staff_list else 0

    # 人员总投入：本项目工时明细总和，换算为人月（21.75天×8h=174h/人月）
    total_investment_hours = sum(
        _num(r.get("reportManHour")) for r in manhour_common
        if r.get("projectName") == proj_name_for_health
    )
    total_investment = total_investment_hours / 174  # 人月
    # 平均考勤时长：优先使用平台看板数据，回退到自己计算
    avg_attendance = _num(proj.get("attendance_hour"))
    if not avg_attendance:
        proj_staff = set(
            r.get("staffName") for r in manhour_common
            if r.get("projectName") == proj_name_for_health and r.get("staffName")
        )
        proj_att_daily = defaultdict(float)
        for r in attendance_records:
            staff = r.get("staffName", "")
            date = r.get("workDate", "")
            if staff and date and staff in proj_staff:
                proj_att_daily[(staff, date)] += _num(r.get("attendanceHour"))
        proj_att_total = sum(proj_att_daily.values())
        proj_att_days = len(proj_att_daily)
        avg_attendance = proj_att_total / proj_att_days if proj_att_days else 0

    executive_cards = "".join(_metric_card(items[code]) for code in (
        "workhour_deviation_rate", "budget_deviation_rate", "sprint_completion_rate", "defect_escape_rate",
        "bug_close_rate", "req_verify_rate", "requirement_cost", "mttr", "rd_resource_input_ratio",
    ) if items[code] in filtered_metrics)
    # 提取项目参与人员数（用于 JS 校准重算）
    _rd_note = items['rd_resource_input_ratio'].note
    _proj_staff_match = re.search(r'\u4eba\u5458\u603b\u6570\uff08(\d+)\u4eba\uff09', _rd_note)
    if not _proj_staff_match:
        _proj_staff_match = re.search(r'=\s*(\d+)', _rd_note)
    _proj_staff_count = int(_proj_staff_match.group(1)) if _proj_staff_match else 0
    verification = [{"label": str(row["statMonth"])[5:7] + "月", "value": _num(row.get("requirementVerifyPassRate")) * 100} for row in history if number(row.get("requirementVerifyPassRate")) is not None]
    workhour = [{"label": str(row["statMonth"])[5:7] + "月", "actual": _num(row.get("actualWorkhourMonth")), "plan": _num(row.get("planWorkhourMonth"))} for row in history]
    costs = [{"label": str(row["statMonth"])[5:7] + "月", "研发实际": _num(row.get("rdActualCostMonth")), "研发预算": _num(row.get("rdBudgetCostMonth")), "人力实际": _num(row.get("staffActualCostMonth")), "人力预算": _num(row.get("staffBudgetCostMonth"))} for row in history]
    quality_cur_close = [{"label": str(row["statMonth"])[5:7] + "月", "value": _num(row.get("bugCloseRate")) * 100} for row in history if number(row.get("bugCloseRate")) is not None]
    quality_cur_leak = [{"label": str(row["statMonth"])[5:7] + "月", "value": _num(row.get("bugLeakRate")) * 100} for row in history if number(row.get("bugLeakRate")) is not None]

    # ── 预警表行（支持 focus 行过滤 + columns 列过滤） ──
    alert_header = ["指标名称", "当前值", "目标值", "计算口径", "预警等级"]
    alert_rows = []
    for metric in filtered_metrics:
        row_vals = [metric.name, _value(metric), TARGETS.get(metric.code, ""), metric.formula, _state(metric)[0]]
        alert_rows.append(row_vals)
    # focus 额外过滤：在预警表行文本中搜索关键词
    if focus:
        focus_lower = focus.lower()
        alert_rows = [r for r in alert_rows if any(focus_lower in str(v).lower() for v in r)]
    # columns 列过滤
    if columns:
        cols_lower = {c.lower() for c in columns}
        col_indices = [i for i, h in enumerate(alert_header) if h.lower() in cols_lower]
        # 始终保留"指标名称"列（第 0 列）和"预警等级"列（最后一列）
        col_indices = sorted(set([0, len(alert_header) - 1] + col_indices))
        alert_header = [alert_header[i] for i in col_indices]
        alert_rows = [[row[i] for i in col_indices] for row in alert_rows]
    alerts_html = "<thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in alert_header) + "</tr></thead><tbody>"
    for row in alert_rows:
        state_label = row[-1]  # 预警等级始终是最后一列
        state_class = {"正常": "green", "关注": "yellow", "干预": "red", "待补": "gray"}.get(state_label, "gray")
        alerts_html += "<tr>"
        for idx, val in enumerate(row):
            if idx == len(row) - 1:
                alerts_html += f'<td><span class="tag {state_class}">{html.escape(str(val))}</span></td>'
            else:
                alerts_html += f'<td contenteditable="true" spellcheck="false">{html.escape(str(val))}</td>'
        alerts_html += "</tr>"
    alerts_html += "</tbody>"
    # ── 生成预警清单下方的动态指标说明 ──
    _concern_items = [m for m in filtered_metrics if m.warning_level == "concern"]
    _intervention_items = [m for m in filtered_metrics if m.warning_level == "intervention"]
    _unavailable_items = [m for m in filtered_metrics if m.warning_level == "unavailable"]
    _normal_items = [m for m in filtered_metrics if m.warning_level == "normal"]
    _summary_parts = []
    if _normal_items:
        _summary_parts.append(f"{'、'.join(m.name for m in _normal_items)} 等 {len(_normal_items)} 项指标处于正常区间。")
    if _intervention_items:
        _summary_parts.append(f"⚠ {'、'.join(m.name for m in _intervention_items)} 当前已超阈值，需立即关注并排查原因。")
    elif _concern_items:
        _summary_parts.append(f"⚠ {'、'.join(m.name for m in _concern_items)} 接近或超出预警阈值，建议重点跟踪。")
    if _unavailable_items:
        _summary_parts.append(f"{'、'.join(m.name for m in _unavailable_items)} 数据待补齐，当前标记为\u201c待补\u201d，不代表项目异常。")
    if not _summary_parts:
        _summary_parts.append("本期各项指标数据已接入，整体状态平稳。")
    alert_summary_text = "".join(_summary_parts)

    # ── 指标深度分析 ──
    analysis = run_analysis(
        filtered_metrics, TARGETS, quality_history,
        custom_templates=custom_templates,
    )
    _health = analysis["health"]
    _priority = analysis["priority"]
    _suggestions = analysis["suggestions"]
    _score_color = "#16a34a" if _health["score"] >= 70 else "#d68c00" if _health["score"] >= 50 else "#e32828"
    _analysis_html = f'<div style="margin-top:28px"><div class="section-head"><div><h2>指标深度分析</h2><p>基于目标阈值的差距、趋势与优先级分析，辅助项目例会决策。</p></div><div class="decision">综合健康评分 {_health["score"]} 分（{_health["grade"]}级）：{html.escape(_health["grade_text"])}。</div></div><div style="display:grid;grid-template-columns:200px 1fr;gap:18px;margin-bottom:18px"><div style="background:#fff;border-radius:13px;padding:24px;text-align:center;box-shadow:0 4px 11px #2137550d"><div style="font-size:13px;color:#667895;margin-bottom:8px">综合健康评分</div><div style="font-size:48px;font-weight:800;color:{_score_color};line-height:1">{_health["score"]}</div><div style="font-size:22px;font-weight:700;color:{_score_color};margin:4px 0">{_health["grade"]}</div><div style="font-size:12px;color:#667895;line-height:1.5;margin-top:8px">{html.escape(_health["grade_text"])}</div></div><div style="background:#fff;border-radius:13px;padding:20px;box-shadow:0 4px 11px #2137550d;overflow:auto"><table style="border-collapse:collapse;width:100%;font-size:13px"><thead><tr><th style="background:#edf3fd;color:#3c577e;text-align:left;padding:8px 10px">优先级</th><th style="background:#edf3fd;color:#3c577e;text-align:left;padding:8px 10px">指标</th><th style="background:#edf3fd;color:#3c577e;text-align:left;padding:8px 10px">状态</th><th style="background:#edf3fd;color:#3c577e;text-align:left;padding:8px 10px">趋势</th><th style="background:#edf3fd;color:#3c577e;text-align:left;padding:8px 10px">紧急度</th><th style="background:#edf3fd;color:#3c577e;text-align:left;padding:8px 10px">差距</th></tr></thead><tbody>'
    for _p in _priority:
        _sl = {"normal": "正常", "concern": "关注", "intervention": "干预", "unavailable": "待补"}.get(_p.status, "待补")
        _sc = {"normal": "green", "concern": "yellow", "intervention": "red", "unavailable": "gray"}.get(_p.status, "gray")
        _uc = {"立即处理": "red", "重点关注": "yellow", "数据补齐": "gray", "持续观察": "green"}.get(_p.action_urgency, "gray")
        _analysis_html += f'<tr><td style="padding:7px 10px;border-bottom:1px solid #e4eaf2">{_p.rank}</td><td style="padding:7px 10px;border-bottom:1px solid #e4eaf2">{html.escape(_p.name)}</td><td style="padding:7px 10px;border-bottom:1px solid #e4eaf2"><span class="tag {_sc}">{_sl}</span></td><td style="padding:7px 10px;border-bottom:1px solid #e4eaf2">{html.escape(_p.trend_verdict)}</td><td style="padding:7px 10px;border-bottom:1px solid #e4eaf2"><span class="tag {_uc}">{html.escape(_p.action_urgency)}</span></td><td style="padding:7px 10px;border-bottom:1px solid #e4eaf2;color:#61718a">{html.escape(_p.reason)}</td></tr>'
    _analysis_html += '</tbody></table></div></div>'
    if _suggestions:
        # 按预警状态分组归纳
        from collections import OrderedDict as _OD
        _group_order = _OD([("intervention", []), ("concern", []), ("unavailable", []), ("normal", [])])
        for _s in _suggestions:
            _g = _s["status"] if _s["status"] in _group_order else "normal"
            _group_order[_g].append(_s)
        _group_meta = {
            "intervention": ("需干预", "#e32828", "red"),
            "concern": ("需关注", "#d68c00", "yellow"),
            "unavailable": ("数据待补", "#8b98aa", "gray"),
            "normal": ("正常", "#16a34a", "green"),
        }
        _active_groups = [(k, v) for k, v in _group_order.items() if v]
        if _active_groups:
            _analysis_html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">'
            for _gk, _gitems in _active_groups:
                _g_label, _g_border, _g_tag = _group_meta[_gk]
                _analysis_html += f'<div style="background:#fff;border-radius:13px;padding:18px 20px;box-shadow:0 4px 11px #2137550d;border-left:4px solid {_g_border}"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><span style="font-weight:700;font-size:15px;color:#1c2941">{_g_label}</span><span class="tag {_g_tag}">{len(_gitems)} 项</span></div>'
                for _idx, _s in enumerate(_gitems):
                    _analysis_html += f'<div style="{"margin-top:12px;padding-top:12px;border-top:1px solid #eef2f7" if _idx else ""}"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-weight:600;color:#1c2941;font-size:13px">{html.escape(_s["name"])}</span><span style="font-size:12px;color:#8b98aa">{html.escape(_s["trend"])} · {html.escape(_s["gap_display"])}</span></div><p style="font-size:13px;color:#5c6e88;line-height:1.6;margin:0">{html.escape(_s["advice"])}</p></div>'
                _analysis_html += '</div>'
            _analysis_html += '</div>'
    _analysis_html += '</div>'
    # 将建议区块包裹在 contenteditable 分析块 + 批注栏中
    _suggestions_start = _analysis_html.find('<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">')
    if _suggestions_start >= 0:
        _wrap = '<div class="analysis-block"><div class="analysis-main"><div class="analysis-label"><b>数据分析：</b><span class="analysis-tip">可编辑 · 划选后可改色或加批注</span></div><div class="analysis-body" contenteditable="true" spellcheck="false">'
        _analysis_html = _analysis_html[:_suggestions_start] + _wrap + _analysis_html[_suggestions_start:]
        _analysis_html += '</div></div></div>'

    # ── 分板块数据校准面板（各板块底部，公式+数据+可编辑） ──
    _calib_fields = {
        'company_headcount': {'label': '公司总人数', 'value': 0, 'unit': '人', 'decimals': 0},
        'total_req_count': {'label': '总需求数', 'value': _num(snap.get('resolved_total_requirement_count') or proj.get('totalReqCount') or task_done), 'unit': '个', 'decimals': 0},
        'actual_rd_man_hour': {'label': '研发工时', 'value': _num(proj.get('actual_rd_man_hour')), 'unit': '小时', 'decimals': 2},
        'task_count': {'label': '任务总数', 'value': _num(proj.get('task_count')), 'unit': '个', 'decimals': 0},
        'task_finish_count': {'label': '完成任务数', 'value': _num(proj.get('task_finish_count')), 'unit': '个', 'decimals': 0},
        'bug_total_count': {'label': 'Bug 总数', 'value': _num(bug_detail.get('bug_total_count')), 'unit': '个', 'decimals': 0},
        'field_bug_count': {'label': '线上 Bug 数', 'value': _num(bug_detail.get('field_bug_count')), 'unit': '个', 'decimals': 0},
        'bug_closed_count': {'label': '已关闭 Bug 数', 'value': _num(bug_detail.get('bug_closed_count')), 'unit': '个', 'decimals': 0},
        'bug_resolve_duration_sec': {'label': 'Bug 修复总时长', 'value': _num(bug_detail.get('bug_resolve_duration_exclude_reject_third') or bug_detail.get('bug_resolve_duration_seconds')), 'unit': '秒', 'decimals': 0},
        'actual_total_man_hour': {'label': '实际总工时', 'value': _num(proj.get('actual_total_man_hour')), 'unit': '小时', 'decimals': 2},
        'plan_total_man_hour': {'label': '计划总工时', 'value': _num(proj.get('plan_total_man_hour')), 'unit': '小时', 'decimals': 2},
        'rd_actual_cost': {'label': '研发成本实际', 'value': total_cost_actual, 'unit': '元', 'decimals': 0},
        'rd_budget_cost': {'label': '研发成本预算', 'value': total_cost_budget, 'unit': '元', 'decimals': 0},
        'project_staff_count': {'label': '项目人员数', 'value': _proj_staff_count, 'unit': '人', 'decimals': 0},
        'monthly_work_days': {'label': '月工作天数', 'value': 21.75, 'unit': '天', 'decimals': 2},
        'total_investment_hours': {'label': '本项目投入工时', 'value': total_investment_hours, 'unit': '小时', 'decimals': 1},
        'overtime_hours': {'label': '加班工时', 'value': overtime_hours, 'unit': '小时', 'decimals': 1},
        'total_staff_all_hours': {'label': '人员跨项目总工时', 'value': total_staff_all_hours, 'unit': '小时', 'decimals': 1},
    }
    def _calib_panel(section_id, title, groups, fields=_calib_fields):
        rows = ''
        for group_name, formula, field_ids in groups:
            rows += f'<tr><td style="padding:8px 10px;border-bottom:1px solid #eef2f7;font-weight:600;color:#3c577e;font-size:13px" colspan="3">{group_name}</td></tr>'
            rows += f'<tr><td style="padding:4px 10px 8px 18px;border-bottom:2px solid #e4eaf2;color:#8b98aa;font-size:12px;font-family:monospace" colspan="3">{formula}</td></tr>'
            for fid in field_ids:
                fd = fields[fid]
                v = f"{fd['value']:.{fd['decimals']}f}" if fd['value'] else '0'
                rows += f'<tr><td style="padding:6px 10px 6px 28px;border-bottom:1px solid #eef2f7;font-size:13px;color:#667895">{fd["label"]}</td><td style="padding:6px 4px;border-bottom:1px solid #eef2f7"><input type="number" id="calib-{section_id}_{fid}" value="{v}" step="any" style="width:95px;padding:5px 8px;border:1px solid #d3deee;border-radius:6px;font-size:13px;text-align:right" oninput="__calibSync(this);__recalc()"></td><td style="padding:6px 10px;border-bottom:1px solid #eef2f7;font-size:12px;color:#8b98aa">{fd["unit"]}</td></tr>'
        return f'<details style="margin-top:18px"><summary style="cursor:pointer;font-weight:700;color:#3c577e;font-size:14px;padding:8px 0;user-select:none">{title}</summary><div style="margin-top:8px;background:#fff;border-radius:13px;padding:16px 8px;box-shadow:0 4px 11px #2137550d;overflow:auto"><table style="border-collapse:collapse;width:100%">{rows}</table></div></details>'
    _calib_p01 = _calib_panel('p01', '指标计算口径与数据校准', [('Sprint 完成率', '完成任务数 / 任务总数', ['task_count', 'task_finish_count'])])
    _calib_p04 = _calib_panel('p04', '指标计算口径与数据校准', [('缺陷逃逸率', '线上 Bug 数 / Bug 总数', ['bug_total_count', 'field_bug_count']), ('Bug 关闭率', '已关闭 Bug 数 / Bug 总数', ['bug_closed_count'])])
    _calib_p05 = _calib_panel('p05', '指标计算口径与数据校准', [('MTTR', 'Bug 修复总时长 / Bug 总数 / 3600', ['bug_total_count', 'bug_resolve_duration_sec'])])
    _calib_p02 = _calib_panel('p02', '指标计算口径与数据校准', [('工时偏差率', '(实际总工时 - 计划总工时) / 计划总工时', ['actual_total_man_hour', 'plan_total_man_hour']), ('预算偏差率', '研发成本实际 / 研发成本预算 - 1', ['rd_actual_cost', 'rd_budget_cost'])])
    _calib_p03 = _calib_panel('p03', '指标计算口径与数据校准', [('总可用工时', '项目人员数 × 月工作天数 × 9', ['project_staff_count', 'monthly_work_days']), ('人员总投入(人月)', '本项目投入工时 / 174', ['total_investment_hours']), ('加班工时', '每人每天 > 9h 的累计', ['overtime_hours']), ('跨项目投入占比', '(人员总工时 - 本项目工时) / 人员总工时', ['total_staff_all_hours', 'actual_total_man_hour'])])
    _calib_p06 = _calib_panel('p06', '指标计算口径与数据校准', [('研发资源投入占比', '项目参与人员数 / 公司总人数', ['project_staff_count', 'company_headcount']), ('需求成本', '研发工时 / 总需求数（9h/人天）', ['total_req_count', 'actual_rd_man_hour'])])

    # ── 浏览器内交互：表格/分析可改、划线批注、字体颜色、本页另存为 ──
    _PAGE_SAVE_JS = r"""
(function(){
  function inEditable(el){
    return !!(el && el.closest && el.closest('[contenteditable="true"]'));
  }
  function getSelectionCtx(){
    var sel=window.getSelection();
    if(!sel || !sel.rangeCount || sel.isCollapsed) return null;
    var page=sel.anchorNode&&(sel.anchorNode.nodeType===1?sel.anchorNode:sel.anchorNode.parentElement);
    page=page&&page.closest?page.closest('[data-pmopage]'):null;
    return {sel:sel, range:sel.getRangeAt(0), pageLabel:page?((page.querySelector('h2')||{}).textContent||'').trim():''};
  }
  document.addEventListener('keydown', function(e){
    if(e.key!=='Enter') return;
    var td=e.target && e.target.closest && e.target.closest('td[contenteditable]');
    if(!td) return;
    e.preventDefault();
    td.blur();
  });
  document.addEventListener('paste', function(e){
    if(!inEditable(e.target)) return;
    e.preventDefault();
    var t=(e.clipboardData||window.clipboardData).getData('text/plain')||'';
    document.execCommand('insertText', false, t);
  });
  function applyForeColor(color){
    var ctx=getSelectionCtx();
    if(!ctx){ alert('请先划选要改色的文字'); return; }
    try{ document.execCommand('styleWithCSS', false, true); }catch(_e){}
    document.execCommand('foreColor', false, color);
  }
  function addAnnotation(){
    var ctx=getSelectionCtx();
    if(!ctx){ alert('请先在页面任意位置划选要批注的文字'); return; }
    var note=window.prompt('批注内容：','');
    if(note===null) return;
    note=String(note||'').trim();
    if(!note){ alert('批注内容不能为空'); return; }
    var id='anno-'+Date.now()+'-'+Math.floor(Math.random()*1000);
    var pageLabel=ctx.pageLabel||'';
    var mark=document.createElement('mark');
    mark.className='anno-hl';
    mark.setAttribute('data-anno-id', id);
    mark.title=(pageLabel?'['+pageLabel+'] ':'')+note;
    try{ ctx.range.surroundContents(mark); }
    catch(_e){ var frag=ctx.range.extractContents(); mark.appendChild(frag); ctx.range.insertNode(mark); }
    var rail=document.getElementById('pmo-anno-rail');
    if(rail){
      var item=document.createElement('div'); item.className='anno-item'; item.setAttribute('data-anno-id', id); item.setAttribute('data-page', pageLabel);
      var plabel=document.createElement('div'); plabel.className='anno-page-label'; plabel.textContent=pageLabel||'总览';
      var text=document.createElement('div'); text.className='anno-text'; text.setAttribute('contenteditable','true'); text.setAttribute('spellcheck','false'); text.textContent=note;
      var meta=document.createElement('div'); meta.className='anno-meta';
      meta.innerHTML='<button type="button" class="anno-jump">定位</button><button type="button" class="anno-del">删除</button>';
      item.appendChild(plabel); item.appendChild(text); item.appendChild(meta); rail.appendChild(item);
    }
    ctx.sel.removeAllRanges();
  }
  document.addEventListener('click', function(e){
    var jump=e.target && e.target.closest && e.target.closest('.anno-jump');
    if(jump){ var item=jump.closest('.anno-item'); var id=item && item.getAttribute('data-anno-id'); if(!id) return;
      var m=document.querySelector('mark.anno-hl[data-anno-id="'+id+'"]');
      if(m){ m.scrollIntoView({behavior:'smooth',block:'center'}); m.classList.add('anno-flash'); setTimeout(function(){m.classList.remove('anno-flash');},1200); } return; }
    var del=e.target && e.target.closest && e.target.closest('.anno-del');
    if(del){ var item2=del.closest('.anno-item'); var id2=item2 && item2.getAttribute('data-anno-id'); if(!id2) return;
      var m2=document.querySelector('mark.anno-hl[data-anno-id="'+id2+'"]');
      if(m2){ var parent=m2.parentNode; while(m2.firstChild) parent.insertBefore(m2.firstChild, m2); parent.removeChild(m2); parent.normalize(); }
      if(item2) item2.remove(); }
  });
  var colorBar=document.getElementById('color-bar');
  if(colorBar){ colorBar.addEventListener('click', function(e){ var btn=e.target && e.target.closest && e.target.closest('[data-color]'); if(!btn) return; applyForeColor(btn.getAttribute('data-color')); }); }
  var btnAnno=document.getElementById('btn-add-anno');
  if(btnAnno) btnAnno.addEventListener('click', addAnnotation);
  var btn=document.getElementById('btn-save-html');
  if(!btn) return;
  btn.addEventListener('click', function(){
    if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
    var clone=document.documentElement.cloneNode(true);
    var boxes=clone.querySelectorAll('.chart-canvas');
    for(var i=0;i<boxes.length;i++){ boxes[i].removeAttribute('_echarts_instance_'); boxes[i].innerHTML=''; }
    var flashes=clone.querySelectorAll('.anno-flash');
    for(var j=0;j<flashes.length;j++) flashes[j].classList.remove('anno-flash');
    var html='<!DOCTYPE html>\n'+clone.outerHTML;
    var blob=new Blob([html], {type:'text/html;charset=utf-8'});
    var a=document.createElement('a');
    var name=(document.title||'项目管理快报').replace(/[\\/:*?"<>|]/g,'_')+'.html';
    a.href=URL.createObjectURL(blob); a.download=name;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){URL.revokeObjectURL(a.href);}, 2000);
  });
})();
"""

    # ── 构建导出按钮（服务端生成的真实文件下载链接）──
    export_files = export_files or {}
    _btns = []
    if export_files.get('pdf'):
        _btns.append(f'<a class="button pdf" href="{html.escape(export_files["pdf"])}" download>▤ 导出PDF</a>')
    if export_files.get('docx'):
        _btns.append(f'<a class="button word" href="{html.escape(export_files["docx"])}" download>▣ 导出Word</a>')
    if export_files.get('pptx'):
        _btns.append(f'<a class="button ppt" href="{html.escape(export_files["pptx"])}" download>▥ 导出PPT</a>')
    export_buttons = ''  # 导出按钮不在 HTML 页面展示，构建逻辑保留供后续启用

    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{_echarts_js_tag()}
<title>{html.escape(report_title)}</title><style>
:root{{--blue:#2458d7;--navy:#173e79;--ink:#1c2941;--muted:#667895;--paper:#fff;--bg:#eff3f8;--green:#16a34a;--yellow:#d68c00;--red:#e32828;--gray:#8b98aa}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;font-size:15px}}button,.button{{font:inherit;cursor:pointer;text-decoration:none}}.toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:0 0 16px;padding:10px 12px;background:#F2F7FC;border:1px solid #d6e3f0;border-radius:6px;position:sticky;top:0;z-index:20}}.toolbar button{{background:#1F497D;color:#fff;border:0;border-radius:4px;padding:8px 14px;font-size:14px;cursor:pointer}}.toolbar button:hover{{background:#16365a}}.toolbar .btn-secondary{{background:#5B7AA5}}.color-bar{{display:inline-flex;align-items:center;gap:6px;padding:2px 4px}}.color-bar .color-label{{font-size:13px;color:#456;margin-right:2px}}.color-swatch{{width:22px;height:22px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 1px #99a;cursor:pointer;padding:0}}.toolbar button.color-swatch{{padding:0;min-width:22px}}.color-swatch:hover{{transform:scale(1.08)}}.hint{{color:#666;font-size:13px;margin:0;flex:1;min-width:200px}}td[contenteditable],.analysis-body[contenteditable],.anno-text[contenteditable],[data-metric],[data-pmopage] .decision,[data-pmopage] .note-card{{cursor:text;outline:none}}td[contenteditable]:focus,.analysis-body[contenteditable]:focus,.anno-text[contenteditable]:focus,[data-metric]:focus,[data-pmopage] .decision:focus,[data-pmopage] .note-card:focus{{background:#FFF8E1;box-shadow:inset 0 0 0 2px #1F497D}}.analysis-block{{display:flex;gap:14px;align-items:stretch;margin:12px 0}}.analysis-main{{flex:1;min-width:0;background:#f0f7ff;border-radius:8px;padding:12px 14px;border-left:4px solid #2E86AB}}.analysis-label{{font-size:14px;color:#1F497D;margin-bottom:8px}}.analysis-tip{{margin-left:8px;font-size:12px;color:#7a8aa0;font-weight:400}}.analysis-body{{line-height:1.7;min-height:3.2em;font-size:14px;color:#333}}.analysis-body:empty:before{{content:attr(data-placeholder);color:#aab}}.anno-rail{{width:220px;flex-shrink:0;display:none;flex-direction:column;gap:8px}}.anno-rail:not(:empty){{display:flex}}.anno-item{{background:#FFF9E8;border:1px solid #F0D78C;border-radius:8px;padding:8px 10px;font-size:12px}}.anno-page-label{{font-size:11px;color:#fff;background:#5B7AA5;border-radius:3px;padding:1px 6px;margin-bottom:4px;display:inline-block}}.anno-text{{line-height:1.5;margin-bottom:6px;min-height:1.4em}}.anno-meta{{display:flex;gap:6px}}.anno-meta button{{background:#eee;border:0;border-radius:4px;padding:3px 8px;font-size:12px;cursor:pointer;color:#345}}.anno-meta button:hover{{background:#ddd}}mark.anno-hl{{background:#FFE082;padding:0 2px;border-radius:2px;cursor:help}}mark.anno-flash{{outline:2px solid #F57C00}}.hero{{background:linear-gradient(118deg,#173e79,#2864f3);color:#fff;padding:46px max(5vw,32px) 38px}}.hero-inner,.page-inner{{max-width:1220px;margin:auto}}.hero-grid{{display:grid;grid-template-columns:1.55fr repeat(3,.85fr);gap:22px;align-items:start}}.hero h1{{font-size:38px;letter-spacing:1px;margin:0 0 15px}}.hero .project{{font-size:20px;line-height:1.5;color:#e6eeff}}.hero-meta{{font-size:16px;line-height:1.5;padding-top:8px}}.hero-meta b{{display:block;font-size:19px;margin-top:4px}}.hero-actions{{display:flex;gap:13px;margin-top:25px}}.hero-actions .button{{font-size:16px;padding:12px 22px;border-radius:9px;font-weight:800}}.hero-actions .pdf{{border:0;background:#fff;color:#2458d7}}.hero-actions .word{{border:2px solid #9bb8ff;background:#ffffff16;color:#fff}}.hero-actions .ppt{{border:2px solid #ffb87a;background:#ffffff16;color:#fff}}.page{{padding:29px max(5vw,32px)}}.summary-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}}.status-card{{border-radius:13px;padding:19px 25px;border:2px solid;background:#fff}}.status-card b{{display:block;font-size:35px;margin-left:56px;margin-top:-43px}}.status-card p{{font-weight:700;color:#667895;margin:13px 0 0 56px}}.status-card:before{{content:"";display:block;width:40px;height:40px;border-radius:50%;box-shadow:inset 0 3px 5px #ffffffa8,0 2px 5px #0002}}.status-card.green{{background:#effdf4;border-color:#79e6a5}}.status-card.green:before{{background:#0bbd27}}.status-card.yellow{{background:#fffbd9;border-color:#f4d235}}.status-card.yellow:before{{background:#ffd318}}.status-card.red{{background:#fff0f0;border-color:#ff9b9b}}.status-card.red:before{{background:#e22222}}.status-card.gray{{background:#f7f9fc;border-color:#cfd9e8}}.status-card.gray:before{{background:#96a5ba}}.overview{{padding-top:32px}}.metric-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}}.metric-card{{background:#fff;border-radius:14px;padding:23px 25px;min-height:202px;box-shadow:0 6px 13px #233e6e12;border-left:6px solid var(--gray)}}.metric-card.green{{border-color:var(--green)}}.metric-card.yellow{{border-color:var(--yellow)}}.metric-card.red{{border-color:var(--red)}}.metric-card.gray{{border-color:var(--gray)}}.metric-card h3{{font-size:19px;color:#62748f;font-weight:500;line-height:1.35;margin:0 0 20px}}.metric-card strong{{display:block;font-size:35px;line-height:1.05;color:var(--ink);letter-spacing:-1px;word-break:break-word}}.metric-card.green strong{{color:var(--green)}}.metric-card.yellow strong{{color:var(--yellow)}}.metric-card.red strong{{color:var(--red)}}.metric-card p{{font-size:15px;color:#657793;margin:17px 0 0;display:flex;align-items:center;gap:8px}}.dot{{width:22px;height:22px;border-radius:50%;display:inline-block;box-shadow:inset 0 3px 5px #ffffffa0,0 1px 3px #0003}}.dot.green{{background:var(--green)}}.dot.yellow{{background:#ffcd16}}.dot.red{{background:var(--red)}}.dot.gray{{background:var(--gray)}}.section-head{{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}}.section-head h2{{margin:0;font-size:27px}}.section-head p{{color:var(--muted);margin:5px 0 0;font-size:16px}}.section-head .decision{{max-width:43%;padding:10px 14px;border-radius:9px;background:#edf4ff;color:#35598d;font-size:14px;line-height:1.45}}.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.chart-card,.note-card,.table-card{{background:#fff;border-radius:13px;box-shadow:0 4px 11px #2137550d;padding:20px}}.chart-card h3,.note-card h3{{margin:0 0 14px;font-size:18px}}.chart-canvas{{width:100%;height:265px;display:block}}.empty-chart{{height:265px;border:2px dashed #cbd5e1;border-radius:9px;color:#718096;display:grid;place-items:center;text-align:center;padding:20px;line-height:1.6}}.two-notes{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}}.note-card p{{line-height:1.65;color:#5c6e88;margin:0}}.note-card .big{{font-size:34px;color:#2458d7;font-weight:800;margin:5px 0 8px}}.alert-table{{border-collapse:collapse;width:100%;font-size:13px}}.alert-table th{{background:#edf3fd;color:#3c577e;text-align:left}}.alert-table th,.alert-table td{{padding:10px 9px;border-bottom:1px solid #e4eaf2;vertical-align:top;line-height:1.45}}.alert-table td:nth-child(4){{color:#61718a;max-width:380px}}.tag{{border-radius:999px;padding:4px 9px;font-weight:700;white-space:nowrap}}.tag.green{{color:#0d8231;background:#e9f9ee}}.tag.yellow{{color:#996500;background:#fff8dc}}.tag.red{{color:#c51f1f;background:#ffebeb}}.tag.gray{{color:#62748f;background:#edf1f6}}.coverage{{margin-top:14px;color:#687994;line-height:1.6}}.footer{{padding:21px 5vw;background:#172033;color:#d9e2f1;text-align:center;font-size:12px}}.pmo-anno-rail{{position:fixed;right:12px;top:70px;width:240px;max-height:calc(100vh - 90px);overflow-y:auto;display:flex;flex-direction:column;gap:8px;z-index:15;pointer-events:auto}}.pmo-anno-rail:empty{{display:none}}@media(max-width:980px){{.hero-grid{{grid-template-columns:1fr 1fr}}.hero-grid>:first-child{{grid-column:span 2}}.summary-grid,.metric-grid{{grid-template-columns:1fr 1fr}}.chart-grid{{grid-template-columns:1fr}}.section-head{{display:block}}.section-head .decision{{max-width:none;margin-top:13px}}}}@media(max-width:600px){{.hero,.page{{padding-left:18px;padding-right:18px}}.hero h1{{font-size:30px}}.hero-grid,.summary-grid,.metric-grid,.two-notes{{grid-template-columns:1fr}}.hero-grid>:first-child{{grid-column:auto}}.metric-card{{min-height:190px}}.metric-card strong{{font-size:30px}}.table-card{{overflow:auto;padding:10px}}.alert-table{{min-width:780px}}}}@page{{size:A4 landscape;margin:0}}@media print{{body{{background:#fff;font-size:11px}}.toolbar{{display:none}}.hero{{padding:13mm 12mm 9mm}}.hero h1{{font-size:33px}}.hero .project{{font-size:17px}}.hero-meta{{font-size:13px}}.hero-meta b{{font-size:16px}}.hero-actions{{display:none}}.page{{padding:8mm 10mm;break-after:page}}.overview{{padding-top:8mm}}.summary-grid{{gap:9px}}.status-card{{padding:13px 16px}}.status-card b{{font-size:27px}}.status-card p{{font-size:13px}}.metric-grid{{gap:10px}}.metric-card{{min-height:150px;padding:16px 18px;border-left-width:5px}}.metric-card h3{{font-size:16px;margin-bottom:13px}}.metric-card strong{{font-size:26px}}.metric-card p{{font-size:12px;margin-top:11px}}.dot{{width:17px;height:17px}}.section-head h2{{font-size:23px}}.section-head p{{font-size:13px}}.section-head .decision{{font-size:11px}}.chart-grid{{gap:12px}}.chart-card,.note-card,.table-card{{padding:13px;border-radius:9px}}.chart-card h3,.note-card h3{{font-size:15px;margin-bottom:7px}}.chart-canvas,.empty-chart{{height:175px}}.two-notes{{gap:12px;margin-top:12px}}.note-card .big{{font-size:26px}}.note-card p{{font-size:12px}}.alert-table{{font-size:9px}}.alert-table th,.alert-table td{{padding:6px 5px}}.footer{{display:none}}}}
</style></head><body>
<div class="toolbar"><button type="button" id="btn-save-html">保存本页</button><button type="button" id="btn-add-anno" class="btn-secondary">加批注</button><div class="color-bar" id="color-bar" title="先选中页面任意文字再点颜色"><span class="color-label">字体颜色</span><button type="button" class="color-swatch" data-color="#222222" style="background:#222" title="黑色"></button><button type="button" class="color-swatch" data-color="#C62828" style="background:#C62828" title="红色"></button><button type="button" class="color-swatch" data-color="#EF6C00" style="background:#EF6C00" title="橙色"></button><button type="button" class="color-swatch" data-color="#2E7D32" style="background:#2E7D32" title="绿色"></button><button type="button" class="color-swatch" data-color="#1565C0" style="background:#1565C0" title="蓝色"></button><button type="button" class="color-swatch" data-color="#6A1B9A" style="background:#6A1B9A" title="紫色"></button></div><p class="hint">全页面数据可编辑，点击即可修改。划选任意文字可改字体颜色或加批注，批注可在右侧面板直接编辑修改。点「保存本页」下载到本机（含修改与批注），不写回服务器。</p></div>
<section class="hero" contenteditable="true" spellcheck="false"><div class="hero-inner"><div class="hero-grid"><div><h1>项目管理快报</h1><div class="project">{html.escape(product_line)} · {html.escape(project_name)}<br>（{html.escape(project_code)}）</div></div><div class="hero-meta">▣ 报告期<b>{html.escape(month)}</b></div><div class="hero-meta">♟ 项目经理<b>{html.escape(manager)}</b></div><div class="hero-meta">▦ 数据状态<b>月度快报</b></div></div><div class="hero-actions">{export_buttons}</div></div></section>
<main contenteditable="true" spellcheck="false"><section class="page" data-pmopage="overview"><div class="page-inner"><div class="summary-grid"><article class="status-card green"><b>{counts['normal']}</b><p>正常指标</p></article><article class="status-card yellow"><b>{counts['concern']}</b><p>关注指标</p></article><article class="status-card red"><b>{counts['intervention']}</b><p>干预指标</p></article><article class="status-card gray"><b>{missing_count}</b><p>待补数据指标</p></article></div><div class="overview"><div class="section-head"><div><h2>本期核心指标</h2><p>先看项目状态，再进入各领域的成因与行动。</p></div><div class="decision">决策提示：工时偏差与预算偏差超过阈值，建议专项复核计划基线与成本归集；交付完成率、缺陷逃逸率保持正常。</div></div><div class="metric-grid">{executive_cards}</div><script>window.__staffCount={_proj_staff_count}</script>{_analysis_html}<div class="section-head" style="margin-top:28px"><div><h2>PMO 预警清单</h2><p>当前值、目标、公式和预警状态统一留痕，便于项目例会追踪。</p></div><div class="decision">"待补"表示当前没有足够原始数据，不等同于项目异常；应优先补齐能改变决策的数据。</div></div><div class="table-card"><table class="alert-table">{alerts_html}</table><p class="coverage" style="margin-top:12px;color:#2c5282;background:#edf4ff;padding:12px 16px;border-radius:8px;line-height:1.7">{html.escape(alert_summary_text)}</p><p class="coverage">数据口径说明：本期实际工时、计划工时及成本优先取产品线看板；质量度量用于补充负责人和历史趋势。若来源冲突，快报保留产品线看板作为本期权威源并提示后续核对。</p></div></div></div></section>
<section class="page" data-pmopage="p01_fine_management"><div class="page-inner"><div class="section-head"><div><h2>① 产研精细化管理</h2><p>核心问题：交付节奏是否可控？</p></div><div class="decision">本期完成 {task_done:.0f}/{task_total:.0f} 项任务，但延期任务 {task_delay:.0f} 项需排查计划质量。</div></div><div class="chart-grid">{_chart('taskChart','任务完成情况','bar',[{'label':'任务总数','value':task_total},{'label':'已完成','value':task_done},{'label':'延期任务','value':task_delay},{'label':'高难任务','value':high_difficulty}])}{_chart('verifyChart','需求验证通过率趋势','line',verification,empty='需要每月需求验证通过率，才能展示趋势。' if not verification else '')}</div><div class="two-notes"><article class="note-card"><h3>本期结论</h3><p>Sprint 完成率 <span data-metric="sprint_completion_rate">{_value(items['sprint_completion_rate'])}</span>，交付结果正常；延期任务不等于未完成任务，需在项目计划中明确其延期原因及对后续里程碑的影响。</p></article><article class="note-card"><h3>建议动作</h3><p>下月按任务类型拆分延期原因（需求变更、资源不足、技术阻塞），将“完成率”与“延期率”同时纳入例会复盘。</p></article></div>{_calib_p01}</div></section>
<section class="page" data-pmopage="p02_investment_deviation"><div class="page-inner"><div class="section-head"><div><h2>② 项目投入偏差</h2><p>核心问题：钱和时间花在哪了？偏了多少？</p></div><div class="decision">本期实际投入 {_num(proj.get('actual_total_man_hour')):.0f} 小时，计划 {_num(proj.get('plan_total_man_hour')):.2f} 小时；总成本实际 {total_cost_actual:,.0f} 元，预算 {total_cost_budget:,.0f} 元。</div></div><div class="chart-grid">{_chart('costChart','月度成本：实际与预算对比','grouped',costs,empty='需要至少两个统计月的成本数据，才能展示月度对比。' if len(costs)<2 else '')}{_chart('workhourChart','月度工时：实际与计划对比','grouped',workhour,empty='需要至少两个统计月的工时数据，才能展示月度对比。' if len(workhour)<2 else '')}</div><div class="two-notes"><article class="note-card"><h3>偏差判断</h3><div class="big"><span data-metric="workhour_deviation_rate">{_value(items['workhour_deviation_rate'])}</span> / <span data-metric="budget_deviation_rate">{_value(items['budget_deviation_rate'])}</span></div><p>依次为工时偏差率与预算偏差率；均以产品线看板的本期数据为权威源。</p></article><article class="note-card"><h3>建议动作</h3><p>按“新增需求、返工、资源价格、外包/人力”拆解成本偏差；确认计划工时是否仍反映当前范围，避免把过期基线用于预警。</p></article></div>{_calib_p02}</div></section>
<section class="page" data-pmopage="p03_staff_health"><div class="page-inner"><div class="section-head"><div><h2>③ 人员投入健康度</h2><p>核心问题：团队是否在透支？</p></div><div class="decision">本月总可用工时 <span data-metric="total_available_hours">{total_available_hours:.1f}</span> 小时，人员总投入 <span data-metric="total_investment">{total_investment:.2f}</span> 人月，加班 <span data-metric="overtime_hours">{overtime_hours:.1f}</span> 小时；跨项目投入占比 <span data-metric="cross_project_ratio">{cross_project_ratio:.1f}</span>%。</div></div><div class="chart-grid">{_chart('staffChart','项目成员投入工时（Top 10）','barh',staff_top,empty='当前工时明细中未识别到可用的成员工时字段。' if not staff_top else '')}<div class="chart-card" style="display:flex;align-items:center;gap:18px"><div style="text-align:center;min-width:120px;flex-shrink:0"><div style="font-size:13px;color:#667895">总可用工时</div><div id="p03_total_avail" style="font-size:30px;font-weight:800;color:#2458d7;line-height:1.2">{total_available_hours:.1f}</div><div style="font-size:13px;color:#667895">小时</div></div><div style="flex:1;min-width:0">{_chart('healthChart','团队健康度指标','bar',[{'label':'人员总投入(人月)','value':total_investment},{'label':'平均考勤时长(小时)','value':avg_attendance},{'label':'加班工时(小时)','value':overtime_hours},{'label':'跨项目投入(%)','value':cross_project_ratio}])}</div></div></div><div class="two-notes"><article class="note-card"><h3>已确认口径</h3><p>"实际总工时"是所有成员在该项目的投入总和；9 小时/人天只用于需求成本的人天换算，不能用于反推项目实际总工时。</p></article><article class="note-card"><h3>健康度说明</h3><p>加班工时 = 每人每天投入 > 9h 的累计；跨项目投入 = (参与项目的人员总工时 - 投入到本项目的工时) / 参与项目的人员总工时。</p></article></div>{_calib_p03}</div></section>
<section class="page" data-pmopage="p04_product_quality"><div class="page-inner"><div class="section-head"><div><h2>④ 产品质量</h2><p>核心问题：交付物靠不靠谱？</p></div><div class="decision">缺陷逃逸率 <span data-metric="defect_escape_rate">{_value(items['defect_escape_rate'])}</span>；Bug 关闭率 <span data-metric="bug_close_rate">{_value(items['bug_close_rate'])}</span>。</div></div><div class="chart-grid">{_chart('closeChart','Bug 关闭率趋势','line',quality_cur_close,empty='需要每月 Bug 关闭率，才能展示趋势。' if not quality_cur_close else '')}{_chart('leakChart','缺陷逃逸率趋势','line',quality_cur_leak,empty='需要每月线上逃逸缺陷率，才能展示趋势。' if not quality_cur_leak else '')}</div><div class="two-notes"><article class="note-card"><h3>当前质量状态</h3><div class="big">线上 {bug_detail.get('field_bug_count', 0)} / 总计 {total_bug_count}</div><p>缺陷逃逸率 = 线上 Bug 数({bug_detail.get('field_bug_count', 0)}) ÷ 总 Bug 数({total_bug_count}) = <span data-metric="defect_escape_rate">{_value(items['defect_escape_rate'])}</span>。</p></article><article class="note-card"><h3>建议动作</h3><p>持续跟踪 Bug 关闭率与缺陷逃逸率趋势，确保质量判断链路完整。</p></article></div>{_calib_p04}</div></section>
<section class="page" data-pmopage="p05_bug_efficiency"><div class="page-inner"><div class="section-head"><div><h2>⑤ Bug 修复效率</h2><p>核心问题：问题解决得快不快？</p></div><div class="decision">本期关闭 {closed_bug_count}/{total_bug_count} 个 Bug，平均修复时长 <span data-metric="mttr">{_value(items['mttr'])}</span>；缺少严重级别目标与历史关闭数，暂不输出误导性的趋势判断。</div></div><div class="chart-grid">{_chart('bugCloseChart','本期 Bug 关闭情况','donut',[{'label':'已关闭','value':closed_bug_count},{'label':'未关闭','value':max(total_bug_count-closed_bug_count,0)}])}{_chart('mttrChart','MTTR 数据状态','bar',[{'label':'本期 MTTR（小时）','value':items['mttr'].value or 0}],empty='')}</div><div class="two-notes"><article class="note-card"><h3>计算公式</h3><div class="big" data-metric="mttr">{_value(items['mttr'])}</div><p>排除拒绝/第三方后 Bug 修复时长({(bug_detail.get('bug_resolve_duration_exclude_reject_third') or 0):,.0f}秒) ÷ Bug 总数({total_bug_count}) ÷ 3600。</p></article><article class="note-card"><h3>后续接入</h3><p>接入每个 Bug 的严重级别和状态，以计算分级 MTTR 与月度趋势。</p></article></div>{_calib_p05}</div></section>
<section class="page" data-pmopage="p06_value_delivery"><div class="page-inner"><div class="section-head"><div><h2>⑥ 需求价值交付</h2><p>核心问题：做的东西有没有用？</p></div><div class="decision">当前可衡量交付成本与研发投入结构；需求价值需补充验收、使用、收益或业务目标达成数据。</div></div><div class="chart-grid">{_chart('valueChart','研发与测试工时投入结构','donut',[{'label':'研发工时','value':_num(proj.get('actual_rd_man_hour'))},{'label':'测试工时','value':_num(proj.get('actual_test_man_hour'))}])}<article class="note-card"><h3>需求成本</h3><div class="big" data-metric="requirement_cost">{html.escape(_value(items['requirement_cost']))}</div><p>公式：研发工时(actual_rd_man_hour) ÷ 总需求数；同时按 9 小时/人天换算。总需求数优先取 DB(requirements) 当月记录数，降级取 API totalReqCount，再降级取完成任务数。</p></article></div><div class="two-notes"><article class="note-card"><h3>研发资源投入占比</h3><div class="big" data-metric="rd_resource_input_ratio">{_value(items['rd_resource_input_ratio'])}</div><p>申报本项目工时的人员数 ÷ 配置的组织总人数。研发人力 = 申报了本项目工时的人员总数。</p></article><article class="note-card"><h3>待建立价值闭环</h3><p>建议接入需求验收、活跃使用、客户/业务收益、目标达成字段，届时按需求建立“投入—交付—验证—收益”链路。</p></article></div>{_calib_p06}</div></section>
</main><footer class="footer">项目管理快报 · 由 PMO 项目月度快报工作流生成 · {html.escape(month)}</footer>
<script>
function drawCharts(){{const palette=['#2864f3','#15a34a','#f2ae19','#eb3b3b','#8b5cf6','#10b9a7'];document.querySelectorAll('canvas[data-chart]').forEach(c=>{{const data=JSON.parse(c.dataset.chart),ctx=c.getContext('2d'),ratio=devicePixelRatio||1,w=c.clientWidth*ratio,h=c.clientHeight*ratio;c.width=w;c.height=h;ctx.scale(ratio,ratio);w=c.clientWidth;h=c.clientHeight;ctx.font='13px PingFang SC';ctx.textBaseline='middle';const pad={{l:48,r:18,t:25,b:38}};const plotW=w-pad.l-pad.r,plotH=h-pad.t-pad.b;ctx.clearRect(0,0,w,h);if(c.dataset.kind==='donut'){{const total=data.reduce((s,d)=>s+d.value,0)||1;let a=-Math.PI/2;data.forEach((d,i)=>{{const next=a+d.value/total*Math.PI*2;ctx.beginPath();ctx.strokeStyle=palette[i];ctx.lineWidth=Math.min(w,h)*.18;ctx.arc(w*.34,h*.47,Math.min(w,h)*.23,a,next);ctx.stroke();a=next;}});ctx.fillStyle='#1c2941';ctx.font='bold 25px PingFang SC';ctx.textAlign='center';ctx.fillText(Math.round(total),w*.34,h*.47);ctx.font='12px PingFang SC';ctx.fillStyle='#667895';ctx.fillText('总数',w*.34,h*.47+23);data.forEach((d,i)=>{{ctx.fillStyle=palette[i];ctx.fillRect(w*.65,h*.28+i*30,12,12);ctx.fillStyle='#42536d';ctx.textAlign='left';ctx.fillText(`${{d.label}} ${{d.value.toFixed(d.value%1?1:0)}}`,w*.65+19,h*.28+6+i*30)}});return;}}const series=c.dataset.kind==='grouped'?Object.keys(data[0]||{{}}).filter(k=>k!=='label'):['value'];const max=Math.max(1,...data.flatMap(d=>series.map(k=>d[k]||0)))*1.14;ctx.strokeStyle='#e6edf6';ctx.fillStyle='#70819a';ctx.lineWidth=1;ctx.textAlign='right';for(let i=0;i<5;i++){{const y=pad.t+plotH*i/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();ctx.fillText((max*(1-i/4)).toFixed(max>=100?0:1),pad.l-7,y);}}if(c.dataset.kind==='line'){{const pts=data.map((d,i)=>[pad.l+(data.length===1?plotW/2:plotW*i/(data.length-1)),pad.t+plotH-(d.value||0)/max*plotH]);ctx.strokeStyle=palette[0];ctx.lineWidth=3;ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.stroke();pts.forEach((p,i)=>{{ctx.fillStyle=palette[0];ctx.beginPath();ctx.arc(...p,4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#42536d';ctx.textAlign='center';ctx.fillText(data[i].label,p[0],h-16);}});return;}}if(c.dataset.kind==='barh'){{const row=plotH/data.length;data.forEach((d,i)=>{{const y=pad.t+i*row+row*.22;ctx.fillStyle='#62748f';ctx.textAlign='right';ctx.fillText(d.label,pad.l-8,y+row*.25);ctx.fillStyle=palette[0];ctx.fillRect(pad.l,y,(d.value||0)/max*plotW,row*.48);ctx.fillStyle='#42536d';ctx.textAlign='left';ctx.fillText(d.value.toFixed(1),pad.l+(d.value||0)/max*plotW+6,y+row*.25);}});return;}}const groupW=plotW/data.length;data.forEach((d,i)=>{{series.forEach((key,j)=>{{const bw=groupW/(series.length+1);const x=pad.l+i*groupW+bw*(j+.5);const bh=(d[key]||0)/max*plotH;ctx.fillStyle=palette[j];ctx.fillRect(x,pad.t+plotH-bh,bw*.75,bh);}});ctx.fillStyle='#42536d';ctx.textAlign='center';ctx.fillText(d.label,pad.l+i*groupW+groupW/2,h-16);}});series.forEach((key,i)=>{{ctx.fillStyle=palette[i];ctx.fillRect(pad.l+i*110,pad.t-18,10,10);ctx.fillStyle='#42536d';ctx.textAlign='left';ctx.fillText(key,pad.l+14+i*110,pad.t-13)}})}});}});window.addEventListener('resize',drawCharts);drawCharts();
</script></body></html>'''
    interactive_script = r'''<script>
    (function(){
      /* ── 导航栏 ── */
      var s=document.createElement('style');s.textContent='.quick-nav{position:sticky;top:0;z-index:4;background:#fff;border-bottom:1px solid #dbe5f2;padding:10px max(5vw,32px);display:flex;gap:8px;overflow:auto}.quick-nav button{border:1px solid #d3deee;background:#fff;color:#52647d;border-radius:999px;padding:7px 13px;white-space:nowrap;font-size:13px;cursor:pointer;transition:all .2s}.quick-nav button:hover,.quick-nav button.active{background:#2864f3;border-color:#2864f3;color:#fff}.page{scroll-margin-top:55px}@media print{.quick-nav{display:none}}';document.head.appendChild(s);
      var pages=[].slice.call(document.querySelectorAll('main > .page'));
      var names=['总览','产研管理','投入偏差','人员健康','产品质量','Bug效率','价值交付'];
      pages.forEach(function(p,i){p.id='pmo-section-'+i});
      var vPages=[],vNames=[];
      pages.forEach(function(p,i){if(window.getComputedStyle(p).display!=='none'){vPages.push(p);vNames.push(names[i])}});
      var nav=document.createElement('nav');nav.className='quick-nav';
      nav.innerHTML=vNames.map(function(n,i){return '<button data-target="'+vPages[i].id+'">'+n+'</button>'}).join('');
      document.querySelector('.hero').insertAdjacentElement('afterend',nav);
      nav.addEventListener('click',function(e){var t=e.target.dataset.target;if(t)document.getElementById(t).scrollIntoView({behavior:'smooth',block:'start'})});

      /* ── ECharts 初始化 ── */
      var charts=[];
      window.__charts=charts;
      document.querySelectorAll('div[data-option]').forEach(function(el){
        try{
          var opt=JSON.parse(el.getAttribute('data-option'));
          var c=echarts.init(el,null,{renderer:'canvas'});
          c.setOption(opt);charts.push(c);
        }catch(e){console.warn('Chart init error:',el.id,e)}
      });
      window.addEventListener('resize',function(){charts.forEach(function(c){c.resize()})});

      /* ── 数据校准：字段链接与同步 ── */
      window.__calibSync=function(el){
        var id=el.id.replace('calib-','');
        var m=id.match(/^(p\d+)_(.+)$/);if(!m)return;
        var field=m[2],val=el.value;
        document.querySelectorAll('input[id^="calib-"]').forEach(function(inp){
          if(inp!==el&&inp.id.indexOf('_'+field)>=0)inp.value=val;
        });
      };

      /* ── 数据校准重算 ── */
      window.__recalc=function(){
        try{
        var v=function(id){var el=document.getElementById('calib-'+id);return el?(parseFloat(el.value)||0):0};
        var d={company_headcount:v('p06_company_headcount'),total_req_count:v('p06_total_req_count'),actual_rd_man_hour:v('p06_actual_rd_man_hour'),task_count:v('p01_task_count'),task_finish_count:v('p01_task_finish_count'),bug_total_count:v('p04_bug_total_count'),field_bug_count:v('p04_field_bug_count'),bug_closed_count:v('p04_bug_closed_count'),bug_resolve_duration_sec:v('p05_bug_resolve_duration_sec'),actual_total_man_hour:v('p02_actual_total_man_hour'),plan_total_man_hour:v('p02_plan_total_man_hour'),rd_actual_cost:v('p02_rd_actual_cost'),rd_budget_cost:v('p02_rd_budget_cost'),project_staff_count:v('p03_project_staff_count'),monthly_work_days:v('p03_monthly_work_days')};
        var staffCount=v('p06_project_staff_count')||v('p03_project_staff_count')||window.__staffCount||0;
        var m={};
        m.rd_resource_input_ratio=d.company_headcount>0?(staffCount/d.company_headcount):null;
        m.requirement_cost=d.total_req_count>0?(d.actual_rd_man_hour/d.total_req_count):null;
        m.sprint_completion_rate=d.task_count>0?(d.task_finish_count/d.task_count):null;
        m.defect_escape_rate=d.bug_total_count>0?(d.field_bug_count/d.bug_total_count):null;
        m.bug_close_rate=d.bug_total_count>0?(d.bug_closed_count/d.bug_total_count):null;
        m.mttr=(d.bug_total_count>0&&d.bug_resolve_duration_sec>0)?(d.bug_resolve_duration_sec/d.bug_total_count/3600):null;
        m.workhour_deviation_rate=d.plan_total_man_hour>0?((d.actual_total_man_hour-d.plan_total_man_hour)/d.plan_total_man_hour):null;
        m.budget_deviation_rate=d.rd_budget_cost>0?(d.rd_actual_cost/d.rd_budget_cost-1):null;
        var newTotalAvail=d.project_staff_count>0&&d.monthly_work_days>0?(d.project_staff_count*d.monthly_work_days*9):0;
        var totalInvestmentH=v('p03_total_investment_hours');
        var overtimeH=v('p03_overtime_hours');
        var totalStaffAllH=v('p03_total_staff_all_hours');
        var newTotalInvestment=totalInvestmentH>0?(totalInvestmentH/174):0;
        var newCrossRatio=totalStaffAllH>0?((totalStaffAllH-d.actual_total_man_hour)/totalStaffAllH*100):0;
        m.total_available_hours=newTotalAvail||null;
        m.total_investment=newTotalInvestment>0?newTotalInvestment:null;
        m.overtime_hours=overtimeH>0?overtimeH:null;
        m.cross_project_ratio=newCrossRatio>0?newCrossRatio:null;
        if(newTotalAvail>0){var taEl=document.getElementById('p03_total_avail');if(taEl)taEl.textContent=newTotalAvail.toFixed(1);var taSpan=document.querySelector('[data-metric="total_available_hours"]');if(taSpan)taSpan.textContent=newTotalAvail.toFixed(1)+' 小时';}
        var fmt=function(code,val){
          if(val===null||isNaN(val))return'\u5f85\u63a5\u5165';
          if(code==='requirement_cost')return val.toFixed(2)+' \u4eba\u5929/\u9879';
          if(code==='mttr')return val.toFixed(1)+' \u5c0f\u65f6';
          if(code==='rd_resource_input_ratio')return (val*100).toFixed(1)+'%';
          if(code==='total_investment')return val.toFixed(2)+' 人月';
          if(code==='overtime_hours')return val.toFixed(1)+' 小时';
          if(code==='cross_project_ratio')return val.toFixed(1)+'%';
          if(code==='total_available_hours')return val.toFixed(1)+' 小时';
          if(['req_verify_rate','defect_escape_rate','bug_close_rate','sprint_completion_rate','workhour_deviation_rate','budget_deviation_rate'].indexOf(code)>=0)return (val*100).toFixed(1)+'%';
          return val.toFixed(2);
        };
        var warn=function(code,val){
          if(val===null||isNaN(val))return'gray';var a=Math.abs(val);
          if(code==='req_verify_rate')return val>=0.85?'green':val>=0.70?'yellow':'red';
          if(code==='sprint_completion_rate')return val>=0.85?'green':'yellow';
          if(code==='workhour_deviation_rate')return a<=0.15?'green':a<=0.25?'yellow':'red';
          if(code==='budget_deviation_rate')return a<=0.10?'green':a<=0.25?'yellow':'red';
          if(code==='defect_escape_rate')return val<=0.03?'green':val<=0.10?'yellow':'red';
          if(code==='bug_close_rate')return val>=0.95?'green':val>=0.80?'yellow':'red';
          if(code==='requirement_cost')return val<=3?'green':val<=5?'yellow':'red';
          if(code==='mttr')return val<=336?'green':val<=504?'yellow':'red';
          return'normal';
        };
        var stateLabel={green:'\u6b63\u5e38',yellow:'\u5173\u6ce8',red:'\u5e72\u9884',gray:'\u5f85\u8865'};
        Object.keys(m).forEach(function(code){
          var all=document.querySelectorAll('[data-metric="'+code+'"]');
          var lv=warn(code,m[code]);
          all.forEach(function(card){
            var el=card.querySelector('.metric-value');
            if(el)el.textContent=fmt(code,m[code]);
            else card.textContent=fmt(code,m[code]);
            ['green','yellow','red','gray'].forEach(function(c){card.classList.remove(c);});
            card.classList.add(lv);
            var dot=card.querySelector('.dot');if(dot){['green','yellow','red','gray'].forEach(function(c){dot.classList.remove(c);});dot.classList.add(lv);}
            var strong=card.querySelector('strong');if(strong){var cs={green:'var(--green)',yellow:'var(--yellow)',red:'var(--red)',gray:'var(--gray)'};strong.style.color=cs[lv]||'';}
          });
        });
        var ch=window.__charts||[];
        ch.forEach(function(c){
          var id=c.getDom().id;
          if(id==='taskChart')c.setOption({series:[{data:[d.task_count,d.task_finish_count]}]});
          if(id==='valueChart')c.setOption({series:[{data:[{name:'\u7814\u53d1\u5de5\u65f6',value:d.actual_rd_man_hour}]}]});
          if(id==='bugCloseChart')c.setOption({series:[{data:[{name:'\u5df2\u5173\u95ed',value:d.bug_closed_count},{name:'\u672a\u5173\u95ed',value:Math.max(d.bug_total_count-d.bug_closed_count,0)}]}]});
          if(id==='mttrChart')c.setOption({series:[{data:[m.mttr||0]}]});
          if(id==='healthChart'){var opt=c.getOption();var avgAtt=(opt.series&&opt.series[0]&&opt.series[0].data&&opt.series[0].data[1])?opt.series[0].data[1].value:0;c.setOption({series:[{data:[{name:'人员总投入(人月)',value:newTotalInvestment||0},{name:'平均考勤时长(小时)',value:avgAtt},{name:'加班工时(小时)',value:overtimeH||0},{name:'跨项目投入(%)',value:newCrossRatio||0}]}]});}
        });
        var alertRows=document.querySelectorAll('.alert-table tbody tr');
        var nameMap={'\u7f3a\u9677\u9003\u9038\u7387':'defect_escape_rate','Bug\u5173\u95ed\u7387':'bug_close_rate','\u5e73\u5747\u4fee\u590d\u65f6\u957f\uff08MTTR\uff09':'mttr','\u9700\u6c42\u6210\u672c':'requirement_cost','Sprint\u5b8c\u6210\u7387':'sprint_completion_rate','\u7814\u53d1\u8d44\u6e90\u6295\u5165\u5360\u6bd4':'rd_resource_input_ratio','\u5de5\u65f6\u504f\u5dee\u7387':'workhour_deviation_rate','\u9884\u7b97\u504f\u5dee\u7387':'budget_deviation_rate'};
        alertRows.forEach(function(row){
          var name=row.cells[0]?.textContent.trim()||'';
          var code=nameMap[name];
          if(code&&m[code]!==undefined){
            var valEl=row.cells[1];if(valEl)valEl.textContent=fmt(code,m[code]);
            var tagEl=row.cells[4];if(tagEl){var lv=warn(code,m[code]);var sp=tagEl.querySelector('span');if(sp){sp.className='tag '+lv;sp.textContent=stateLabel[lv]||lv;}}
          }
        });
        }catch(e){console.error('__recalc error:',e)}
      };

      /* ── 滚动高亮导航 ── */
      var btns=nav.querySelectorAll('button');
      window.addEventListener('scroll',function(){
        var st=window.scrollY+80,cur=0;
        vPages.forEach(function(p,i){if(p.offsetTop<=st)cur=i});
        btns.forEach(function(b,i){b.classList.toggle('active',i===cur)});
      });
    })();
    ''' + _PAGE_SAVE_JS + '''
    </script>'''
    # 只替换最后一个 </body></html>（前面的出现在 exportWord/exportPPT 的 JS 字符串中，不能替换）
    # ── 全局批注栏（固定右侧）──
    _GLOBAL_RAIL = '<div class="pmo-anno-rail" id="pmo-anno-rail" aria-label="批注栏"></div>'
    _insert_pos = document.rfind("</body></html>")
    if _insert_pos >= 0:
        document = document[:_insert_pos] + _GLOBAL_RAIL + interactive_script + document[_insert_pos:]
    # ── 页面选择（--pages 控制显示/隐藏页面 section） ──
    resolved = resolve_page_ids(page_ids)
    if resolved is not None:
        always = {"overview"}
        for m in re.finditer(r'<section class="page" data-pmopage="([^"]+)">', document):
            if m.group(1) not in always and m.group(1) not in resolved:
                document = document.replace(m.group(0), m.group(0).replace('<section', '<section style="display:none"'))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
