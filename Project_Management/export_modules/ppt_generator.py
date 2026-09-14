"""PPT 生成器 — 项目管理月度快报。

基于 python-pptx，为每个领域页面生成幻灯片：
- 封面页：项目名 + 月份 + 9 项核心指标摘要
- 每个选中的领域页面对应一张幻灯片（表格 + 关键指标）
- 末页：数据质量说明
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

try:
    from pptx import Presentation
    from pptx.util import Cm, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
except ImportError:
    pass  # python-pptx 未安装时延迟到 export_pptx() 报错

from data_modules.metrics_engine import create_metrics, number
from export_modules.html_generator import resolve_page_ids, _ALL_PAGE_IDS

logger = logging.getLogger(__name__)

# 统一配色
_COLOR_PRIMARY = "173E79"   # 深蓝
_COLOR_ACCENT = "2864F3"    # 亮蓝
_COLOR_WHITE = "FFFFFF"
_COLOR_LIGHT_BG = "F0F4FA"  # 浅蓝斑马纹
_COLOR_GREY = "999999"
_COLOR_DARK = "333333"
_COLOR_GREEN = "16A34A"
_COLOR_YELLOW = "D68C00"
_COLOR_RED = "E32828"

# 页面标题映射
_PAGE_TITLES = {
    "p01_fine_management": "① 产研精细化管理",
    "p02_investment_deviation": "② 项目投入偏差",
    "p03_staff_health": "③ 人员投入健康度",
    "p04_product_quality": "④ 产品质量",
    "p05_bug_efficiency": "⑤ Bug 修复效率",
    "p06_value_delivery": "⑥ 需求价值交付",
}


def n(value, default=0.0):
    parsed = number(value)
    return default if parsed is None else parsed


def _metric_value(metric):
    if metric.display_value:
        return metric.display_value
    if metric.value is None:
        return "待接入"
    if metric.unit == "%":
        return f"{metric.value:.1%}"
    if metric.unit == "小时":
        return f"{metric.value:.2f}h"
    if metric.unit == "个":
        return f"{metric.value:.0f}"
    return f"{metric.value:.2f}"


def export_pptx(snap: dict, output_path: Path, page_ids=None) -> Path:
    """生成 PPT 文件。

    Args:
        snap: 统一数据快照
        output_path: 输出 .pptx 路径
        page_ids: 可选页面选择

    Returns:
        生成的文件路径
    """
    try:
        from pptx import Presentation
        from pptx.util import Cm, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    except ImportError:
        raise ImportError("python-pptx 未安装: pip install python-pptx")

    resolved = resolve_page_ids(page_ids)
    proj = snap.get("project", {})
    bug_detail = snap.get("bug_detail", {})
    staff_list = snap.get("staff_daily_workhours", [])
    quality_history = snap.get("quality_history", [])

    metrics, _ = create_metrics(snap)
    items = {m.code: m for m in metrics}

    project_name = proj.get("project_name", "未知项目")
    month = proj.get("stat_month", "")
    manager = proj.get("manager_name", "待补")
    project_code = proj.get("project_code", proj.get("project_id", ""))
    product_line = proj.get("product_line") or proj.get("department_name") or "产品线"

    prs = Presentation()
    prs.slide_width = Cm(33.867)
    prs.slide_height = Cm(19.05)

    # ══════ 封面页 ══════
    _add_cover_slide(prs, project_name, month, manager, project_code, product_line, metrics)

    # ══════ 指标总览页 ══════
    _add_metrics_slide(prs, metrics, proj)

    # ══════ 各领域内容页 ══════
    closed_bug_count = bug_detail.get("bug_closed_count") or 0
    total_bug_count = bug_detail.get("bug_total_count") or 0

    # 构建历史数据
    history = []
    for r in quality_history:
        sm = str(r.get("settlement_month") or r.get("stat_month", ""))
        is_cur = sm == str(proj.get("stat_month", ""))
        history.append({
            "statMonth": sm,
            "actualWorkhourMonth": n(r.get("actual_workhour_month")) or (n(proj.get("actual_total_man_hour")) if is_cur else 0),
            "planWorkhourMonth": n(r.get("estimated_workhour_month")) or (n(proj.get("plan_total_man_hour")) if is_cur else 0),
            "rdActualCostMonth": n(r.get("rd_actual_cost_month")) or (n(proj.get("actual_dev_cost")) if is_cur else 0),
            "rdBudgetCostMonth": n(r.get("rd_budget_cost_month")) or (n(proj.get("plan_dev_cost")) if is_cur else 0),
        })

    # p01: 产研精细化管理
    if resolved is None or "p01_fine_management" in resolved:
        _add_p01_slide(prs, proj, items)

    # p02: 项目投入偏差
    if resolved is None or "p02_investment_deviation" in resolved:
        _add_p02_slide(prs, proj, items, history)

    # p03: 人员投入健康度
    if resolved is None or "p03_staff_health" in resolved:
        _add_p03_slide(prs, proj, staff_list)

    # p04: 产品质量
    if resolved is None or "p04_product_quality" in resolved:
        _add_p04_slide(prs, items, bug_detail, closed_bug_count, total_bug_count)

    # p05: Bug 修复效率
    if resolved is None or "p05_bug_efficiency" in resolved:
        _add_p05_slide(prs, items, closed_bug_count, total_bug_count, bug_detail)

    # p06: 需求价值交付
    if resolved is None or "p06_value_delivery" in resolved:
        _add_p06_slide(prs, proj, items)

    # ══════ 末页：数据质量 ══════
    _add_footer_slide(prs, project_name, month, snap)

    # ══════ 保存 ══════
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    logger.info(f"PPT 已生成: {output_path}")
    return output_path


# ══════════════════════════════════════════════════════════
# 幻灯片构建辅助函数
# ══════════════════════════════════════════════════════════

def _add_textbox(slide, left_cm, top_cm, width_cm, height_cm, text, *,
                 font_size=14, bold=False, color=_COLOR_DARK, align=None):
    """添加文本框的通用辅助函数。"""
    from pptx.util import Cm, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    txBox = slide.shapes.add_textbox(Cm(left_cm), Cm(top_cm), Cm(width_cm), Cm(height_cm))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = RGBColor.from_string(color)
    if align:
        p.alignment = align
    return tf


def _add_cover_slide(prs, project_name, month, manager, project_code, product_line, metrics):
    """封面页。"""
    from pptx.enum.text import PP_ALIGN

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor.from_string(_COLOR_PRIMARY)

    _add_textbox(slide, 3, 3, 27.867, 2, "项目管理快报", font_size=32, bold=True,
                 color=_COLOR_WHITE, align=PP_ALIGN.CENTER)
    _add_textbox(slide, 3, 5.5, 27.867, 2, f"{product_line} · {project_name}",
                 font_size=22, bold=True, color=_COLOR_WHITE, align=PP_ALIGN.CENTER)
    _add_textbox(slide, 3, 8, 27.867, 1.5, f"（{project_code}）",
                 font_size=14, color="CCCCCC", align=PP_ALIGN.CENTER)
    _add_textbox(slide, 3, 10.5, 13, 1, f"报告期：{month}", font_size=14, color="CCDDEE")
    _add_textbox(slide, 3, 12, 13, 1, f"项目经理：{manager}", font_size=14, color="CCDDEE")

    # 指标摘要
    items = {m.code: m for m in metrics}
    summary_lines = []
    for code in ("sprint_completion_rate", "workhour_deviation_rate", "budget_deviation_rate",
                 "defect_escape_rate", "bug_close_rate", "req_verify_rate"):
        m = items.get(code)
        if m:
            summary_lines.append(f"{m.name}: {_metric_value(m)}")
    if summary_lines:
        _add_textbox(slide, 3, 14, 27.867, 4, "\n".join(summary_lines[:6]),
                     font_size=11, color="AABBDD", align=PP_ALIGN.CENTER)


def _add_metrics_slide(prs, metrics, proj):
    """9 项指标总览页。"""
    from pptx.util import Cm, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, 1.5, 0.8, 30.867, 1.5, "本期核心指标", font_size=22, bold=True, color=_COLOR_PRIMARY)

    # 指标表格
    table_shape = slide.shapes.add_table(len(metrics) + 1, 5, Cm(1.5), Cm(2.8), Cm(30.867), Cm(13))
    table = table_shape.table

    headers = ["指标名称", "当前值", "目标值", "计算口径", "预警等级"]
    targets = {
        "req_verify_rate": "≥85%", "sprint_completion_rate": "≥85%",
        "workhour_deviation_rate": "±15%", "budget_deviation_rate": "±10%",
        "requirement_cost": "≤3人天", "defect_escape_rate": "≤3%",
        "bug_close_rate": "≥95%", "mttr": "P0≤4h P1≤24h",
        "rd_resource_input_ratio": "研发人力/组织总人数",
    }
    labels = {"normal": "正常", "concern": "关注", "intervention": "干预", "unconfigured": "参考", "unavailable": "待补"}

    col_widths = [Cm(6), Cm(4), Cm(4), Cm(12), Cm(4.867)]
    for i, w in enumerate(col_widths):
        table.columns[i].width = w

    for i, h in enumerate(headers):
        cell = table.cell(0, i)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(_COLOR_PRIMARY)
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(10)
            p.font.bold = True
            p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            p.alignment = PP_ALIGN.CENTER

    for row_idx, m in enumerate(metrics):
        values = [m.name, _metric_value(m), targets.get(m.code, ""), m.formula[:40], labels.get(m.warning_level, "待补")]
        for col_idx, val in enumerate(values):
            cell = table.cell(row_idx + 1, col_idx)
            cell.text = str(val)
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(8)
                p.font.color.rgb = RGBColor.from_string(_COLOR_DARK)
            if row_idx % 2 == 1:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor.from_string(_COLOR_LIGHT_BG)


def _add_p01_slide(prs, proj, items):
    """① 产研精细化管理。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, 1.5, 0.8, 30.867, 1.5, "① 产研精细化管理 — 交付节奏是否可控？",
                 font_size=20, bold=True, color=_COLOR_PRIMARY)

    task_total = n(proj.get("task_count"))
    task_done = n(proj.get("task_finish_count"))
    task_delay = n(proj.get("task_delay_count"))
    high_diff = n(proj.get("high_difficulty_task_count"))

    table_shape = slide.shapes.add_table(5, 2, Cm(1.5), Cm(3), Cm(14), Cm(6))
    table = table_shape.table
    table.columns[0].width = Cm(7)
    table.columns[1].width = Cm(7)

    data = [("任务总数", f"{task_total:.0f}"), ("已完成", f"{task_done:.0f}"),
            ("延期任务", f"{task_delay:.0f}"), ("高难任务", f"{high_diff:.0f}")]
    _fill_table(table, ["类别", "数量"], data)

    _add_textbox(slide, 1.5, 10, 30.867, 3,
                 f"Sprint 完成率: {_metric_value(items['sprint_completion_rate'])}\n"
                 f"建议: 按需求变更、资源不足、技术阻塞拆分延期原因",
                 font_size=11, color="666666")


def _add_p02_slide(prs, proj, items, history):
    """② 项目投入偏差。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, 1.5, 0.8, 30.867, 1.5, "② 项目投入偏差 — 钱和时间花在哪？",
                 font_size=20, bold=True, color=_COLOR_PRIMARY)

    actual_hours = n(proj.get("actual_total_man_hour"))
    plan_hours = n(proj.get("plan_total_man_hour"))
    total_actual = n(proj.get("actual_dev_cost")) + n(proj.get("actual_staff_cost"))
    total_budget = n(proj.get("plan_dev_cost")) + n(proj.get("plan_staff_cost"))

    data = [
        ("实际工时", f"{actual_hours:,.0f}h"),
        ("计划工时", f"{plan_hours:,.0f}h"),
        ("实际成本", f"{total_actual:,.0f} 元"),
        ("预算成本", f"{total_budget:,.0f} 元"),
        ("工时偏差率", _metric_value(items["workhour_deviation_rate"])),
        ("预算偏差率", _metric_value(items["budget_deviation_rate"])),
    ]
    table_shape = slide.shapes.add_table(len(data) + 1, 2, Cm(1.5), Cm(3), Cm(14), Cm(8))
    _fill_table(table_shape.table, ["项目", "值"], data)


def _add_p03_slide(prs, proj, staff_list):
    """③ 人员投入健康度。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, 1.5, 0.8, 30.867, 1.5, "③ 人员投入健康度 — 团队是否在透支？",
                 font_size=20, bold=True, color=_COLOR_PRIMARY)

    staff_sorted = sorted(staff_list, key=lambda r: n(r.get("allocated_hour")), reverse=True)[:10]
    if staff_sorted:
        data = [(r.get("staff_name", "成员"), f"{n(r.get('allocated_hour')):.1f}h") for r in staff_sorted]
        table_shape = slide.shapes.add_table(len(data) + 1, 2, Cm(1.5), Cm(3), Cm(14), Cm(10))
        _fill_table(table_shape.table, ["成员", "投入工时"], data)
    else:
        _add_textbox(slide, 1.5, 4, 30.867, 3, "暂无成员工时数据", font_size=14, color="999999")


def _add_p04_slide(prs, items, bug_detail, closed_bug_count, total_bug_count):
    """④ 产品质量。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, 1.5, 0.8, 30.867, 1.5, "④ 产品质量 — 交付物靠不靠谱？",
                 font_size=20, bold=True, color=_COLOR_PRIMARY)

    data = [
        ("缺陷逃逸率", _metric_value(items["defect_escape_rate"])),
        ("Bug 关闭率", _metric_value(items["bug_close_rate"])),
        ("线上 Bug", str(bug_detail.get("field_bug_count", 0))),
        ("Bug 总数", str(total_bug_count)),
        ("已关闭", str(closed_bug_count)),
    ]
    table_shape = slide.shapes.add_table(len(data) + 1, 2, Cm(1.5), Cm(3), Cm(14), Cm(6))
    _fill_table(table_shape.table, ["指标", "值"], data)


def _add_p05_slide(prs, items, closed_bug_count, total_bug_count, bug_detail):
    """⑤ Bug 修复效率。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, 1.5, 0.8, 30.867, 1.5, "⑤ Bug 修复效率 — 问题解决得快不快？",
                 font_size=20, bold=True, color=_COLOR_PRIMARY)

    mttr_val = items["mttr"].value or 0
    fix_duration = bug_detail.get('bug_resolve_duration_exclude_reject_third') or 0
    data = [
        ("已关闭/总数", f"{closed_bug_count}/{total_bug_count}"),
        ("MTTR", f"{mttr_val:.2f} 小时"),
        ("修复时长(秒)", f"{fix_duration:,.0f}"),
    ]
    table_shape = slide.shapes.add_table(len(data) + 1, 2, Cm(1.5), Cm(3), Cm(14), Cm(4))
    _fill_table(table_shape.table, ["指标", "值"], data)


def _add_p06_slide(prs, proj, items):
    """⑥ 需求价值交付。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, 1.5, 0.8, 30.867, 1.5, "⑥ 需求价值交付 — 做的东西有没有用？",
                 font_size=20, bold=True, color=_COLOR_PRIMARY)

    data = [
        ("需求成本", _metric_value(items["requirement_cost"])),
        ("研发资源占比", _metric_value(items["rd_resource_input_ratio"])),
        ("研发工时", f"{n(proj.get('actual_rd_man_hour')):,.1f}"),
        ("测试工时", f"{n(proj.get('actual_test_man_hour')):,.1f}"),
    ]
    table_shape = slide.shapes.add_table(len(data) + 1, 2, Cm(1.5), Cm(3), Cm(14), Cm(5))
    _fill_table(table_shape.table, ["指标", "值"], data)


def _add_footer_slide(prs, project_name, month, snap):
    """末页：数据质量说明。"""
    from pptx.enum.text import PP_ALIGN

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor.from_string("F5F7FA")

    _add_textbox(slide, 3, 4, 27.867, 2, "数据质量说明", font_size=24, bold=True,
                 color=_COLOR_PRIMARY, align=PP_ALIGN.CENTER)

    dq = snap.get("data_quality", {})
    missing = dq.get("missing_fields", [])
    warnings = dq.get("warnings", [])

    lines = [f"项目：{project_name}  月份：{month}", ""]
    lines.append(f"缺失字段: {len(missing)} 项" if missing else "缺失字段: 无")
    lines.append(f"警告: {len(warnings)} 项" if warnings else "警告: 无")
    lines.append("")
    lines.append("本报告由 PMO 项目管理快报 Skill 自动生成")

    _add_textbox(slide, 3, 7, 27.867, 8, "\n".join(lines),
                 font_size=14, color="666666", align=PP_ALIGN.CENTER)


def _fill_table(table, headers, data):
    """填充表格的表头和数据行。"""
    from pptx.util import Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    n_cols = len(headers)
    for i, h in enumerate(headers):
        cell = table.cell(0, i)
        cell.text = str(h)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(_COLOR_PRIMARY)
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(10)
            p.font.bold = True
            p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            p.alignment = PP_ALIGN.CENTER

    for row_idx, row_data in enumerate(data):
        for col_idx in range(n_cols):
            val = row_data[col_idx] if col_idx < len(row_data) else "—"
            cell = table.cell(row_idx + 1, col_idx)
            cell.text = str(val)
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(9)
                p.font.color.rgb = RGBColor.from_string(_COLOR_DARK)
                p.alignment = PP_ALIGN.LEFT if col_idx == 0 else PP_ALIGN.RIGHT
            if row_idx % 2 == 1:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor.from_string(_COLOR_LIGHT_BG)
