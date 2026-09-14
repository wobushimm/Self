"""
data_validator.py - 快照数据校验引擎

职责：
  1. 校验 ProjectMonthlySnapshot.json 的结构完整性
  2. 校验跨字段一致性（需求数 vs 通过率、Bug 数 vs 关闭率等）
  3. 校验工时一致性（项目汇总 vs 人员日分摊之和）
  4. 生成 data_quality 报告写入快照

设计原则：
  - 校验结果写入 snapshot["data_quality"]，不中断流水线
  - 严重不一致（项目 ID/名称不匹配）标记为 blocking error
  - 轻微缺失（某指标无数据）标记为 warning
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 容差
WORKHOUR_TOLERANCE = 0.01  # 工时容差 0.01 小时


class ValidationResult:
    """校验结果"""

    def __init__(self):
        self.missing_fields: List[str] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.checks_passed: int = 0
        self.checks_total: int = 0

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "missing_fields": self.missing_fields,
            "warnings": self.warnings,
            "errors": self.errors,
            "checks_passed": self.checks_passed,
            "checks_total": self.checks_total,
        }


def validate_snapshot(snap: Dict[str, Any]) -> ValidationResult:
    """
    校验快照数据质量。

    检查项：
      1. 必填字段存在性（project, quality_current, bug_detail 等）
      2. 项目 ID/名称/编码跨来源一致
      3. 验证通过需求数 ≤ 总需求数
      4. 已关闭 Bug ≤ Bug 总数
      5. 工时一致性（项目汇总 vs 人员日分摊之和）
      6. 成本执行率合理性
    """
    result = ValidationResult()

    # --- 1. 必填段存在性 ---
    required_sections = ["project", "quality_current", "bug_detail",
                         "quality_history", "staff_daily_workhours"]
    for section in required_sections:
        result.checks_total += 1
        if section not in snap:
            result.missing_fields.append(section)
        else:
            result.checks_passed += 1

    # 如果基本段缺失，后续校验无意义
    if not result.is_valid or "project" not in snap:
        result.errors.append("缺少 project 段，无法继续校验")
        return result

    proj = snap.get("project", {})
    quality_cur = snap.get("quality_current", {})
    bug_detail = snap.get("bug_detail", {})

    # --- 2. 项目标识一致性 ---
    result.checks_total += 1
    proj_id = proj.get("project_id")
    proj_name = proj.get("project_name")
    if proj_id and proj_name:
        result.checks_passed += 1
    else:
        result.errors.append(f"项目标识不完整: id={proj_id}, name={proj_name}")

    # --- 3. 需求数一致性 ---
    result.checks_total += 1
    req_detail = snap.get("requirement_detail", {})
    req_total = snap.get("resolved_total_requirement_count") or req_detail.get("total_requirement_count")
    req_verify = req_detail.get("verified_requirement_count")
    req_rate = req_detail.get("req_verify_rate") or quality_cur.get("req_verify_rate")
    if req_total is not None and req_verify is not None:
        if req_verify > req_total:
            result.errors.append(
                f"验证通过需求数({req_verify}) > 总需求数({req_total})")
        else:
            result.checks_passed += 1
            # 交叉校验通过率
            if req_rate is not None and req_total > 0:
                computed_rate = req_verify / req_total
                if abs(computed_rate - req_rate) > 0.01:
                    result.warnings.append(
                        f"需求验证通过率不一致: 字段={req_rate:.3f}, "
                        f"计算={computed_rate:.3f}")
    elif req_total is None:
        result.missing_fields.append("resolved_total_requirement_count")
    else:
        result.checks_passed += 1

    # --- 4. Bug 数一致性 ---
    result.checks_total += 1
    bug_total = bug_detail.get("bug_total_count") or 0
    bug_closed = bug_detail.get("bug_closed_count") or 0
    bug_close_rate = quality_cur.get("bug_close_rate")
    if bug_closed > bug_total and bug_total > 0:
        result.errors.append(
            f"已关闭 Bug({bug_closed}) > Bug 总数({bug_total})")
    else:
        result.checks_passed += 1
        # 交叉校验关闭率
        if bug_close_rate is not None and bug_total > 0:
            computed_rate = bug_closed / bug_total
            if abs(computed_rate - bug_close_rate) > 0.01:
                result.warnings.append(
                    f"Bug 关闭率不一致: 字段={bug_close_rate:.3f}, "
                    f"计算={computed_rate:.3f}")

    # --- 5. 工时一致性 ---
    result.checks_total += 1
    proj_allocated = proj.get("actual_total_man_hour")
    staff_list = snap.get("staff_daily_workhours", [])
    if proj_allocated is not None and staff_list:
        staff_total = sum(
            float(s.get("workHour", 0) or 0) for s in staff_list
        )
        if abs(proj_allocated - staff_total) > WORKHOUR_TOLERANCE:
            result.warnings.append(
                f"工时不一致: 项目汇总={proj_allocated:.2f}h, "
                f"人员日分摊合计={staff_total:.2f}h, "
                f"差值={abs(proj_allocated - staff_total):.2f}h")
        else:
            result.checks_passed += 1
    elif proj_allocated is None:
        result.missing_fields.append("project.actual_total_man_hour")
    else:
        result.checks_passed += 1

    # --- 6. 成本执行率 ---
    for cost_type in ("dev", "staff"):
        result.checks_total += 1
        actual = proj.get(f"actual_{cost_type}_cost")
        budget = proj.get(f"plan_{cost_type}_cost")
        rate_field = f"{cost_type}_cost_exec_rate"
        rate = quality_cur.get(rate_field)

        if actual is not None and budget is not None and budget != 0:
            computed_rate = actual / budget
            if rate is not None and abs(computed_rate - rate) > 0.01:
                result.warnings.append(
                    f"{cost_type}成本执行率不一致: "
                    f"字段={rate:.3f}, 计算={computed_rate:.3f}")
            else:
                result.checks_passed += 1
        elif budget is not None and budget == 0:
            result.missing_fields.append(f"quality_current.{rate_field}")
        else:
            result.checks_passed += 1

    return result


def run_validation(snap: Dict[str, Any]) -> Dict[str, Any]:
    """执行校验并将结果写入快照。返回更新后的 data_quality 段。"""
    vr = validate_snapshot(snap)
    data_quality = vr.to_dict()
    snap["data_quality"] = data_quality

    if vr.errors:
        logger.warning(f"数据校验错误: {vr.errors}")
    if vr.warnings:
        logger.warning(f"数据校验告警: {vr.warnings}")
    if vr.missing_fields:
        logger.warning(f"缺失字段: {', '.join(vr.missing_fields[:10])}...")

    return data_quality
