"""③ 人员投入健康度 — 团队是否在透支？"""

from __future__ import annotations

from typing import Any
from collections import defaultdict

from page_modules.base_module import BasePageModule


def _num(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class P03StaffHealth(BasePageModule):
    PAGE_ID = "p03_staff_health"
    TITLE = "③ 人员投入健康度"
    CORE_QUESTION = "团队是否在透支？"
    DOMAIN_INDEX = 3

    def fetch_data(self, snap: dict) -> dict[str, Any]:
        staff_list = snap.get("staff_daily_workhours", [])
        staff_sorted = sorted(staff_list, key=lambda r: _num(r.get("allocated_hour")), reverse=True)[:10]

        # 计算团队健康度指标
        manhour_common = snap.get("manhour_common", [])
        attendance_records = snap.get("attendance_records", [])

        # 1. 成员投入明细覆盖率（有工时明细数据 = 100%）
        member_coverage = 100 if staff_list else 0

        # 2. 加班工时：从工时明细计算，每人每天 > 9h 的部分算加班
        overtime_hours = 0.0
        daily_hours = defaultdict(float)  # (staffName, reportDate) -> total hours
        for r in manhour_common:
            staff = r.get("staffName", "")
            date = r.get("reportDate", "")
            hours = _num(r.get("reportManHour"))
            if staff and date:
                daily_hours[(staff, date)] += hours
        for (staff, date), total in daily_hours.items():
            if total > 9:
                overtime_hours += total - 9

        # 3. 总可用工时：从考勤数据汇总
        total_available_hours = sum(_num(r.get("attendanceHour")) for r in attendance_records)

        # 3b. 人员总投入：本项目工时明细总和，换算为人月（21.75天×8h=174h/人月）
        proj_name = snap.get("project", {}).get("project_name", "")
        total_investment_hours = sum(
            _num(r.get("reportManHour")) for r in manhour_common
            if r.get("projectName") == proj_name
        )
        total_investment_pm = total_investment_hours / 174  # 人月

        # 3c. 平均考勤时长：优先使用平台看板数据，回退到自己计算
        avg_attendance = _num(snap.get("project", {}).get("attendance_hour"))
        if not avg_attendance:
            proj_staff = set(
                r.get("staffName") for r in manhour_common
                if r.get("projectName") == proj_name and r.get("staffName")
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

        # 4. 跨项目投入：(参与项目的人员总工时 - 投入到本项目的工时) / 参与项目的人员总工时
        staff_in_proj = set(
            r.get("staffName") for r in manhour_common
            if r.get("projectName") == proj_name and r.get("staffName")
        )
        total_staff_all_hours = sum(
            _num(r.get("reportManHour")) for r in manhour_common
            if r.get("staffName") in staff_in_proj
        )
        proj_hours = total_investment_hours  # 已在 3b 计算：本项目工时总和
        if total_staff_all_hours > 0:
            cross_project_ratio = (total_staff_all_hours - proj_hours) / total_staff_all_hours * 100
        else:
            cross_project_ratio = 0

        return {
            "staff_top": [
                {"label": r.get("staff_name", "项目成员"), "value": _num(r.get("allocated_hour"))}
                for r in staff_sorted
            ],
            "member_count": snap.get("workhour_summary", {}).get("member_count"),
            "health_metrics": {
                "member_coverage": member_coverage,
                "overtime_hours": round(overtime_hours, 1),
                "total_available_hours": round(total_available_hours, 1),
                "cross_project_ratio": round(cross_project_ratio, 1),
                "total_investment": round(total_investment_pm, 2),
                "avg_attendance": round(avg_attendance, 1),
            },
        }

    def build_context(self, data: dict[str, Any], metrics: dict) -> dict[str, Any]:
        staff_top = data["staff_top"]
        health = data.get("health_metrics", {})

        # 构建健康度数据覆盖图表
        health_chart_data = [
            {"label": "人员总投入(人月)", "value": health.get("total_investment", 0)},
            {"label": "平均考勤时长(小时)", "value": health.get("avg_attendance", 0)},
            {"label": "加班工时(小时)", "value": health.get("overtime_hours", 0)},
            {"label": "跨项目投入(%)", "value": health.get("cross_project_ratio", 0)},
        ]

        overtime = health.get("overtime_hours", 0)
        total_avail = health.get("total_available_hours", 0)
        cross_ratio = health.get("cross_project_ratio", 0)

        return {
            "charts": [
                {"id": "staffChart", "title": "项目成员投入工时（Top 10）", "type": "barh", "data": staff_top},
                {"id": "healthChart", "title": "团队健康度指标", "type": "bar", "data": health_chart_data},
            ],
            "conclusion": f"本月总可用工时 {total_avail:.1f} 小时，加班 {overtime:.1f} 小时；"
                          f"跨项目投入占比 {cross_ratio:.1f}%。",
            "action": "加班超标建议调整计划；跨项目投入过高需关注上下文切换成本。",
            "decision_brief": f"可用工时 {total_avail:.1f}h / 加班 {overtime:.1f}h / 跨项目 {cross_ratio:.1f}%。",
        }
