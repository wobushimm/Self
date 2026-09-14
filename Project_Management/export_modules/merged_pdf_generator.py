#!/usr/bin/env python3
"""跨月合并 PDF 快报生成器（reportlab）。

结构：
- Page 1: 封面 + 多月指标对比表
- Page 2+: 每月一页（指标卡片 + 关键数据摘要）
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

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

# 对比指标名 → 页面
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

W, H = landscape(A4)
BLUE, NAVY, INK, MUTED = map(HexColor, ("#2864F3", "#173E79", "#1C2941", "#657793"))
GREEN, YELLOW, RED, GRAY = map(HexColor, ("#16A34A", "#D68C00", "#E32828", "#8B98AA"))

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_font_name = "STHeiti"
_font_loaded = False
for _c in _FONT_CANDIDATES:
    if Path(_c).exists():
        try:
            pdfmetrics.registerFont(TTFont(_font_name, _c))
            _font_loaded = True
            break
        except Exception:
            continue
if not _font_loaded:
    import warnings
    warnings.warn("未找到可用中文字体，PDF 中文可能显示异常。")
    _font_name = "Helvetica"
FONT = _font_name


def n(value, default=0.0):
    parsed = number(value)
    return default if parsed is None else parsed


def _text(c, x, y, value, size=11, color=INK, font=FONT):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, str(value))


def _box(c, x, y, w, h, fill=white, stroke=None, radius=8):
    c.setFillColor(fill)
    c.setStrokeColor(stroke or fill)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=bool(stroke))


def _fmt(value, unit="", decimals=1):
    if value is None:
        return "—"
    if unit == "%":
        return f"{value * 100:.{decimals}f}%"
    if unit == "小时":
        return f"{value:.{decimals}f}h"
    if unit == "人月":
        return f"{value:.2f}"
    if unit == "个":
        return f"{value:.0f}"
    return f"{value:.{decimals}f}"


def _build_comparison_rows(snapshots, allowed_pages=None):
    """构建对比表格数据行。返回 [(name, [values], unit), ...]。"""
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


def _draw_cover(c, snapshots, comp_rows):
    """Page 1: 封面 + 对比表。"""
    first_proj = snapshots[0].get("project", {})
    project_name = first_proj.get("project_name", "")
    manager = first_proj.get("manager_name", "待补")
    product_line = first_proj.get("product_line") or first_proj.get("department_name") or "产品线"
    project_code = first_proj.get("project_code", first_proj.get("project_id", ""))
    months = [s.get("project", {}).get("stat_month", "") for s in snapshots]
    range_label = f"{months[0]} ~ {months[-1]}"

    # 顶部色块
    c.setFillColor(NAVY)
    c.rect(0, H - 142, W, 142, fill=1, stroke=0)
    _text(c, 38, H - 55, "跨月合并快报", 30, white)
    _text(c, 38, H - 84, f"{product_line} · {project_name} ({project_code})", 16, HexColor("#E4ECFF"))
    _text(c, 510, H - 55, f"报告范围：{range_label}", 13, white)
    _text(c, 510, H - 82, f"项目经理：{manager}", 13, white)

    # 对比表
    _text(c, 38, H - 175, "多月指标对比", 16, NAVY)

    n_months = len(months)
    col_w = 120  # 指标名列
    val_w = (W - 80 - col_w - 100) / n_months  # 每月一列
    trend_w = 100  # 趋势列
    table_x = 40
    table_y = H - 200
    row_h = 24
    header_h = 28

    # 表头
    _box(c, table_x, table_y - header_h, col_w + val_w * n_months + trend_w, header_h, HexColor("#EDF3FD"))
    _text(c, table_x + 8, table_y - header_h + 8, "指标", 10, HexColor("#3C577E"))
    for j, m in enumerate(months):
        _text(c, table_x + col_w + j * val_w + 8, table_y - header_h + 8, m, 10, HexColor("#3C577E"))
    _text(c, table_x + col_w + n_months * val_w + 8, table_y - header_h + 8, "趋势", 10, HexColor("#3C577E"))

    # 数据行
    for i, (name, values, unit) in enumerate(comp_rows):
        ry = table_y - header_h - (i + 1) * row_h
        bg = white if i % 2 == 0 else HexColor("#F8FAFD")
        _box(c, table_x, ry, col_w + val_w * n_months + trend_w, row_h, bg)
        _text(c, table_x + 8, ry + 6, name, 9, HexColor("#3C577E"))

        for j, val in enumerate(values):
            vx = table_x + col_w + j * val_w + 8
            if val is None:
                _text(c, vx, ry + 6, "—", 9, GRAY)
            else:
                _text(c, vx, ry + 6, _fmt(val, unit), 9, INK)

        # 趋势
        valid = [v for v in values if v is not None]
        tx = table_x + col_w + n_months * val_w + 8
        if len(valid) >= 2:
            diff = valid[-1] - valid[-2]
            if abs(diff) < 0.001:
                _text(c, tx, ry + 6, "→ 持平", 8, MUTED)
            elif diff > 0:
                _text(c, tx, ry + 6, f"↑ +{_fmt(diff, unit)}", 8, GREEN)
            else:
                _text(c, tx, ry + 6, f"↓ {_fmt(diff, unit)}", 8, RED)
        else:
            _text(c, tx, ry + 6, "—", 8, GRAY)

    c.showPage()


def _draw_month_detail(c, snap, month, idx, total, allowed_pages=None):
    """每月一页：指标卡片 + 关键数据摘要。"""
    proj = snap.get("project", {})
    bug_detail = snap.get("bug_detail", {})

    metrics, _ = create_metrics(snap)
    # 按页面过滤指标
    if allowed_pages is not None:
        metrics = [m for m in metrics
                   if any(p in allowed_pages for p in _METRIC_PAGE.get(m.code, []))]
    items = {m.code: m for m in metrics}

    # 页眉
    c.setFillColor(NAVY)
    c.rect(0, H - 72, W, 72, fill=1, stroke=0)
    _text(c, 35, H - 38, f"{month} 详细数据", 23, white)
    _text(c, 36, H - 58, f"第 {idx + 1} / {total} 个月", 11, HexColor("#DCE7FF"))

    # 状态统计
    counts = {level: sum(1 for m in metrics if m.warning_level == level)
              for level in ("normal", "concern", "intervention")}
    missing = sum(1 for m in metrics if m.warning_level == "unavailable")

    levels = [
        ("正常", counts["normal"], GREEN),
        ("关注", counts["concern"], YELLOW),
        ("干预", counts["intervention"], RED),
        ("待补", missing, GRAY),
    ]
    for i, (label, count, color) in enumerate(levels):
        x = 38 + i * 193
        _box(c, x, H - 140, 169, 50, HexColor("#F7FAFF"))
        c.setFillColor(color)
        c.circle(x + 25, H - 115, 10, fill=1, stroke=0)
        _text(c, x + 47, H - 108, f"{count}", 22, INK)
        _text(c, x + 47, H - 130, f"{label}指标", 10, MUTED)

    # 指标卡片
    codes = (
        "workhour_deviation_rate", "budget_deviation_rate",
        "sprint_completion_rate", "defect_escape_rate",
        "bug_close_rate", "req_verify_rate",
        "requirement_cost", "mttr", "rd_resource_input_ratio",
    )
    state_map = {"normal": ("正常", GREEN), "concern": ("关注", YELLOW),
                 "intervention": ("干预", RED), "unavailable": ("待补", GRAY),
                 "unconfigured": ("参考", GRAY)}

    for i, code in enumerate(codes):
        if code not in items:
            continue
        m = items[code]
        x = 38 + (i % 4) * 193
        y = 290 - (i // 4) * 130
        label, color = state_map.get(m.warning_level, ("待补", GRAY))

        _box(c, x, y, 169, 108, white)
        c.setFillColor(color)
        c.rect(x, y, 5, 108, fill=1, stroke=0)
        _text(c, x + 18, y + 78, m.name, 12, MUTED)

        if m.display_value:
            val_str = m.display_value
        elif m.value is None:
            val_str = "待接入"
        elif m.unit == "%":
            val_str = f"{m.value:.1%}"
        elif m.unit == "小时":
            val_str = f"{m.value:.2f} 小时"
        elif m.unit == "个":
            val_str = f"{m.value:.0f}"
        else:
            val_str = f"{m.value:.2f}"
        fs = 17 if len(val_str) > 12 else 22
        _text(c, x + 18, y + 45, val_str, fs, color)
        _text(c, x + 18, y + 19, f"状态：{label}", 9, MUTED)

    # 底部摘要
    actual_hour = n(proj.get("actual_total_man_hour"))
    plan_hour = n(proj.get("plan_total_man_hour"))
    task_done = n(proj.get("task_finish_count"))
    task_total = n(proj.get("task_count"))
    total_bug = n(bug_detail.get("bug_total_count"))
    closed_bug = n(bug_detail.get("bug_closed_count"))
    avg_att = n(proj.get("attendance_hour"))

    _box(c, 38, 35, W - 76, 100, HexColor("#F8FAFD"))
    _text(c, 56, 110, "关键数据摘要", 13, NAVY)
    summary_items = [
        f"任务完成: {task_done:.0f}/{task_total:.0f}",
        f"实际工时: {actual_hour:.0f}h / 计划: {plan_hour:.0f}h",
        f"Bug 总数/已关闭: {total_bug:.0f}/{closed_bug:.0f}",
        f"平均考勤: {avg_att:.1f}h",
    ]
    for j, s in enumerate(summary_items):
        _text(c, 56 + j * 180, 80, s, 10, MUTED)

    c.showPage()


def _draw_quarterly_page(c, quarterly_data, months, quarter_label=""):
    """绘制季度汇总页。"""
    q = quarterly_data
    display_label = quarter_label or f"{months[0]} ~ {months[-1]}"
    range_detail = f"{months[0]} ~ {months[-1]}" if quarter_label else ""

    # 页眉
    c.setFillColor(NAVY)
    c.rect(0, H - 72, W, 72, fill=1, stroke=0)
    _text(c, 35, H - 38, f"{display_label} 季度汇总", 23, white)
    _text(c, 36, H - 58, f"数据来源：产品线看板 API 季度聚合口径 ({range_detail})", 11, HexColor("#DCE7FF"))

    def _fp(v, unit="%"):
        if v is None:
            return "—"
        if unit == "%":
            return f"{v * 100:.1f}%"
        if unit == "h":
            return f"{v:,.0f}h"
        return f"{v:,.0f}"

    # 计算偏差率
    ah = n(q.get("actual_total_man_hour"))
    ph = n(q.get("plan_total_man_hour")) or 1
    wdr = (ah - ph) / ph if ph else None
    adc = n(q.get("actual_dev_cost")) + n(q.get("actual_staff_cost"))
    pdc = n(q.get("plan_dev_cost")) + n(q.get("plan_staff_cost"))
    bdr = (adc - pdc) / pdc if pdc else None

    sprint_rate = n(q.get("task_finish_count")) / n(q.get("task_count")) if n(q.get("task_count")) else None

    cards = [
        ("实际总工时", _fp(ah, "h"), f"计划 {_fp(ph, 'h')}"),
        ("工时偏差率", _fp(wdr), "±15% 以内为正常"),
        ("预算偏差率", _fp(bdr), "±10% 以内为正常"),
        ("Sprint完成率", _fp(sprint_rate), f"完成 {int(n(q.get('task_finish_count')))}/{int(n(q.get('task_count')))} 项"),
        ("Bug关闭率", _fp(n(q.get("bug_close_rate"))), "目标 ≥95%"),
        ("缺陷逃逸率", _fp(n(q.get("bug_leak_rate"))), "目标 ≤10%"),
        ("任务完成数", _fp(n(q.get("task_finish_count")), "个"), f"延期 {int(n(q.get('task_delay_count')))} 项"),
        ("Bug总数", _fp(n(q.get("bug_total_count")), "个"), ""),
    ]

    # 绘制卡片网格 (4列 x 2行)
    card_w = 175
    card_h = 95
    gap = 18
    start_x = 38
    start_y = H - 100  # 卡片顶部

    for i, (name, val, sub) in enumerate(cards):
        col = i % 4
        row = i // 4
        x = start_x + col * (card_w + gap)
        y = start_y - row * (card_h + gap) - card_h

        _box(c, x, y, card_w, card_h, HexColor("#F7FAFF"))
        c.setFillColor(BLUE)
        c.rect(x, y, 4, card_h, fill=1, stroke=0)
        _text(c, x + 16, y + card_h - 22, name, 10, MUTED)
        _text(c, x + 16, y + card_h - 55, val, 20, INK)
        if sub:
            _text(c, x + 16, y + 10, sub, 8, MUTED)

    # 底部说明
    _text(c, 38, 50, "可加指标（工时、成本、任务数）为各月之和，比率指标为重新计算。", 9, MUTED)
    c.showPage()


def export_merged_pdf(snapshots: list[dict], output_path: Path, quarterly_data: dict = None,
                      quarter_label: str = "", aggregated_data: dict = None,
                      page_ids: list[str] = None) -> Path:
    """生成跨月合并 PDF 报告。"""
    if not snapshots:
        raise ValueError("snapshots 列表不能为空")

    first_proj = snapshots[0].get("project", {})
    project_name = first_proj.get("project_name", "unknown")
    months = [s.get("project", {}).get("stat_month", "") for s in snapshots]
    range_label = f"{months[0]}_{months[-1]}"

    # 解析页面选择
    allowed_pages = _resolve_page_ids(page_ids)

    c = canvas.Canvas(str(output_path), pagesize=landscape(A4))
    c.setTitle(f"{project_name}_{range_label}_跨月合并快报")

    # Page 1: 封面 + 对比表
    comp_rows = _build_comparison_rows(snapshots, allowed_pages=allowed_pages)
    _draw_cover(c, snapshots, comp_rows)

    # Page 2: 聚合数据汇总（优先使用实际月份范围聚合数据，与平台看板口径一致）
    months = [s.get("project", {}).get("stat_month", "") for s in snapshots]
    summary_data = aggregated_data or quarterly_data
    if summary_data:
        _draw_quarterly_page(c, summary_data, months, quarter_label=quarter_label or "")

    # Page 3+: 每月详情
    total = len(snapshots)
    for i, (snap, month) in enumerate(zip(snapshots, months)):
        _draw_month_detail(c, snap, month, i, total, allowed_pages=allowed_pages)

    c.save()
    return output_path
