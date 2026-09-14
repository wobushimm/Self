#!/usr/bin/env python3
"""指标深度分析引擎 —— 差距分析、趋势判断、优先级排序、建议生成、健康评分。

分析结果放在 PMO 预警清单上方，为项目例会提供可操作的决策依据。
"""

from __future__ import annotations

from typing import Any, Optional
from dataclasses import dataclass, field


# ── 目标值解析（用于差距计算）──────────────────────────

def _parse_target_pct(target_str: str) -> Optional[float]:
    """将 TARGETS 字符串解析为数值（百分比类 → 小数，人天类 → 原值）。
    返回 None 表示无法解析（如 '研发人力/组织总人数'、'P0≤4h P1≤24h'）。
    """
    if not target_str:
        return None
    s = target_str.strip()
    # ≥85% → 0.85
    if s.startswith("≥") and s.endswith("%"):
        return float(s[1:-1]) / 100
    # ≤3% → 0.03
    if s.startswith("≤") and s.endswith("%"):
        return float(s[1:-1]) / 100
    # ±15% → 0.15
    if s.startswith("±") and s.endswith("%"):
        return float(s[1:-1]) / 100
    # ≤3人天 → 3.0
    if "人天" in s:
        for prefix in ("≤", "≥", "±"):
            if s.startswith(prefix):
                try:
                    return float(s[len(prefix):].replace("人天", ""))
                except ValueError:
                    return None
    return None


# ── 单指标差距分析 ────────────────────────────────────

@dataclass
class GapItem:
    code: str
    name: str
    current: Optional[float]
    target_str: str
    target_val: Optional[float]
    direction: str          # "higher_better" / "lower_better" / "abs_better"
    gap: Optional[float]    # 正数=达标/有余量，负数=未达标
    gap_pct: Optional[float]  # 偏离百分比（相对目标）
    is_met: bool            # 是否达标
    status: str             # "normal" / "concern" / "intervention" / "unavailable"
    display_current: str    # 格式化后的当前值


def _gap_direction(code: str) -> str:
    """根据指标代码判断目标方向。"""
    if code in ("req_verify_rate", "sprint_completion_rate", "bug_close_rate"):
        return "higher_better"
    if code in ("defect_escape_rate", "workhour_deviation_rate",
                "budget_deviation_rate", "requirement_cost", "mttr"):
        return "lower_better"
    return "abs_better"


def _format_current(metric) -> str:
    """复用 MetricResult 的 display_value 或格式化。"""
    if metric.display_value:
        return metric.display_value
    if metric.value is None:
        return "待接入"
    if metric.unit == "%":
        return f"{metric.value:.1%}"
    if metric.unit == "个":
        return f"{metric.value:.0f}"
    if metric.unit == "小时":
        return f"{metric.value:.1f} 小时"
    if metric.unit == "人天/项":
        return f"{metric.value:.2f} 人天"
    return f"{metric.value:.2f}"


def analyze_gap(metrics: list, targets: dict[str, str]) -> list[GapItem]:
    """对每项指标计算与目标的差距。"""
    results = []
    for m in metrics:
        target_str = targets.get(m.code, "")
        target_val = _parse_target_pct(target_str)
        direction = _gap_direction(m.code)
        gap = None
        gap_pct = None
        is_met = False

        if m.value is None:
            results.append(GapItem(
                code=m.code, name=m.name, current=None,
                target_str=target_str, target_val=target_val,
                direction=direction, gap=None, gap_pct=None,
                is_met=False, status="unavailable",
                display_current="待接入",
            ))
            continue

        if target_val is not None:
            if direction == "higher_better":
                gap = m.value - target_val
                gap_pct = (gap / target_val * 100) if target_val != 0 else None
                is_met = m.value >= target_val
            elif direction == "lower_better":
                gap = target_val - m.value
                gap_pct = (gap / target_val * 100) if target_val != 0 else None
                is_met = m.value <= target_val
            else:  # abs_better（如工时偏差率、预算偏差率）
                abs_val = abs(m.value)
                gap = target_val - abs_val
                gap_pct = (gap / target_val * 100) if target_val != 0 else None
                is_met = abs_val <= target_val

        results.append(GapItem(
            code=m.code, name=m.name, current=m.value,
            target_str=target_str, target_val=target_val,
            direction=direction, gap=gap, gap_pct=gap_pct,
            is_met=is_met, status=m.warning_level,
            display_current=_format_current(m),
        ))
    return results


# ── 趋势判断 ──────────────────────────────────────────

@dataclass
class TrendItem:
    code: str
    name: str
    history: list[float]     # 按月份排序的历史值
    labels: list[str]        # 月份标签
    direction: str           # "up" / "down" / "flat"
    is_improving: bool       # 趋势是否在改善
    verdict: str             # "↗ 趋好" / "↘ 趋差" / "→ 持平" / "数据不足"


def analyze_trend(metrics: list, history: list[dict], targets: dict[str, str]) -> list[TrendItem]:
    """利用历史数据判断每项指标的趋势方向。"""
    # 指标 → 历史字段映射
    FIELD_MAP = {
        "req_verify_rate": "req_verify_rate",
        "defect_escape_rate": "bug_leak_rate",
        "bug_close_rate": "bug_close_rate",
    }
    # 需要从 history 中提取的数值型指标（百分比类）
    TREND_CODES = {"req_verify_rate", "defect_escape_rate", "bug_close_rate",
                   "workhour_deviation_rate", "budget_deviation_rate"}

    results = []
    for m in metrics:
        if m.code not in TREND_CODES:
            continue
        direction_key = _gap_direction(m.code)

        hist_field = FIELD_MAP.get(m.code)
        if hist_field:
            vals = []
            labels = []
            for row in history:
                v = row.get(hist_field)
                if v is not None:
                    try:
                        vals.append(float(v))
                        month_str = str(row.get("settlement_month") or row.get("stat_month", ""))
                        labels.append(month_str[5:7] + "月" if len(month_str) >= 7 else month_str)
                    except (TypeError, ValueError):
                        pass
        else:
            # 工时/预算偏差率需要从 history 手动计算
            vals = []
            labels = []
            for row in history:
                actual = _safe_float(row.get("actual_workhour_month"))
                plan = _safe_float(row.get("plan_total_man_hour") or row.get("estimated_workhour_month"))
                if actual is not None and plan and plan > 0:
                    vals.append((actual - plan) / plan)
                    month_str = str(row.get("settlement_month") or row.get("stat_month", ""))
                    labels.append(month_str[5:7] + "月" if len(month_str) >= 7 else month_str)

        if len(vals) < 2:
            results.append(TrendItem(
                code=m.code, name=m.name, history=vals, labels=labels,
                direction="flat", is_improving=False, verdict="数据不足",
            ))
            continue

        # 简单线性趋势：比较首尾
        delta = vals[-1] - vals[0]
        threshold = abs(vals[0]) * 0.05 if vals[0] != 0 else 0.01

        if abs(delta) <= threshold:
            trend_dir = "flat"
        elif delta > 0:
            trend_dir = "up"
        else:
            trend_dir = "down"

        # 判断是否在改善
        if direction_key == "higher_better":
            is_improving = trend_dir == "up"
        elif direction_key == "lower_better":
            is_improving = trend_dir == "down"
        else:  # abs_better
            is_improving = abs(vals[-1]) < abs(vals[0])

        if trend_dir == "flat":
            verdict = "→ 持平"
        elif is_improving:
            verdict = "↗ 趋好"
        else:
            verdict = "↘ 趋差"

        results.append(TrendItem(
            code=m.code, name=m.name, history=vals, labels=labels,
            direction=trend_dir, is_improving=is_improving, verdict=verdict,
        ))
    return results


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 优先级排序 ─────────────────────────────────────────

@dataclass
class PriorityItem:
    rank: int
    code: str
    name: str
    status: str
    gap_pct: Optional[float]
    trend_verdict: str
    action_urgency: str   # "立即处理" / "重点关注" / "持续观察"
    reason: str


def prioritize(gap_items: list[GapItem], trend_items: list[TrendItem]) -> list[PriorityItem]:
    """按严重程度排序，最需要关注的排最前。"""
    trend_map = {t.code: t for t in trend_items}

    # 排序权重：intervention=3, concern=2, unavailable=1, normal=0
    STATUS_WEIGHT = {"intervention": 3, "concern": 2, "unavailable": 1, "normal": 0}

    scored = []
    for g in gap_items:
        status_w = STATUS_WEIGHT.get(g.status, 0)
        # 偏离越大越紧急（gap_pct 负数表示未达标，取绝对值）
        gap_w = abs(g.gap_pct) if g.gap_pct is not None and g.gap_pct < 0 else 0
        # 趋势恶化额外加权
        t = trend_map.get(g.code)
        trend_w = 1.5 if (t and not t.is_improving and t.verdict != "数据不足") else 0
        total_score = status_w * 10 + gap_w + trend_w
        scored.append((total_score, g))

    scored.sort(key=lambda x: -x[0])

    results = []
    for rank, (score, g) in enumerate(scored, 1):
        t = trend_map.get(g.code)
        trend_verdict = t.verdict if t else "—"

        if g.status == "intervention":
            urgency = "立即处理"
        elif g.status == "concern":
            urgency = "重点关注"
        elif g.status == "unavailable":
            urgency = "数据补齐"
        else:
            urgency = "持续观察"

        # 生成原因说明
        parts = []
        if g.status in ("intervention", "concern"):
            if g.gap_pct is not None:
                if g.gap_pct < 0:
                    parts.append(f"偏离目标 {abs(g.gap_pct):.0f}%")
                else:
                    parts.append(f"余量 {g.gap_pct:.0f}%")
            if t and not t.is_improving and t.verdict != "数据不足":
                parts.append(f"趋势{t.verdict}")
        elif g.status == "unavailable":
            parts.append("数据待接入")
        else:
            if g.gap_pct is not None:
                parts.append(f"余量 {g.gap_pct:.0f}%")
            if t:
                parts.append(f"趋势{t.verdict}")
        reason = "；".join(parts) if parts else "状态正常"

        results.append(PriorityItem(
            rank=rank, code=g.code, name=g.name, status=g.status,
            gap_pct=g.gap_pct, trend_verdict=trend_verdict,
            action_urgency=urgency, reason=reason,
        ))
    return results


# ── 建议生成（模板化，支持自定义覆盖）─────────────────

DEFAULT_SUGGESTION_TEMPLATES: dict[str, list[str]] = {
    "req_verify_rate": [
        "建议梳理未通过验证的需求，分析阻塞原因并制定解决计划。",
        "加强与产品/测试团队的沟通，提升需求验证通过率至目标水平。",
    ],
    "sprint_completion_rate": [
        "排查延期任务根因（需求变更、资源不足、技术阻塞），制定纠偏计划。",
        "建议在下次迭代规划中增加缓冲时间或调整任务优先级。",
    ],
    "workhour_deviation_rate": [
        "复核计划工时基线是否仍反映当前范围，必要时更新基准。",
        "按「新增需求、返工、外包」拆解工时偏差来源，专项跟进。",
    ],
    "budget_deviation_rate": [
        "专项复核成本归集，确认研发成本执行率偏差原因。",
        "检查是否存在未入账费用或预算编制偏差。",
    ],
    "requirement_cost": [
        "分析需求成本偏高原因：是否因返工、需求变更或估算不准导致。",
        "建议按需求粒度拆分成本，识别高成本需求并优化开发流程。",
    ],
    "defect_escape_rate": [
        "加强测试覆盖率，补充集成测试和端到端测试用例。",
        "建立缺陷逃逸根因分析机制，从源头减少线上问题。",
    ],
    "bug_close_rate": [
        "排查未关闭 Bug 的阻塞原因，加快关闭进度。",
        "建议按严重级别优先处理高优先级 Bug，避免遗留。",
    ],
    "mttr": [
        "优化 Bug 分派流程，缩短从发现到分配的响应时间。",
        "建立分级响应机制（P0≤4h，P1≤24h），提升修复效率。",
    ],
    "rd_resource_input_ratio": [
        "关注研发人力投入占比，确保核心项目有足够人力保障。",
    ],
}

# 通用建议（所有指标适用）
GENERIC_SUGGESTIONS = {
    "intervention": "当前已超阈值，需立即组织专项会议排查根因并制定纠偏措施。",
    "concern": "接近或超出预警阈值，建议在项目例会中重点跟踪。",
    "unavailable": "数据待补齐，当前标记为「待补」不代表项目异常；应优先补齐能改变决策的数据。",
    "normal": "指标处于正常区间，建议持续监控保持当前状态。",
}


def generate_suggestions(
    gap_items: list[GapItem],
    trend_items: list[TrendItem],
    priority_items: list[PriorityItem],
    *,
    custom_templates: Optional[dict[str, list[str]]] = None,
) -> list[dict[str, Any]]:
    """为每项指标生成建议。custom_templates 可覆盖默认模板。"""
    templates = dict(DEFAULT_SUGGESTION_TEMPLATES)
    if custom_templates:
        templates.update(custom_templates)

    trend_map = {t.code: t for t in trend_items}
    suggestions = []

    for p in priority_items:
        g = next((x for x in gap_items if x.code == p.code), None)
        t = trend_map.get(p.code)
        if not g:
            continue

        # 选择建议模板
        tpl = templates.get(p.code, [])
        if p.status in ("intervention", "concern") and tpl:
            # 未达标：给模板中的具体建议
            advice = tpl[0] if len(tpl) == 1 else tpl[0] + " " + tpl[1]
        elif p.status == "unavailable":
            advice = GENERIC_SUGGESTIONS["unavailable"]
        elif p.status == "normal":
            advice = GENERIC_SUGGESTIONS["normal"]
            # 如果趋势在恶化，追加提醒
            if t and not t.is_improving and t.verdict != "数据不足":
                advice += f" 但注意趋势{t.verdict}，需保持关注。"
        else:
            advice = GENERIC_SUGGESTIONS.get(p.status, "")

        suggestions.append({
            "rank": p.rank,
            "code": p.code,
            "name": p.name,
            "status": p.status,
            "urgency": p.action_urgency,
            "advice": advice,
            "trend": t.verdict if t else "—",
            "gap_display": _gap_display(g),
        })
    return suggestions


def _gap_display(g: GapItem) -> str:
    """格式化差距展示。"""
    if g.gap_pct is None:
        return "—"
    if g.is_met:
        return f"达标（余量 {g.gap_pct:.0f}%）"
    return f"未达标（偏离 {abs(g.gap_pct):.0f}%）"


# ── 健康评分 ──────────────────────────────────────────

def calc_health_score(metrics: list) -> dict[str, Any]:
    """综合健康评分（0-100），基于各指标预警等级加权。

    权重分配：
    - 质量类（defect_escape_rate, bug_close_rate）：各 15 分
    - 交付类（req_verify_rate, sprint_completion_rate, requirement_cost）：各 12 分
    - 偏差类（workhour_deviation_rate, budget_deviation_rate）：各 10 分
    - 效率类（mttr）：8 分
    - 资源类（rd_resource_input_ratio）：8 分
    """
    WEIGHTS = {
        "defect_escape_rate": 15, "bug_close_rate": 15,
        "req_verify_rate": 12, "sprint_completion_rate": 12, "requirement_cost": 12,
        "workhour_deviation_rate": 10, "budget_deviation_rate": 10,
        "mttr": 8, "rd_resource_input_ratio": 8,
    }
    # 各等级得分比例
    LEVEL_SCORE = {"normal": 1.0, "concern": 0.5, "intervention": 0.0, "unavailable": 0.5}

    total_weight = 0
    earned = 0
    detail = []
    for m in metrics:
        w = WEIGHTS.get(m.code, 5)
        score_ratio = LEVEL_SCORE.get(m.warning_level, 0.5)
        earned += w * score_ratio
        total_weight += w
        detail.append({
            "code": m.code, "name": m.name, "weight": w,
            "level": m.warning_level, "score_ratio": score_ratio,
        })

    health_score = round(earned / total_weight * 100) if total_weight > 0 else 0

    if health_score >= 85:
        grade = "A"
        grade_text = "项目整体健康，继续保持"
    elif health_score >= 70:
        grade = "B"
        grade_text = "部分指标需关注，整体可控"
    elif health_score >= 55:
        grade = "C"
        grade_text = "多项指标偏离目标，建议专项改进"
    else:
        grade = "D"
        grade_text = "项目健康度较低，需立即干预"

    return {
        "score": health_score,
        "grade": grade,
        "grade_text": grade_text,
        "detail": detail,
    }


# ── 统一入口 ──────────────────────────────────────────

def run_analysis(
    metrics: list,
    targets: dict[str, str],
    history: list[dict],
    *,
    custom_templates: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """运行完整分析流程，返回分析结果字典。

    Parameters
    ----------
    metrics : list[MetricResult]
        9 项指标计算结果
    targets : dict[str, str]
        目标值字典（如 html_generator.TARGETS）
    history : list[dict]
        质量历史数据（quality_history）
    custom_templates : dict, optional
        自定义建议模板，key 为指标 code，value 为建议列表

    Returns
    -------
    dict with keys: gap, trend, priority, suggestions, health
    """
    gap_items = analyze_gap(metrics, targets)
    trend_items = analyze_trend(metrics, history, targets)
    priority_items = prioritize(gap_items, trend_items)
    suggestions = generate_suggestions(
        gap_items, trend_items, priority_items,
        custom_templates=custom_templates,
    )
    health = calc_health_score(metrics)

    return {
        "gap": gap_items,
        "trend": trend_items,
        "priority": priority_items,
        "suggestions": suggestions,
        "health": health,
    }
