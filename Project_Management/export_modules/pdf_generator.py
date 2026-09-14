#!/usr/bin/env python3
"""Native PDF export for the PMO HTML report (no browser print dialog needed)."""

from __future__ import annotations

import sys
from math import pi
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from data_modules.metrics_engine import create_metrics, number
from export_modules.html_generator import resolve_page_ids

W, H = landscape(A4)
BLUE, NAVY, INK, MUTED = map(HexColor, ("#2864F3", "#173E79", "#1C2941", "#657793"))
GREEN, YELLOW, RED, GRAY = map(HexColor, ("#16A34A", "#D68C00", "#E32828", "#8B98AA"))

# 跨平台中文字体检测
_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",      # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
]

_font_name = "STHeiti"
_font_loaded = False
for _candidate in _FONT_CANDIDATES:
    if Path(_candidate).exists():
        try:
            pdfmetrics.registerFont(TTFont(_font_name, _candidate))
            _font_loaded = True
            break
        except Exception:
            continue

if not _font_loaded:
    # 回退：使用 reportlab 内置字体（中文可能显示异常）
    import warnings
    warnings.warn("未找到可用中文字体，PDF 中文可能显示异常。")
    _font_name = "Helvetica"

FONT = _font_name


def n(value, default=0.0):
    value = number(value)
    return default if value is None else value


def text(c, x, y, value, size=11, color=INK, font=FONT):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, str(value))


def box(c, x, y, w, h, fill=white, stroke=None, radius=10):
    c.setFillColor(fill)
    c.setStrokeColor(stroke or fill)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=bool(stroke))


def state(metric):
    mapping = {"normal": ("正常", GREEN), "concern": ("关注", YELLOW), "intervention": ("干预", RED)}
    return mapping.get(metric.warning_level, ("待补", GRAY))


def metric_value(metric):
    if metric.display_value:
        return metric.display_value
    if metric.value is None:
        return "待接入"
    if metric.unit == "%":
        return f"{metric.value:.1%}"
    if metric.unit == "小时":
        return f"{metric.value:.2f} 小时"
    return f"{metric.value:.0f}" if metric.unit == "个" else f"{metric.value:.2f}"


def page_header(c, title, question, decision):
    c.setFillColor(NAVY)
    c.rect(0, H - 72, W, 72, fill=1, stroke=0)
    text(c, 35, H - 38, title, 23, white)
    text(c, 36, H - 58, question, 11, HexColor("#DCE7FF"))
    box(c, W - 308, H - 57, 273, 29, HexColor("#EFF5FF"))
    text(c, W - 298, H - 46, decision, 9, HexColor("#365886"))


def bar_chart(c, x, y, w, h, records, title, colors=None):
    box(c, x, y, w, h, white)
    text(c, x + 18, y + h - 24, title, 14)
    if not records:
        text(c, x + 28, y + h / 2, "待接入足够的历史数据后展示趋势", 12, MUTED)
        return
    maximum = max(
        value for record in records for key, value in record.items() if key != "label"
    ) or 1
    chart_y, chart_h, chart_x = y + 34, h - 82, x + 48
    c.setStrokeColor(HexColor("#E6EDF6"))
    for i in range(5):
        yy = chart_y + chart_h * i / 4
        c.line(chart_x, yy, x + w - 18, yy)
    keys = [k for k in records[0] if k != "label"]
    group = (w - 75) / len(records)
    palette = colors or [BLUE, GREEN, YELLOW, RED]
    for idx, rec in enumerate(records):
        for j, key in enumerate(keys):
            bw = group / (len(keys) + 1)
            bh = rec.get(key, 0) / maximum * chart_h
            c.setFillColor(palette[j % len(palette)])
            c.rect(chart_x + idx * group + bw * (j + .45), chart_y, bw * .72, bh, fill=1, stroke=0)
        text(c, chart_x + idx * group + group * .25, y + 17, rec["label"], 9, MUTED)
    for i, key in enumerate(keys):
        c.setFillColor(palette[i % len(palette)])
        c.rect(x + 20 + i * 94, y + h - 43, 8, 8, fill=1, stroke=0)
        text(c, x + 31 + i * 94, y + h - 43, key, 8, MUTED)


def donut(c, x, y, w, h, parts, title):
    box(c, x, y, w, h, white)
    text(c, x + 18, y + h - 24, title, 14)
    total_raw = sum(v for _, v in parts)
    total = total_raw or 1
    center_x, center_y, radius, start = x + w * .34, y + h * .48, min(w, h) * .20, 90
    colors = [BLUE, GREEN, YELLOW, RED]
    if total_raw > 0:
        for i, (_, value) in enumerate(parts):
            extent = value / total * 360
            if extent < 0.1:
                continue
            c.setStrokeColor(colors[i])
            c.setLineWidth(radius * .42)
            c.arc(center_x-radius, center_y-radius, center_x+radius, center_y+radius, start, extent)
            start += extent
    else:
        c.setStrokeColor(GRAY)
        c.setLineWidth(radius * .42)
        c.circle(center_x, center_y, radius, stroke=1, fill=0)
    text(c, center_x - 10, center_y - 4, f"{total_raw:.0f}", 18, INK)
    for i, (label, value) in enumerate(parts):
        c.setFillColor(colors[i]); c.circle(x + w*.63, y + h*.63 - i*27, 5, fill=1, stroke=0)
        text(c, x + w*.63 + 12, y + h*.63 - 4 - i*27, f"{label}  {value:.1f}", 10, MUTED)


def notes(c, left_title, left, right_title, right):
    y, h, gap, w = 35, 95, 16, (W - 86) / 2
    for x, title, body in ((35, left_title, left), (35 + w + gap, right_title, right)):
        box(c, x, y, w, h, HexColor("#F8FAFD"))
        text(c, x + 18, y + h - 25, title, 13)
        c.setFillColor(MUTED); c.setFont(FONT, 10)
        words, line, yy = str(body), "", y + h - 47
        for char in words:
            line += char
            if len(line) >= 30:
                c.drawString(x + 18, yy, line); yy -= 17; line = ""
        if line: c.drawString(x + 18, yy, line)


def export_pdf(snap, output_path: Path, page_ids=None):
    proj = snap.get("project", {})
    quality_cur = snap.get("quality_current", {})
    bug_detail = snap.get("bug_detail", {})
    staff_list = snap.get("staff_daily_workhours", [])
    quality_history = snap.get("quality_history", [])
    closed_bug_count = bug_detail.get("bug_closed_count") or 0
    total_bug_count = bug_detail.get("bug_total_count") or 0

    metrics, _ = create_metrics(snap)
    items = {m.code: m for m in metrics}
    # MTTR 已在 metrics_engine 中计算，不再强制覆盖
    c = canvas.Canvas(str(output_path), pagesize=landscape(A4)); c.setTitle(f"{proj.get('project_name', '')}_{proj.get('stat_month', '')}_项目管理月度快报")
    resolved = resolve_page_ids(page_ids)
    # Page 1 overview
    c.setFillColor(NAVY); c.rect(0, H-142, W, 142, fill=1, stroke=0)
    product_line = proj.get('product_line') or proj.get('department_name') or '产品线'
    text(c, 38, H-55, "项目管理快报", 30, white); text(c, 38, H-84, f"{product_line} · {proj.get('project_name', '')} ({proj.get('project_code', proj.get('project_id', ''))})", 16, HexColor('#E4ECFF'))
    text(c, 510, H-55, f"报告期：{proj.get('stat_month', '')}", 13, white); text(c, 510, H-82, f"项目经理：{proj.get('manager_name', '待补')}", 13, white)
    levels = [("正常", sum(m.warning_level=="normal" for m in metrics), GREEN), ("关注", sum(m.warning_level=="concern" for m in metrics), YELLOW), ("干预", sum(m.warning_level=="intervention" for m in metrics), RED), ("待补", sum(m.warning_level in ('unavailable','unconfigured') for m in metrics), GRAY)]
    for i,(label,count,color) in enumerate(levels):
        x=38+i*193; box(c,x,H-220,169,55,HexColor('#F7FAFF')); c.setFillColor(color); c.circle(x+25,H-193,10,fill=1,stroke=0); text(c,x+47,H-198,f"{count}",22,INK); text(c,x+47,H-215,f"{label}指标",10,MUTED)
    codes=("workhour_deviation_rate","budget_deviation_rate","sprint_completion_rate","defect_escape_rate","bug_close_rate","req_verify_rate","requirement_cost","mttr","rd_resource_input_ratio")
    for i, code in enumerate(codes):
        m=items[code]; x=38+(i%4)*193; y=190-(i//4)*130; label,color=state(m); box(c,x,y,169,108,white); c.setFillColor(color); c.rect(x,y,5,108,fill=1,stroke=0); text(c,x+18,y+78,m.name,12,MUTED); value=metric_value(m); text(c,x+18,y+45,value,17 if len(value)>12 else 22,color); text(c,x+18,y+19,f"状态：{label}",9,MUTED)
    text(c,38,25,"决策提示：工时与预算偏差超阈值，建议专项复核计划基线及成本归集。",11,RED); c.showPage()
    history = []
    # Page 2
    for r in quality_history:
        sm = str(r.get("settlement_month") or r.get("stat_month", ""))
        is_cur = sm == str(proj.get("stat_month", ""))
        history.append({"statMonth": sm, "requirementVerifyPassRate": r.get("req_verify_rate"), "bugCloseRate": r.get("bug_close_rate"), "bugLeakRate": r.get("bug_leak_rate"), "actualWorkhourMonth": n(r.get("actual_workhour_month")) or (n(proj.get("actual_total_man_hour")) if is_cur else 0), "planWorkhourMonth": n(r.get("estimated_workhour_month")) or (n(proj.get("plan_total_man_hour")) if is_cur else 0), "rdActualCostMonth": n(r.get("rd_actual_cost_month")) or (n(proj.get("actual_dev_cost")) if is_cur else 0), "rdBudgetCostMonth": n(r.get("rd_budget_cost_month")) or (n(proj.get("plan_dev_cost")) if is_cur else 0), "staffActualCostMonth": n(r.get("staff_actual_cost_month")) or (n(proj.get("actual_staff_cost")) if is_cur else 0), "staffBudgetCostMonth": n(r.get("staff_budget_cost_month")) or (n(proj.get("plan_staff_cost")) if is_cur else 0)})
    # Page 2
    page_header(c,"① 产研精细化管理","交付节奏是否可控？",f"完成 {n(proj.get('task_finish_count')):.0f}/{n(proj.get('task_count')):.0f} 项")
    bar_chart(c,35,155,365,290,[{"label":"任务总数","value":n(proj.get('task_count'))},{"label":"已完成","value":n(proj.get('task_finish_count'))},{"label":"延期","value":n(proj.get('task_delay_count'))},{"label":"高难","value":n(proj.get('high_difficulty_task_count'))}],"任务完成情况")
    verify=[{"label":str(r.get('statMonth',''))[5:7]+'月',"value":n(r.get('requirementVerifyPassRate'))*100} for r in history if number(r.get('requirementVerifyPassRate')) is not None]
    bar_chart(c,430,155,377,290,verify,"需求验证通过率趋势",[BLUE])
    notes(c,"本期结论",f"Sprint 完成率 {metric_value(items['sprint_completion_rate'])}，但延期任务仍需复盘。","建议动作","按需求变更、资源、技术阻塞拆分延期原因。")
    c.showPage()
    # Page 3
    page_header(c,"② 项目投入偏差","钱和时间花在哪？偏了多少？",f"工时偏差 {metric_value(items['workhour_deviation_rate'])}")
    cost=[{"label":str(r.get('statMonth',''))[5:7]+'月',"研发实际":n(r.get('rdActualCostMonth')),"研发预算":n(r.get('rdBudgetCostMonth')),"人力实际":n(r.get('staffActualCostMonth')),"人力预算":n(r.get('staffBudgetCostMonth'))} for r in history]
    hour=[{"label":str(r.get('statMonth',''))[5:7]+'月',"实际":n(r.get('actualWorkhourMonth')),"计划":n(r.get('planWorkhourMonth'))} for r in history]
    bar_chart(c,35,155,365,290,cost,"月度成本：实际与预算")
    bar_chart(c,430,155,377,290,hour,"月度工时：实际与计划")
    notes(c,"偏差判断",f"预算偏差率 {metric_value(items['budget_deviation_rate'])}；以产品线看板为本期权威源。","建议动作","确认计划基线是否仍反映当前范围，再拆解新增需求和返工成本。")
    c.showPage()
    # Page 4
    # 计算团队健康度指标
    manhour_common = snap.get("manhour_common", [])
    attendance_records = snap.get("attendance_records", [])
    from collections import defaultdict
    daily_hours = defaultdict(float)
    for r in manhour_common:
        staff = r.get("staffName", "")
        date = r.get("reportDate", "")
        hours = n(r.get("reportManHour"))
        if staff and date:
            daily_hours[(staff, date)] += hours
    overtime_hours = sum(max(0, total - 9) for total in daily_hours.values())
    total_available_hours = sum(n(r.get("attendanceHour")) for r in attendance_records)
    proj_name_pdf = str(proj.get("project_name", ""))
    # 跨项目投入：(参与项目的人员总工时 - 投入到本项目的工时) / 参与项目的人员总工时
    staff_in_proj = set(
        r.get("staffName") for r in manhour_common
        if r.get("projectName") == proj_name_pdf and r.get("staffName")
    )
    total_staff_all_hours = sum(
        n(r.get("reportManHour")) for r in manhour_common
        if r.get("staffName") in staff_in_proj
    )
    proj_hours = sum(
        n(r.get("reportManHour")) for r in manhour_common
        if r.get("projectName") == proj_name_pdf
    )
    if total_staff_all_hours > 0:
        cross_project_ratio = (total_staff_all_hours - proj_hours) / total_staff_all_hours * 100
    else:
        cross_project_ratio = 0
    # 人员总投入：本项目工时明细总和，换算为人月（21.75天×8h=174h/人月）
    total_investment_hours = sum(
        n(r.get("reportManHour")) for r in manhour_common
        if r.get("projectName") == proj_name_pdf
    )
    total_investment = total_investment_hours / 174  # 人月
    # 平均考勤时长：优先使用平台看板数据，回退到自己计算
    avg_attendance = n(proj.get("attendance_hour"))
    if not avg_attendance:
        proj_staff_pdf = set(
            r.get("staffName") for r in manhour_common
            if r.get("projectName") == proj_name_pdf and r.get("staffName")
        )
        proj_att_daily_pdf = defaultdict(float)
        for r in attendance_records:
            staff = r.get("staffName", "")
            date = r.get("workDate", "")
            if staff and date and staff in proj_staff_pdf:
                proj_att_daily_pdf[(staff, date)] += n(r.get("attendanceHour"))
        proj_att_total_pdf = sum(proj_att_daily_pdf.values())
        proj_att_days_pdf = len(proj_att_daily_pdf)
        avg_attendance = proj_att_total_pdf / proj_att_days_pdf if proj_att_days_pdf else 0

    page_header(c,"③ 人员投入健康度","团队是否在透支？",f"总投入 {total_investment:.2f}人月 / 可用工时 {total_available_hours:.1f}h / 加班 {overtime_hours:.1f}h")
    staff = sorted(staff_list, key=lambda r: n(r.get('allocated_hour')), reverse=True)[:10]
    bar_chart(c,35,155,365,290,[{"label":str(r.get('staff_name','成员')),"value":n(r.get('allocated_hour'))} for r in staff],"项目成员投入工时 Top 10")
    bar_chart(c,430,155,377,290,[{"label":"人员总投入(人月)","value":total_investment},{"label":"平均考勤时长(小时)","value":avg_attendance},{"label":"加班工时(小时)","value":overtime_hours},{"label":"跨项目投入(%)","value":cross_project_ratio}],"团队健康度指标")
    notes(c,"已确认口径","实际总工时为所有成员在该项目的投入总和；9 小时仅用于人天换算。","健康度说明","加班 = 每人每天 > 9h 累计；跨项目 = (参与项目的人员总工时 - 投入到本项目的工时) / 参与项目的人员总工时。")
    c.showPage()
    # Page 5
    page_header(c,"④ 产品质量","交付物靠不靠谱？",f"缺陷逃逸率 {metric_value(items['defect_escape_rate'])}")
    close=[{"label":str(r.get('statMonth',''))[5:7]+'月',"value":n(r.get('bugCloseRate'))*100} for r in history if number(r.get('bugCloseRate')) is not None]
    leak=[{"label":str(r.get('statMonth',''))[5:7]+'月',"value":n(r.get('bugLeakRate'))*100} for r in history if number(r.get('bugLeakRate')) is not None]
    bar_chart(c,35,155,365,290,close,"Bug 关闭率趋势",[GREEN]); bar_chart(c,430,155,377,290,leak,"缺陷逃逸率趋势",[BLUE])
    notes(c,"当前质量状态",f"缺陷逃逸率 {metric_value(items['defect_escape_rate'])}；Bug 关闭率 {metric_value(items['bug_close_rate'])}。","建议动作","持续跟踪 Bug 关闭率与缺陷逃逸率趋势。")
    c.showPage()
    # Page 6
    page_header(c,"⑤ Bug 修复效率","问题解决得快不快？",f"已关闭 {closed_bug_count}/{total_bug_count} 个")
    donut(c,35,155,365,290,[("已关闭",closed_bug_count),("未关闭",max(total_bug_count-closed_bug_count,0))],"本期 Bug 关闭情况")
    mttr_val = items["mttr"].value or 0
    bar_chart(c,430,155,377,290,[{"label":"本期 MTTR（小时）","value":mttr_val}],"平均修复时长")
    notes(c,"计算公式",f"排除拒绝/第三方后修复时长 ÷ Bug 总数({total_bug_count}) ÷ 3600 = {mttr_val:.2f} 小时。","后续接入","需补 Bug 严重级别和状态，以计算分级 MTTR 与月度趋势。")
    c.showPage()
    # Page 7
    page_header(c,"⑥ 需求价值交付","做的东西有没有用？",f"需求成本 {metric_value(items['requirement_cost'])}")
    donut(c,35,155,365,290,[("研发工时",n(proj.get('actual_rd_man_hour'))),("测试工时",n(proj.get('actual_test_man_hour')))],"研发与测试工时投入结构")
    bar_chart(c,430,155,377,290,[{"label":"完成任务数","value":n(proj.get('task_finish_count'))},{"label":"实际总工时","value":n(proj.get('actual_total_man_hour'))}],"需求成本的输入数据")
    notes(c,"当前可衡量",f"需求成本 {metric_value(items['requirement_cost'])}，当前以完成任务数代替完成需求数。","待建立价值闭环","接入需求验收、使用、收益或业务目标达成数据。")
    c.save()
