#!/usr/bin/env python3
"""Generate a project-level PMO quick report from the current Excel exports.

This is deliberately a local, file-based MVP.  It keeps metric formulas and
data-availability checks in code so the same calculation layer can later be
called by an internal Skill or replaced with API-based data adapters.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class MetricResult:
    code: str
    name: str
    formula: str
    unit: str
    status: str
    value: Optional[float] = None
    warning_level: str = "unavailable"
    source_fields: tuple[str, ...] = ()
    note: str = ""
    display_value: Optional[str] = None


def number(value: Any) -> Optional[float]:
    """Return a usable numeric value, otherwise None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ratio(numerator: Any, denominator: Any) -> Optional[float]:
    numerator_value, denominator_value = number(numerator), number(denominator)
    if numerator_value is None or denominator_value in (None, 0):
        return None
    return numerator_value / denominator_value


def percent_warning(value: Optional[float], target: str, *, lower_is_better: bool) -> str:
    """Apply the PMO normal / concern / intervention bands where possible."""
    if value is None:
        return "unavailable"
    if target == "sprint":
        return "normal" if value >= 0.85 else "concern"
    if target == "deviation_15":
        absolute = abs(value)
        return "normal" if absolute <= 0.15 else "concern" if absolute <= 0.25 else "intervention"
    if target == "budget_10":
        absolute = abs(value)
        return "normal" if absolute <= 0.10 else "concern" if absolute <= 0.25 else "intervention"
    if target == "bug_leak":
        return "normal" if value <= 0.03 else "concern" if value <= 0.10 else "intervention"
    if target == "bug_close":
        # quality targets 阈值：目标 1.0，预警 0.95
        return "normal" if value >= 0.95 else "concern" if value >= 0.80 else "intervention"
    if target == "req_verify":
        return "normal" if value >= 0.85 else "concern" if value >= 0.70 else "intervention"
    if target == "req_cost":
        return "normal" if value <= 3 else "concern" if value <= 5 else "intervention"
    if target == "mttr_hours":
        # MTTR 越小越好；阈值来自 quality targets API（默认 14 天 = 1209600 秒 → 336 小时）
        return "normal" if value <= 336 else "concern" if value <= 504 else "intervention"
    if target == "info_only":
        # 纯信息展示，无阈值判断
        return "normal"
    return "unconfigured"


def metric(
    code: str,
    name: str,
    formula: str,
    unit: str,
    value: Optional[float],
    source_fields: tuple[str, ...],
    *,
    target: str = "",
    note: str = "",
    status: str = "calculated",
) -> MetricResult:
    return MetricResult(
        code=code,
        name=name,
        formula=formula,
        unit=unit,
        status=status if value is not None else "data_missing",
        value=value,
        warning_level=percent_warning(value, target, lower_is_better=False),
        source_fields=source_fields,
        note=note,
    )


def _calc_mttr(snap: dict) -> Optional[float]:
    """计算 MTTR（平均修复时长，小时）。
    公式：bug 修复总时长 / bug 总数，转换为小时。
    优先使用排除拒绝/第三方后的时长。
    """
    bug = snap.get("bug_detail", {})
    # 优先用排除拒绝/第三方的时长（更准确）
    duration_sec = number(bug.get("bug_resolve_duration_exclude_reject_third"))
    if duration_sec is None:
        duration_sec = number(bug.get("bug_resolve_duration_seconds"))
    total = number(bug.get("bug_total_count"))
    if duration_sec is None or total is None or total == 0:
        return None
    return duration_sec / total / 3600.0  # 秒 → 小时


def load_snapshot(path: Path) -> dict:
    """Load the unified snapshot.json produced by fetch_data."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def render_value(result: MetricResult) -> str:
    if result.display_value:
        return result.display_value
    if result.value is None:
        return "暂无数据"
    if result.unit == "%":
        return f"{result.value:.2%}"
    if result.unit == "人天/项":
        return f"{result.value:.2f}"
    if result.unit == "个":
        return f"{result.value:.0f}"
    return f"{result.value:.2f}"


# 公司总人数（用于研发资源投入占比计算）；公开版必须显式配置。
_COMPANY_HEADCOUNT = int(os.getenv("COMPANY_HEADCOUNT", "0"))


def create_metrics(snap: dict) -> tuple[list[MetricResult], list[dict[str, Any]]]:
    """Calculate 9 PMO metrics from a snapshot dict (snapshot.json)."""
    proj = snap.get("project", {})
    quality_cur = snap.get("quality_current", {})
    req_detail = snap.get("requirement_detail", {})

    actual_hours = number(proj.get("actual_total_man_hour"))
    planned_hours = number(proj.get("plan_total_man_hour"))
    actual_rd_hours = number(proj.get("actual_rd_man_hour"))
    actual_cost_parts = (number(proj.get("actual_dev_cost")), number(proj.get("actual_staff_cost")))
    planned_cost_parts = (number(proj.get("plan_dev_cost")), number(proj.get("plan_staff_cost")))
    actual_cost = sum(actual_cost_parts) if all(v is not None for v in actual_cost_parts) else None
    planned_cost = sum(planned_cost_parts) if all(v is not None for v in planned_cost_parts) else None
    task_finish_count = number(proj.get("task_finish_count"))
    task_count = number(proj.get("task_count"))
    quality_month_hours = number(quality_cur.get("actual_allocated_hour"))

    # 需求验证通过率：优先用质量趋势 API 的 reqVerifyPassRate
    req_verify_rate = number(quality_cur.get("req_verify_rate"))
    # 需求成本分母：优先用双源决策后的总需求数（fetch_data 已合并 API+DB），降级到完成任务数
    resolved_total_req = snap.get("resolved_total_requirement_count")
    if resolved_total_req is not None and resolved_total_req > 0:
        requirement_throughput = resolved_total_req
    else:
        requirement_throughput = task_finish_count

    # 项目参与人员数（用于研发资源投入占比）
    staff_list = snap.get("staff_daily_workhours", [])
    unique_staff = set(r.get("staff_name", "") for r in staff_list if r.get("staff_name"))
    project_staff_count = len(unique_staff)

    conflicts: list[dict[str, Any]] = []
    summary_hours = number(snap.get("workhour_summary", {}).get("reported_hour"))
    if actual_hours is not None and quality_month_hours is not None and abs(actual_hours - quality_month_hours) > 0.01:
        conflicts.append({
            "field": "actual_workhour",
            "project_value": actual_hours,
            "quality_value": quality_month_hours,
            "message": "项目看板与质量度量的本月实际工时不一致；本版本以项目看板作为快报计算源。",
        })
    if actual_hours is not None and summary_hours is not None and abs(actual_hours - summary_hours) > 0.01:
        conflicts.append({
            "field": "actual_workhour",
            "project_value": actual_hours,
            "summary_value": summary_hours,
            "message": "项目看板与工时汇总中的项目工时不一致；需确认统计周期后再作为权威数据源。",
        })

    # 需求成本：用研发工时 / 总需求数
    requirement_cost_person_hour = ratio(actual_rd_hours, requirement_throughput)
    requirement_cost_person_day = ratio(
        actual_rd_hours / 9 if actual_rd_hours is not None else None, requirement_throughput
    )

    metrics = [
        metric(
            "req_verify_rate", "需求验证通过率", "需求验证通过率", "%",
            req_verify_rate, ("req_verify_rate",),
            target="req_verify",
            note="取 quality/metrics 的 reqVerifyPassRate。",
        ),
        metric(
            "sprint_completion_rate", "Sprint完成率", "完成任务数 / 计划任务数", "%",
            ratio(task_finish_count, task_count), ("task_finish_count", "task_count"), target="sprint",
            note="当前以任务数量代替故事点；接入 Jira 后应优先使用故事点。",
        ),
        metric(
            "workhour_deviation_rate", "工时偏差率", "(实际总工时 - 计划总工时) / 计划总工时", "%",
            ratio((actual_hours - planned_hours) if actual_hours is not None and planned_hours is not None else None, planned_hours),
            ("actual_total_man_hour", "plan_total_man_hour"), target="deviation_15",
        ),
        metric(
            "budget_deviation_rate", "预算偏差率",
            "研发成本执行率(当月) - 1", "%",
            (number(quality_cur.get("rd_cost_exec_rate_month")) - 1) if number(quality_cur.get("rd_cost_exec_rate_month")) is not None else None,
            ("rd_cost_exec_rate_month",), target="budget_10",
        ),
        metric(
            "requirement_cost", "需求成本", "研发工时 / 总需求数；人天结果再按 9 小时 / 人天折算", "人天/项",
            requirement_cost_person_day,
            ("actual_rd_man_hour", "db_total_requirement_count"),
            target="req_cost",
            note="分子用研发工时（actual_rd_man_hour）；分母优先取 DB(requirements) 当月总需求数，降级取 API totalReqCount，再降级取完成任务数。",
        ),
        metric(
            "defect_escape_rate", "缺陷逃逸率", "线上发现 Bug 数 / 总 Bug 数", "%",
            number(proj.get("bug_leak_rate")) or number(quality_cur.get("bug_leak_rate")),
            ("bug_leak_rate",), target="bug_leak", status="source_value",
            note="优先取月度看板，回退到质量度量 page；当前只有已计算比率，缺少线上 Bug 数和总 Bug 数，无法复核分子分母。",
        ),
        metric(
            "bug_close_rate", "Bug关闭率", "已关闭 Bug 数 / 总 Bug 数", "%",
            number(quality_cur.get("bug_close_rate")) or number(snap.get("bug_detail", {}).get("bug_close_rate")), ("bug_close_rate",),
            target="bug_close",
            note="quality targets 阈值：目标 1.0，预警 0.95。",
        ),
        metric(
            "mttr", "平均修复时长（MTTR）",
            "bugResolveDuration / bugTotalCount（bug 修复总时长 / bug 总数）",
            "小时",
            _calc_mttr(snap),
            ("bug_resolve_duration_exclude_reject_third", "bug_total_count"),
            target="mttr_hours",
            note="quality targets 阈值 1209600 秒 = 14 天；分母为 bug 总数（含未关闭）。",
        ),
        metric(
            "rd_resource_input_ratio", "研发资源投入占比",
            f"申报项目工时的人员数({project_staff_count}) / 公司总人数({_COMPANY_HEADCOUNT})", "%",
            ratio(project_staff_count, _COMPANY_HEADCOUNT) if project_staff_count and _COMPANY_HEADCOUNT > 0 else None,
            ("staff_daily_workhours",),
            target="info_only",
            note=(f"研发人力 = 申报了本项目工时的人员总数（{project_staff_count}人）；"
                  f"公司总人数 = {_COMPANY_HEADCOUNT or '未配置'}。"),
        ),
    ]
    for item in metrics:
        if item.code == "requirement_cost" and requirement_cost_person_hour is not None and requirement_cost_person_day is not None:
            item.display_value = f"{requirement_cost_person_hour:.2f} 人时/项（{requirement_cost_person_day:.2f} 人天/项）"
    return metrics, conflicts


def write_markdown(report: dict[str, Any], output_path: Path) -> None:
    lines = [
        f"# 项目快报：{report['project']['project_name']}",
        "",
        f"- 项目 ID：{report['project']['project_id']}",
        f"- 统计月份：{report['project']['settlement_month']}",
        f"- 生成时间：{report['generated_at']}",
        "",
        "| 指标 | 结果 | 预警 | 数据状态 | 公式 |",
        "|---|---:|---|---|---|",
    ]
    for item in report["metrics"]:
        result = MetricResult(**item)
        lines.append(
            f"| {result.name} | {render_value(result)} | {result.warning_level} | {result.status} | {result.formula} |"
        )
    if report["data_conflicts"]:
        lines.extend(["", "## 数据口径待确认"])
        lines.extend(f"- {item['message']}" for item in report["data_conflicts"])
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="按项目和月份生成 PMO 项目快报数据")
    parser.add_argument("--snapshot", type=Path, default=Path("snapshot.json"),
                        help="snapshot.json 文件路径")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()

    snap = load_snapshot(args.snapshot)
    metrics, conflicts = create_metrics(snap)

    proj = snap.get("project", {})
    report = {
        "schema_version": "0.2.0",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "project": {
            "project_id": proj.get("project_id", ""),
            "project_name": proj.get("project_name", ""),
            "settlement_month": proj.get("stat_month", ""),
        },
        "metrics": [asdict(item) for item in metrics],
        "data_conflicts": conflicts,
        "source_policy": "统一 snapshot.json（API 数据）作为唯一数据源。",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{proj.get('project_id', 'unknown')}_{proj.get('stat_month', '')}_项目快报"
    json_path = args.output_dir / f"{stem}.json"
    markdown_path = args.output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, markdown_path)
    print(f"已生成：{json_path}")
    print(f"已生成：{markdown_path}")


if __name__ == "__main__":
    main()
