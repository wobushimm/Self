"""数据校验报告生成器。

每次执行流水线时自动生成独立的 HTML 校验报告，包含：
- 校验总览（通过/警告/错误数量）
- 每项检查的详细结果
- 缺失字段列表
- 数据源一致性比对
- 9 项指标的数据来源和计算状态
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from export_modules.report_runner import safe_filename


def _import_validator():
    """延迟导入数据校验器，避免模块级导入失败导致整个校验报告模块不可用。"""
    from report_modules.data_validator import validate_snapshot, ValidationResult
    return validate_snapshot, ValidationResult


# ── 校验项定义 ──────────────────────────────────────────

_CHECK_LABELS = {
    "required_sections": "必填数据段存在性",
    "project_identity": "项目标识一致性",
    "req_count": "需求数一致性",
    "bug_count": "Bug 数一致性",
    "workhour": "工时一致性",
    "dev_cost": "研发成本执行率",
    "staff_cost": "人力成本执行率",
}


def _status_tag(level: str) -> str:
    """返回带颜色的状态标签 HTML。"""
    colors = {
        "pass": "#16a34a",
        "warning": "#ca8a04",
        "error": "#dc2626",
        "missing": "#6b7280",
    }
    labels = {
        "pass": "通过",
        "warning": "警告",
        "error": "错误",
        "missing": "缺失",
    }
    color = colors.get(level, "#6b7280")
    label = labels.get(level, level)
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;' \
           f'background:{color};color:#fff;font-size:12px">{label}</span>'


def _build_overview(vr: ValidationResult) -> str:
    """构建校验总览卡片。"""
    total = vr.checks_total or 1
    passed = vr.checks_passed
    warnings = len(vr.warnings)
    errors = len(vr.errors)
    missing = len(vr.missing_fields)
    rate = passed / total * 100

    return f'''<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0">
  <div style="background:#f0fdf4;border-radius:8px;padding:16px;text-align:center">
    <div style="font-size:28px;font-weight:700;color:#16a34a">{passed}</div>
    <div style="font-size:13px;color:#4b5563">检查通过</div>
  </div>
  <div style="background:#fefce8;border-radius:8px;padding:16px;text-align:center">
    <div style="font-size:28px;font-weight:700;color:#ca8a04">{warnings}</div>
    <div style="font-size:13px;color:#4b5563">警告</div>
  </div>
  <div style="background:#fef2f2;border-radius:8px;padding:16px;text-align:center">
    <div style="font-size:28px;font-weight:700;color:#dc2626">{errors}</div>
    <div style="font-size:13px;color:#4b5563">错误</div>
  </div>
  <div style="background:#f3f4f6;border-radius:8px;padding:16px;text-align:center">
    <div style="font-size:28px;font-weight:700;color:#6b7280">{missing}</div>
    <div style="font-size:13px;color:#4b5563">缺失字段</div>
  </div>
</div>
<div style="margin-bottom:24px;padding:12px 16px;background:#f9fafb;border-radius:8px;border-left:4px solid {"#16a34a" if not errors else "#dc2626"}">
  <strong>校验通过率</strong>：{rate:.0f}%（{passed}/{total} 项通过）
  {"&nbsp;&nbsp;|&nbsp;&nbsp;<strong style='color:#dc2626'>存在校验错误，请核实数据后重新生成</strong>" if errors else ""}
</div>'''


def _build_check_details(vr: ValidationResult) -> str:
    """构建每项检查的详细结果表格。"""
    rows = []

    # 从 errors/warnings/missing_fields 反推每项状态
    error_fields = set()
    warning_fields = set()
    for msg in vr.errors:
        for key in _CHECK_LABELS:
            if key.replace("_", " ") in msg.lower() or key in msg:
                error_fields.add(key)
    for msg in vr.warnings:
        for key in _CHECK_LABELS:
            if key.replace("_", " ") in msg.lower() or key in msg:
                warning_fields.add(key)

    # 映射检查项到具体描述
    checks = [
        ("required_sections", "必填数据段存在性",
         "project / quality_current / bug_detail / quality_history / staff_daily_workhours"),
        ("project_identity", "项目标识一致性",
         "项目 ID、名称、编码跨来源一致"),
        ("req_count", "需求数一致性",
         "验证通过需求数 <= 总需求数，且通过率一致"),
        ("bug_count", "Bug 数一致性",
         "已关闭 Bug <= Bug 总数，且关闭率一致"),
        ("workhour", "工时一致性",
         "项目汇总工时 vs 人员日分摊之和（容差 0.01h）"),
        ("dev_cost", "研发成本执行率",
         "实际/预算 vs 质量度量字段一致"),
        ("staff_cost", "人力成本执行率",
         "实际/预算 vs 质量度量字段一致"),
    ]

    for code, name, desc in checks:
        if code in error_fields:
            status = _status_tag("error")
            detail = next((m for m in vr.errors if code.replace("_", " ") in m.lower() or code in m), "校验失败")
        elif code in warning_fields:
            status = _status_tag("warning")
            detail = next((m for m in vr.warnings if code.replace("_", " ") in m.lower() or code in m), "存在偏差")
        else:
            status = _status_tag("pass")
            detail = "数据一致"
        rows.append(f'<tr><td>{html.escape(name)}</td><td style="text-align:center">{status}</td>'
                    f'<td>{html.escape(detail)}</td><td style="font-size:12px;color:#6b7280">{html.escape(desc)}</td></tr>')

    return f'''<h2 style="margin-top:32px;border-bottom:2px solid #e5e7eb;padding-bottom:8px">检查项明细</h2>
<table style="width:100%;border-collapse:collapse;margin-top:12px">
  <thead><tr style="background:#f3f4f6">
    <th style="padding:8px 12px;text-align:left">检查项</th>
    <th style="padding:8px 12px;text-align:center;width:80px">状态</th>
    <th style="padding:8px 12px;text-align:left">详情</th>
    <th style="padding:8px 12px;text-align:left;font-size:12px">校验规则</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>'''


def _build_missing_fields(vr: ValidationResult) -> str:
    """构建缺失字段列表。"""
    if not vr.missing_fields:
        return '<div style="margin:16px 0;padding:12px;background:#f0fdf4;border-radius:8px;color:#16a34a">无缺失字段</div>'

    items = "".join(f"<li><code>{html.escape(f)}</code></li>" for f in vr.missing_fields)
    return f'''<h2 style="margin-top:32px;border-bottom:2px solid #e5e7eb;padding-bottom:8px">缺失字段</h2>
<ul style="margin:12px 0;padding-left:24px">{items}</ul>'''


def _build_metrics_status(snap: dict) -> str:
    """构建 9 项指标的数据来源和计算状态。"""
    from data_modules.metrics_engine import create_metrics, number

    metrics, conflicts = create_metrics(snap)
    proj = snap.get("project", {})
    quality_cur = snap.get("quality_current", {})
    bug_detail = snap.get("bug_detail", {})

    rows = []
    for m in metrics:
        value_display = m.display_value or ("暂无数据" if m.value is None else f"{m.value:.4f}")
        status_color = "#16a34a" if m.value is not None else "#6b7280"
        status_text = "有数据" if m.value is not None else "待补"

        # 确定数据来源
        sources = []
        if m.code == "sprint_completion_rate":
            sources = ["产品线看板 (task_count, task_finish_count)"]
        elif m.code in ("workhour_deviation_rate",):
            sources = ["产品线看板 (actual_total_man_hour, plan_total_man_hour)"]
        elif m.code == "budget_deviation_rate":
            sources = ["产品线看板 (actual_dev_cost, plan_dev_cost, actual_staff_cost, plan_staff_cost)"]
        elif m.code == "defect_escape_rate":
            s = "看板" if proj.get("bug_leak_rate") is not None else "质量度量"
            sources = [f"产品线看板/质量度量 (bug_leak_rate) ← {s}"]
        elif m.code == "bug_close_rate":
            sources = ["质量度量 (bug_close_rate)"]
        elif m.code == "req_verify_rate":
            sources = ["质量度量 page (req_verify_rate)"]
        elif m.code == "mttr":
            sources = ["质量度量 page (bug_resolve_duration_exclude_reject_third / bug_total_count)"]
        elif m.code == "requirement_cost":
            sources = ["看板 (actual_rd_man_hour) + DB(requirements) 总需求数"]
        elif m.code == "rd_resource_input_ratio":
            sources = ["人员工时 API (staff_daily_workhours) / 配置的组织总人数"]

        rows.append(
            f'<tr>'
            f'<td>{html.escape(m.name)}</td>'
            f'<td style="text-align:right;color:{status_color};font-weight:600">{html.escape(value_display)}</td>'
            f'<td style="text-align:center"><span style="color:{status_color}">{status_text}</span></td>'
            f'<td style="font-size:12px">{html.escape(m.warning_level)}</td>'
            f'<td style="font-size:11px;color:#6b7280">{"".join(f"<div>{html.escape(s)}</div>" for s in sources)}</td>'
            f'</tr>'
        )

    return f'''<h2 style="margin-top:32px;border-bottom:2px solid #e5e7eb;padding-bottom:8px">9 项指标状态</h2>
<table style="width:100%;border-collapse:collapse;margin-top:12px">
  <thead><tr style="background:#f3f4f6">
    <th style="padding:8px 12px;text-align:left">指标</th>
    <th style="padding:8px 12px;text-align:right">当前值</th>
    <th style="padding:8px 12px;text-align:center;width:70px">状态</th>
    <th style="padding:8px 12px;text-align:center;width:70px">预警</th>
    <th style="padding:8px 12px;text-align:left;font-size:12px">数据来源</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>'''


def _build_conflicts(snap: dict) -> str:
    """构建数据口径冲突说明。"""
    from data_modules.metrics_engine import create_metrics
    _, conflicts = create_metrics(snap)

    if not conflicts:
        return '<div style="margin:16px 0;padding:12px;background:#f0fdf4;border-radius:8px;color:#16a34a">无数据口径冲突</div>'

    rows = "".join(
        f'<tr><td><code>{html.escape(c["field"])}</code></td>'
        f'<td>{html.escape(c.get("message", ""))}</td></tr>'
        for c in conflicts
    )
    return f'''<h2 style="margin-top:32px;border-bottom:2px solid #e5e7eb;padding-bottom:8px">数据口径冲突</h2>
<table style="width:100%;border-collapse:collapse;margin-top:12px">
  <thead><tr style="background:#fef2f2">
    <th style="padding:8px 12px;text-align:left">字段</th>
    <th style="padding:8px 12px;text-align:left">说明</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>'''


# ── 主入口 ──────────────────────────────────────────────

def generate_validation_report(snap: dict, output_dir: Path) -> Path:
    """生成数据校验报告 HTML。

    Args:
        snap: 统一数据快照 (ProjectMonthlySnapshot)
        output_dir: 输出目录

    Returns:
        生成的 HTML 文件路径
    """
    validate_snapshot, ValidationResult = _import_validator()
    vr = validate_snapshot(snap)

    proj = snap.get("project", {})
    project_name = proj.get("project_name", "unknown")
    month = proj.get("stat_month", "")
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    body = f'''{_build_overview(vr)}
{_build_check_details(vr)}
{_build_missing_fields(vr)}
{_build_metrics_status(snap)}
{_build_conflicts(snap)}'''

    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据校验报告 - {html.escape(project_name)} {html.escape(month)}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0; padding: 24px; background: #fff; color: #1f2937; }}
  .header {{ background: linear-gradient(135deg, #1e40af, #3b82f6); color: #fff; padding: 24px 32px; border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ margin: 0 0 8px; font-size: 22px; }}
  .header p {{ margin: 0; opacity: 0.85; font-size: 14px; }}
  code {{ background: #f3f4f6; padding: 1px 6px; border-radius: 4px; font-size: 13px; }}
  table {{ font-size: 14px; }}
  tbody tr {{ border-bottom: 1px solid #e5e7eb; }}
  tbody tr:hover {{ background: #f9fafb; }}
</style></head><body>
<div class="header">
  <h1>数据校验报告</h1>
  <p>项目：{html.escape(project_name)} &nbsp;|&nbsp; 月份：{html.escape(month)} &nbsp;|&nbsp; 生成时间：{now}</p>
</div>
{body}
<div style="margin-top:40px;padding:16px;background:#f9fafb;border-radius:8px;font-size:12px;color:#6b7280">
  <strong>说明</strong>：本报告由项目管理快报 Skill 自动生成，基于 snapshot.json 的 7 项数据校验规则。
  "通过"表示数据一致且完整；"警告"表示存在轻微偏差但不影响指标计算；"错误"表示数据矛盾，需核实后再使用。
</div>
</body></html>'''

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{safe_filename(project_name)}_{month}_数据校验报告.html"
    report_path.write_text(page, encoding="utf-8")
    return report_path
