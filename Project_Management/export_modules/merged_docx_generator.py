#!/usr/bin/env python3
"""跨月合并 Word 快报生成器（python-docx）。

结构：
- 封面：项目信息 + 月份范围
- 多月指标对比表
- 每月一节：指标概览 + 关键数据摘要
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from data_modules.metrics_engine import create_metrics, number


# ── 页面选择工具 ──
_PAGE_ID_ALIASES = {
    "p01": "p01_fine_management", "p02": "p02_investment_deviation",
    "p03": "p03_staff_health", "p04": "p04_product_quality",
    "p05": "p05_bug_efficiency", "p06": "p06_value_delivery",
}

_METRIC_PAGE = {
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

_COMP_METRIC_PAGE = {
    "工时偏差率": "p02_investment_deviation", "预算偏差率": "p02_investment_deviation",
    "Sprint完成率": "p01_fine_management", "任务完成数": "p01_fine_management",
    "实际总工时": "p02_investment_deviation", "平均考勤时长": "p03_staff_health",
    "Bug关闭率": "p04_product_quality", "缺陷逃逸率": "p04_product_quality",
    "Bug总数": "p04_product_quality", "需求验证通过率": "p01_fine_management",
}


def _resolve_page_ids(page_ids):
    if page_ids is None:
        return None
    if isinstance(page_ids, str):
        page_ids = [page_ids]
    result = []
    for pid in page_ids:
        pid_lower = pid.lower().strip()
        if pid_lower == "all":
            return None
        result.append(_PAGE_ID_ALIASES.get(pid_lower, pid_lower))
    return result

try:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    _DOCX_OK = True
except ImportError:
    Document = None
    RGBColor = None
    _DOCX_OK = False

# ── 颜色（延迟初始化，避免 docx 不可用时 NameError）──
BLUE = NAVY = INK = MUTED = GREEN = YELLOW = RED = GRAY = WHITE = None

def _init_colors():
    global BLUE, NAVY, INK, MUTED, GREEN, YELLOW, RED, GRAY, WHITE
    if BLUE is not None:
        return
    BLUE = RGBColor(0x28, 0x64, 0xF3)
    NAVY = RGBColor(0x17, 0x3E, 0x79)
    INK = RGBColor(0x1C, 0x29, 0x41)
    MUTED = RGBColor(0x65, 0x77, 0x93)
    GREEN = RGBColor(0x16, 0xA3, 0x4A)
    YELLOW = RGBColor(0xD6, 0x8C, 0x00)
    RED = RGBColor(0xE3, 0x28, 0x28)
    GRAY = RGBColor(0x8B, 0x98, 0xAA)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)

LABELS = {"normal": "正常", "concern": "关注", "intervention": "干预",
          "unconfigured": "参考", "unavailable": "待补"}
TARGETS = {
    "req_verify_rate": "≥85%", "sprint_completion_rate": "≥85%",
    "workhour_deviation_rate": "±15%", "budget_deviation_rate": "±10%",
    "requirement_cost": "≤3人天", "defect_escape_rate": "≤3%",
    "bug_close_rate": "≥95%", "mttr": "P0≤4h P1≤24h",
    "rd_resource_input_ratio": "研发人力/组织总人数",
}


def n(value, default=0.0):
    parsed = number(value)
    return default if parsed is None else parsed


def _metric_value(metric):
    if metric.display_value:
        return metric.display_value
    if metric.value is None:
        return "暂无数据"
    if metric.unit == "%":
        return f"{metric.value:.1%}"
    if metric.unit == "小时":
        return f"{metric.value:.2f} 小时"
    if metric.unit == "个":
        return f"{metric.value:.0f}"
    return f"{metric.value:.2f}"


def _set_cell_shading(cell, color_hex: str):
    shading = cell._element.get_or_add_tcPr()
    shading_elem = shading.makeelement(qn("w:shd"), {
        qn("w:fill"): color_hex, qn("w:val"): "clear",
    })
    shading.append(shading_elem)


def _add_para(doc, text_str, *, size=11, bold=False, color=INK,
              alignment=None, space_after=6):
    p = doc.add_paragraph()
    run = p.add_run(text_str)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    if alignment:
        p.alignment = alignment
    p.paragraph_format.space_after = Pt(space_after)
    return p


def _build_comparison_rows(snapshots, allowed_pages=None):
    """构建对比表格数据。返回 [(name, [values], unit), ...]。"""
    metric_defs = [
        ("工时偏差率", lambda m: (n(m.get("actual_total_man_hour", 0)) - n(m.get("plan_total_man_hour", 0))) / n(m.get("plan_total_man_hour", 1)) if n(m.get("plan_total_man_hour")) else None, "%", ["p02_investment_deviation"]),
        ("预算偏差率", lambda m: n(m.get("rd_cost_exec_rate_month")) - 1 if m.get("rd_cost_exec_rate_month") is not None else None, "%", ["p02_investment_deviation"]),
        ("Sprint完成率", lambda m: n(m.get("task_finish_count", 0)) / n(m.get("task_count", 1)) if n(m.get("task_count")) else None, "%", ["p01_fine_management"]),
        ("Bug关闭率", lambda m: n(m.get("bug_close_rate")), "%", ["p04_product_quality", "p05_bug_efficiency"]),
        ("缺陷逃逸率", lambda m: n(m.get("bug_leak_rate")), "%", ["p04_product_quality"]),
        ("MTTR", lambda m: (lambda d, t: d / t / 3600.0 if d is not None and t else None)(
            n(m.get("bug_resolve_duration_exclude_reject_third")) or n(m.get("bug_resolve_duration_seconds")),
            n(m.get("bug_total_count"))), "小时", ["p05_bug_efficiency"]),
        ("需求验证通过率", lambda m: n(m.get("req_verify_rate")), "%", ["p01_fine_management"]),
        ("任务完成数", lambda m: n(m.get("task_finish_count")), "个", ["p01_fine_management"]),
        ("Bug总数", lambda m: n(m.get("bug_total_count")), "个", ["p04_product_quality", "p05_bug_efficiency"]),
        ("实际总工时", lambda m: n(m.get("actual_total_man_hour")), "小时", ["p02_investment_deviation"]),
        ("平均考勤时长", lambda m: n(m.get("attendance_hour")), "小时", ["p03_staff_health"]),
    ]
    if allowed_pages is not None:
        metric_defs = [d for d in metric_defs if any(p in allowed_pages for p in d[3])]

    months_data = []
    for snap in snapshots:
        proj = snap.get("project", {})
        bug = snap.get("bug_detail", {})
        req = snap.get("requirement_detail", {})
        merged = {}
        merged.update(proj)
        merged["bug_total_count"] = bug.get("bug_total_count")
        merged["bug_close_rate"] = bug.get("bug_close_rate")
        merged["bug_leak_rate"] = bug.get("bug_leak_rate")
        merged["req_verify_rate"] = req.get("req_verify_rate")
        merged["bug_resolve_duration_exclude_reject_third"] = bug.get("bug_resolve_duration_exclude_reject_third")
        merged["bug_resolve_duration_seconds"] = bug.get("bug_resolve_duration_seconds")
        quality_cur = snap.get("quality_current", {})
        merged["rd_cost_exec_rate_month"] = quality_cur.get("rd_cost_exec_rate_month")
        months_data.append(merged)

    rows = []
    for name, extractor, unit, _pages in metric_defs:
        values = [extractor(m) for m in months_data]
        rows.append((name, values, unit))
    return rows


def _fmt(value, unit=""):
    if value is None:
        return "—"
    if unit == "%":
        return f"{value * 100:.1f}%"
    if unit == "小时":
        return f"{value:.1f}h"
    if unit == "个":
        return f"{value:.0f}"
    return f"{value:.2f}"


def _add_comparison_table(doc, snapshots, allowed_pages=None):
    """添加多月对比表。"""
    months = [s.get("project", {}).get("stat_month", "") for s in snapshots]
    comp_rows = _build_comparison_rows(snapshots, allowed_pages=allowed_pages)
    n_months = len(months)

    # 列：指标名 + 每月一列 + 趋势
    ncols = 1 + n_months + 1
    table = doc.add_table(rows=len(comp_rows) + 1, cols=ncols)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 表头
    headers = ["指标"] + months + ["趋势"]
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h
        for p in cell.paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
                r.font.bold = True
                r.font.color.rgb = WHITE
        _set_cell_shading(cell, "2864F3")

    # 数据行
    for row_idx, (name, values, unit) in enumerate(comp_rows):
        row = table.rows[row_idx + 1]
        row.cells[0].text = name
        for p in row.cells[0].paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
                r.font.bold = True
                r.font.color.rgb = NAVY

        for j, val in enumerate(values):
            cell = row.cells[j + 1]
            cell.text = _fmt(val, unit) if val is not None else "—"
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
                    r.font.color.rgb = INK

        # 趋势
        valid = [v for v in values if v is not None]
        trend_cell = row.cells[n_months + 1]
        if len(valid) >= 2:
            diff = valid[-1] - valid[-2]
            if abs(diff) < 0.001:
                trend_text = "→ 持平"
                trend_color = MUTED
            elif diff > 0:
                trend_text = f"↑ +{_fmt(diff, unit)}"
                trend_color = GREEN
            else:
                trend_text = f"↓ {_fmt(diff, unit)}"
                trend_color = RED
        else:
            trend_text = "—"
            trend_color = GRAY
        trend_cell.text = trend_text
        for p in trend_cell.paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
                r.font.color.rgb = trend_color

        # 交替行背景
        if row_idx % 2 == 1:
            for col_idx in range(ncols):
                _set_cell_shading(row.cells[col_idx], "F8FAFD")


def _add_month_section(doc, snap, month, idx, allowed_pages=None):
    """添加单月详情节。"""
    proj = snap.get("project", {})
    bug_detail = snap.get("bug_detail", {})

    metrics, _ = create_metrics(snap)
    # 按页面过滤指标
    if allowed_pages is not None:
        metrics = [m for m in metrics
                   if any(p in allowed_pages for p in _METRIC_PAGE.get(m.code, []))]
    items = {m.code: m for m in metrics}

    # 节标题
    _add_para(doc, f"━━ {month} ━━", size=14, bold=True, color=NAVY,
              alignment=WD_ALIGN_PARAGRAPH.CENTER, space_after=8)

    # 状态统计
    counts = {level: sum(1 for m in metrics if m.warning_level == level)
              for level in ("normal", "concern", "intervention")}
    missing = sum(1 for m in metrics if m.warning_level == "unavailable")
    _add_para(doc,
              f"正常 {counts['normal']}  "
              f"关注 {counts['concern']}  "
              f"干预 {counts['intervention']}  "
              f"待补 {missing}",
              size=10, color=MUTED, space_after=8)

    # 指标表
    codes = (
        "workhour_deviation_rate", "budget_deviation_rate",
        "sprint_completion_rate", "defect_escape_rate",
        "bug_close_rate", "req_verify_rate",
        "requirement_cost", "mttr", "rd_resource_input_ratio",
    )
    active_metrics = [items[c] for c in codes if c in items]

    table = doc.add_table(rows=len(active_metrics) + 1, cols=5)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["指标名称", "当前值", "目标值", "计算口径", "预警等级"]
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h
        for p in cell.paragraphs:
            for r in p.runs:
                r.font.size = Pt(8)
                r.font.bold = True
                r.font.color.rgb = WHITE
        _set_cell_shading(cell, "2864F3")

    for row_idx, m in enumerate(active_metrics):
        row = table.rows[row_idx + 1]
        level_label = LABELS.get(m.warning_level, "待补")
        values = [
            m.name,
            _metric_value(m),
            TARGETS.get(m.code, ""),
            m.formula[:60] + ("..." if len(m.formula) > 60 else ""),
            level_label,
        ]
        for col_idx, val in enumerate(values):
            cell = row.cells[col_idx]
            cell.text = str(val)
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(8)
                    r.font.color.rgb = INK
            if col_idx == 4:
                bg = {"正常": "E9F9EE", "关注": "FFF8DC", "干预": "FFEBEB"}.get(val, "EDF1F6")
                _set_cell_shading(cell, bg)
        if row_idx % 2 == 1:
            for col_idx in range(4):
                _set_cell_shading(row.cells[col_idx], "F8FAFD")

    # 关键数据摘要
    actual_hour = n(proj.get("actual_total_man_hour"))
    plan_hour = n(proj.get("plan_total_man_hour"))
    task_done = n(proj.get("task_finish_count"))
    task_total = n(proj.get("task_count"))
    total_bug = n(bug_detail.get("bug_total_count"))
    closed_bug = n(bug_detail.get("bug_closed_count"))
    avg_att = n(proj.get("attendance_hour"))

    _add_para(doc, "", space_after=4)  # spacer
    _add_para(doc, "关键数据摘要", size=10, bold=True, color=NAVY, space_after=4)

    summary_table = doc.add_table(rows=4, cols=2)
    summary_data = [
        ("任务完成/总数", f"{task_done:.0f} / {task_total:.0f}"),
        ("实际工时/计划工时", f"{actual_hour:.0f}h / {plan_hour:.0f}h"),
        ("Bug 总数/已关闭", f"{total_bug:.0f} / {closed_bug:.0f}"),
        ("平均考勤时长", f"{avg_att:.1f}h"),
    ]
    for i, (label, value) in enumerate(summary_data):
        summary_table.rows[i].cells[0].text = label
        summary_table.rows[i].cells[1].text = value
        for p in summary_table.rows[i].cells[0].paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
                r.font.bold = True
                r.font.color.rgb = NAVY
        for p in summary_table.rows[i].cells[1].paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
                r.font.color.rgb = INK


def _add_quarterly_section(doc, quarterly_data, months, quarter_label=""):
    """添加季度汇总节。"""
    q = quarterly_data
    display_label = quarter_label or f"{months[0]} ~ {months[-1]}"
    range_detail = f"{months[0]} ~ {months[-1]}" if quarter_label else ""

    _add_para(doc, f"{display_label} 季度汇总", size=14, bold=True, color=NAVY, space_after=4)
    _add_para(doc, f"数据来源：产品线看板 API 季度聚合口径 ({range_detail})，可加指标为各月之和，比率指标为重新计算。",
              size=9, color=MUTED, space_after=8)

    def _fp(v, unit="%"):
        if v is None:
            return "—"
        if unit == "%":
            return f"{v * 100:.1f}%"
        if unit == "h":
            return f"{v:,.0f}h"
        return f"{v:,.0f}"

    ah = n(q.get("actual_total_man_hour"))
    ph = n(q.get("plan_total_man_hour")) or 1
    wdr = (ah - ph) / ph if ph else None
    adc = n(q.get("actual_dev_cost")) + n(q.get("actual_staff_cost"))
    pdc = n(q.get("plan_dev_cost")) + n(q.get("plan_staff_cost"))
    bdr = (adc - pdc) / pdc if pdc else None
    sprint_rate = n(q.get("task_finish_count")) / n(q.get("task_count")) if n(q.get("task_count")) else None

    rows_data = [
        ("实际总工时", _fp(ah, "h"), f"计划 {_fp(ph, 'h')}"),
        ("工时偏差率", _fp(wdr), "±15% 以内为正常"),
        ("预算偏差率", _fp(bdr), "±10% 以内为正常"),
        ("Sprint完成率", _fp(sprint_rate), f"完成 {int(n(q.get('task_finish_count')))}/{int(n(q.get('task_count')))} 项"),
        ("Bug关闭率", _fp(n(q.get("bug_close_rate"))), "目标 ≥95%"),
        ("缺陷逃逸率", _fp(n(q.get("bug_leak_rate"))), "目标 ≤10%"),
        ("任务完成数", _fp(n(q.get("task_finish_count")), "个"), f"延期 {int(n(q.get('task_delay_count')))} 项"),
        ("Bug总数", _fp(n(q.get("bug_total_count")), "个"), ""),
    ]

    table = doc.add_table(rows=len(rows_data) + 1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["指标", "值", "备注"]
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h
        for p in cell.paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
                r.font.bold = True
                r.font.color.rgb = WHITE
        _set_cell_shading(cell, "2864F3")

    for idx, (name, val, sub) in enumerate(rows_data):
        row = table.rows[idx + 1]
        vals = [name, val, sub]
        for ci, v in enumerate(vals):
            cell = row.cells[ci]
            cell.text = str(v)
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
                    r.font.color.rgb = INK
                    if ci == 0:
                        r.font.bold = True
                        r.font.color.rgb = NAVY
        if idx % 2 == 1:
            for ci in range(3):
                _set_cell_shading(row.cells[ci], "F8FAFD")


def export_merged_docx(snapshots: list[dict], output_path: Path, quarterly_data: dict = None,
                       quarter_label: str = "", aggregated_data: dict = None,
                       page_ids: list[str] = None) -> Path:
    """生成跨月合并 Word 报告。"""
    if not _DOCX_OK:
        raise ImportError("python-docx 未安装或 lxml 异常，无法生成 Word 文档")
    _init_colors()
    if Document is None:
        raise ImportError("python-docx 未安装: pip install python-docx")
    if not snapshots:
        raise ValueError("snapshots 列表不能为空")

    first_proj = snapshots[0].get("project", {})
    project_name = first_proj.get("project_name", "未知项目")
    project_code = first_proj.get("project_code", first_proj.get("project_id", ""))
    manager = first_proj.get("manager_name", "待补")
    product_line = first_proj.get("product_line") or first_proj.get("department_name") or "产品线"
    months = [s.get("project", {}).get("stat_month", "") for s in snapshots]
    range_label = f"{months[0]} ~ {months[-1]}"

    # 解析页面选择
    allowed_pages = _resolve_page_ids(page_ids)

    # ── 创建文档 ──
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    # ── 封面 ──
    _add_para(doc, "跨月合并快报", size=24, bold=True, color=NAVY,
              alignment=WD_ALIGN_PARAGRAPH.CENTER, space_after=8)
    _add_para(doc, f"{product_line} · {project_name}", size=14, color=INK,
              alignment=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    _add_para(doc, f"（{project_code}）", size=10, color=MUTED,
              alignment=WD_ALIGN_PARAGRAPH.CENTER, space_after=12)

    info_table = doc.add_table(rows=3, cols=2)
    info_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    info_data = [
        ("报告范围", range_label),
        ("涵盖月份", f"{len(snapshots)} 个月"),
        ("项目经理", manager),
    ]
    for i, (label, value) in enumerate(info_data):
        info_table.rows[i].cells[0].text = label
        info_table.rows[i].cells[1].text = str(value)
        for p in info_table.rows[i].cells[0].paragraphs:
            for r in p.runs:
                r.font.size = Pt(10)
                r.font.bold = True
                r.font.color.rgb = NAVY
        for p in info_table.rows[i].cells[1].paragraphs:
            for r in p.runs:
                r.font.size = Pt(10)
                r.font.color.rgb = INK

    # ── 聚合数据汇总（优先使用实际月份范围聚合数据，与平台看板口径一致）──
    summary_data = aggregated_data or quarterly_data
    if summary_data:
        doc.add_paragraph()
        _add_quarterly_section(doc, summary_data, months, quarter_label=quarter_label or "")

    # ── 多月对比表 ──
    doc.add_paragraph()
    _add_para(doc, "多月指标对比", size=14, bold=True, color=NAVY, space_after=6)
    _add_comparison_table(doc, snapshots, allowed_pages=allowed_pages)

    # ── 各月详情 ──
    for i, (snap, month) in enumerate(zip(snapshots, months)):
        doc.add_page_break()
        _add_month_section(doc, snap, month, i, allowed_pages=allowed_pages)

    # ── 页脚 ──
    doc.add_paragraph()
    _add_para(doc,
              f"项目管理快报 · 跨月合并版 · {range_label}",
              size=8, color=GRAY, alignment=WD_ALIGN_PARAGRAPH.CENTER)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path
