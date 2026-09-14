"""项目管理快报 — 调度引擎。

负责：
1. 意图解析：从自然语言或参数中提取 project_id / project_name / month / format
2. 模块调度：按 page_modules 注册表加载并执行页面模块
3. 流水线编排：数据拉取 → 校验 → 指标计算 → 页面模块 → 文档生成
4. 输出管理：HTML / PDF / metrics JSON 文件输出

可作为 skill_tools 的后端调用，也可独立运行。
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# 确保 Skill 根目录在 sys.path
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


# ──────────────────────────────────────────────
# 1. 意图解析
# ──────────────────────────────────────────────

def _expand_month_range(start: str, end: str) -> list[str]:
    """展开月份范围，如 ('2026-06', '2026-08') → ['2026-06', '2026-07', '2026-08']。"""
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    months = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _get_quarter_range(month_str: str) -> tuple[str, str]:
    """根据月份计算所属季度的起止月份。

    季度划分：Q1=1-3月, Q2=4-6月, Q3=7-9月, Q4=10-12月。

    Args:
        month_str: 月份字符串，格式 "YYYY-MM"

    Returns:
        (quarter_start, quarter_end) 如 ("2026-07", "2026-09")
    """
    year = int(month_str[:4])
    month = int(month_str[5:7])
    quarter = (month - 1) // 3  # 0=Q1, 1=Q2, 2=Q3, 3=Q4
    q_start = quarter * 3 + 1
    q_end = q_start + 2
    return f"{year}-{q_start:02d}", f"{year}-{q_end:02d}"


def parse_intent(query: str) -> dict[str, Any]:
    """从自然语言中解析用户意图。

    支持的模式：
    - "帮我生成某项目 2026-07 的快报"
    - "查看某项目 7月的月报"
    - "列出所有项目"
    - "导出某项目的 PDF"
    - "智联车载IoT 2026年7月 快报"
    - "119项目 7月 指标"
    - "某项目 2026-06到2026-07 跨月合并快报"
    - "某项目 6-7月 合并报告"
    - "示例项目 Q1 季度快报"              ← 季度模式
    - "10001 2025年一季度快报"            ← 季度模式
    - "某项目 第四季度 报告"              ← 季度模式
    - "某项目 7月 只看P01"              ← 页面选择
    - "某项目 7月 P03和P04"             ← 多页面选择
    - "某项目 7月 只看精细化管理"        ← 中文页面名

    Returns:
        {
            "action": "list" | "fetch" | "generate" | "pipeline",
            "project_id": str | None,
            "project_name": str | None,
            "month": str | None,  # YYYY-MM（单月）
            "month_range": list[str] | None,  # 多月范围，如 ["2026-06", "2026-07"]
            "formats": list[str],  # ["html", "pdf", "metrics"]
            "page_ids": list[str] | None,  # 页面选择，如 ["p01", "p03"]
        }
    """
    result: dict[str, Any] = {
        "action": "pipeline",
        "project_id": None,
        "project_name": None,
        "project_code": None,
        "month": None,
        "month_range": None,
        "formats": ["html", "pdf", "metrics"],
        "page_ids": None,
    }

    query = query.strip()

    # ── 0. 列出项目（仅当查询中没有数字 ID / 月份时才判定为 list）──
    if re.search(r"(列出|有哪些|全部项目|项目列表)", query):
        result["action"] = "list"
        # 提取页码：「第2页」「第 3 页」「page2」
        _page_m = re.search(r'第\s*(\d+)\s*页', query)
        if _page_m:
            result["list_page"] = int(_page_m.group(1))
        return result

    # ── 1. 先提取时间（避免被后续正则误匹配）──
    cleaned = query  # 用于后续匹配，逐步移除已识别的时间片段

    # ── 1a. 季度匹配（优先于月份范围）──
    # "Q1"/"Q2"/"Q3"/"Q4" / "2026Q2" / "一季度"/"二季度" / "第一季度" / "第1季度"
    _CN_DIGITS = {'一': '1', '二': '2', '三': '3', '四': '4'}
    quarter_match = re.search(
        r'(?:第\s*)?(?:[Qq]([1-4])(?:\s*季(?:度)?)?|([1-4一二三四])\s*季(?:度)?)', cleaned
    )
    if quarter_match:
        q_char = quarter_match.group(1) or quarter_match.group(2)
        q = int(_CN_DIGITS.get(q_char, q_char))
        q_start_month = (q - 1) * 3 + 1
        q_end_month = q_start_month + 2
        year_m = re.search(r'(20\d{2})', cleaned)
        year = int(year_m.group(1)) if year_m else datetime.now().year
        start_m = f"{year}-{q_start_month:02d}"
        end_m = f"{year}-{q_end_month:02d}"
        result["month_range"] = _expand_month_range(start_m, end_m)
        result["month"] = end_m
        cleaned = cleaned[:quarter_match.start()] + cleaned[quarter_match.end():]
        # 移除已用于季度的年份（避免被误识别为项目名）
        if year_m:
            cleaned = cleaned.replace(str(year), '', 1)
            cleaned = cleaned.replace('年', '', 1) if '年' in cleaned else cleaned
    else:
        # ── 1b. 月份范围 ──
        # "2026-06到2026-07" / "2026-06:2026-07" / "2026-06~2026-07" / "2026-06至2026-07"
        range_match = re.search(
            r'(\d{4}-\d{2})\s*[到至:\-~]+\s*(\d{4}-\d{2})', cleaned
        )
        if range_match:
            start_m, end_m = range_match.group(1), range_match.group(2)
            result["month_range"] = _expand_month_range(start_m, end_m)
            result["month"] = end_m  # 默认用最后一个月
            cleaned = cleaned[:range_match.start()] + cleaned[range_match.end():]
        else:
            # "6到7月" / "6-7月" / "6~7月"（同年简写）
            cn_range = re.search(
                r'(\d{1,2})\s*[到至\-~]\s*(\d{1,2})\s*月', cleaned
            )
            if cn_range:
                sm, em = int(cn_range.group(1)), int(cn_range.group(2))
                year_m = re.search(r'(20\d{2})', cleaned)
                year = int(year_m.group(1)) if year_m else datetime.now().year
                start_m = f"{year}-{sm:02d}"
                end_m = f"{year}-{em:02d}"
                result["month_range"] = _expand_month_range(start_m, end_m)
                result["month"] = end_m
                cleaned = cleaned[:cn_range.start()] + cleaned[cn_range.end():]
            else:
                # YYYY-MM
                month_match = re.search(r'(\d{4}-\d{2})', cleaned)
                if month_match:
                    result["month"] = month_match.group(1)
                    cleaned = cleaned[:month_match.start()] + cleaned[month_match.end():]
                else:
                    # "2026年7月" / "7月" / "07月"
                    cn_month = re.search(r'(20\d{2})?年(\d{1,2})月', cleaned)
                    if cn_month:
                        year = cn_month.group(1) or datetime.now().strftime("%Y")
                        m = int(cn_month.group(2))
                        result["month"] = f"{year}-{m:02d}"
                        cleaned = cleaned[:cn_month.start()] + cleaned[cn_month.end():]
                    else:
                        cn_month2 = re.search(r'(\d{1,2})月', cleaned)
                        if cn_month2:
                            m = int(cn_month2.group(1))
                            year_match = re.search(r'(20\d{2})', cleaned)
                            year = year_match.group(1) if year_match else datetime.now().strftime("%Y")
                            result["month"] = f"{year}-{m:02d}"
                            cleaned = cleaned[:cn_month2.start()] + cleaned[cn_month2.end():]

    # 移除 "今年" "本月" "上月" 等相对时间词
    if re.search(r'本月', cleaned):
        result["month"] = datetime.now().strftime("%Y-%m")
        cleaned = cleaned.replace("本月", "")
    elif re.search(r'上月', cleaned):
        from datetime import timedelta
        last = datetime.now() - timedelta(days=30)
        result["month"] = last.strftime("%Y-%m")
        cleaned = cleaned.replace("上月", "")
    elif re.search(r'今年', cleaned):
        # "今年" 不指定具体月份，保持 month 为 None（后续会默认当月）
        cleaned = cleaned.replace("今年", "")

    # ── 2. 提取格式 ──
    if re.search(r'(PDF|pdf|打印版|打印)', query):
        result["formats"] = ["pdf"]
    elif re.search(r'(HTML|html|交互版|交互)', query):
        result["formats"] = ["html"]
    elif re.search(r'(指标|JSON|json)', query):
        result["formats"] = ["metrics"]
    elif re.search(r'(Word|word|docx|DOCX)', query):
        result["formats"] = ["docx"]

    # ── 2b. 提取页面选择（P01~P06 / 中文页面名）──
    _PAGE_CN_MAP = {
        "精细化管理": "p01", "产研管理": "p01", "产研精细化管理": "p01",
        "投入偏差": "p02", "项目投入偏差": "p02", "投入": "p02",
        "人员健康": "p03", "人员投入健康度": "p03", "健康度": "p03",
        "产品质量": "p04",
        "bug效率": "p05", "bug修复效率": "p05", "修复效率": "p05",
        "价值交付": "p06", "需求交付": "p06", "需求价值交付": "p06",
    }
    # 先匹配 P0x 编号模式（如 P01, p03, P01~P03, p01和p04）
    page_codes = re.findall(r'[Pp](0[1-6])', query)
    if page_codes:
        result["page_ids"] = [f"p{c}" for c in page_codes]
    else:
        # 再匹配中文页面名
        cn_pages = []
        for keyword, pid in _PAGE_CN_MAP.items():
            if keyword in query:
                cn_pages.append(pid)
        if cn_pages:
            result["page_ids"] = sorted(set(cn_pages))

    # ── 3. 从 cleaned 中移除格式词 + 停用词，再做数字/名称提取 ──
    # 先移除完整短语（避免分步移除导致语义断裂）
    cleaned = re.sub(r'项目管理快报|项目月报|项目报告', '', cleaned, flags=re.IGNORECASE)
    # 再移除格式关键词
    cleaned = re.sub(r'(PDF|pdf|HTML|html|Word|word|docx|DOCX|打印版|打印|交互版|交互|指标|JSON|json)', '', cleaned)
    # 移除页面选择关键词
    cleaned = re.sub(r'(?<![A-Za-z0-9])[Pp](0[1-6])(?!\d)', '', cleaned)
    cleaned = re.sub(r'(精细化管理|产研管理|产研精细化管理|投入偏差|项目投入偏差|人员健康|人员投入健康度|健康度|产品质量|bug效率|bug修复效率|修复效率|价值交付|需求交付|需求价值交付)', '', cleaned, flags=re.IGNORECASE)
    # 清理分隔符
    cleaned = re.sub(r'[,，、~与]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    # 移除其他停用词（已移除"项目"、"发"、"管理"，避免误删项目名称）
    stop_words = re.compile(
        r'(帮我|请|给|我|生成|查看|导出|看看|做|出|的|了|一个|一下|'
        r'快报|月报|报告|月度|分析|'
        r'合并|跨月|合并版|合并报告|跨月合并|'
        r'季度|季度快报|'
        r'只看|只要|仅|页面|板块|领域|和)', re.UNICODE
    )
    cleaned = stop_words.sub('', cleaned)
    # 停用词移除后可能留下多余空格，再次压缩
    cleaned = re.sub(r'\s+', ' ', cleaned)

    # ── 4. 提取项目编号（如 2026RDP006：年份+字母+数字）──
    pcode_match = re.search(r'\d{4}[A-Za-z]+\d+', cleaned)
    if pcode_match:
        result["project_code"] = pcode_match.group(0)
        cleaned = cleaned[:pcode_match.start()] + cleaned[pcode_match.end():]
    else:
        # ── 4b. 提取项目 ID（1-6 位纯数字）──
        # 必须为独立数字：不能紧邻字母/点号（版本号如 V3.0）、不能紧邻 [-/]（规格如 55/75）
        pid_match = re.search(
            r'(?<!\d)(?<![-/.A-Za-z])(\d{1,6})(?!\d)(?![-/.A-Za-z])', cleaned
        )
        if pid_match:
            result["project_id"] = pid_match.group(1)
            cleaned = cleaned[:pid_match.start()] + cleaned[pid_match.end():]

    # ── 5. 剩余文本作为项目名关键词 ──
    name_candidate = cleaned.strip()
    if name_candidate:
        result["project_name"] = name_candidate

    return result


def resolve_intent(query: str) -> dict[str, Any]:
    """增强版意图解析：调用 API 项目列表做模糊匹配。

    在 parse_intent 基础上补充：
    - 将用户输入的项目名关键词与 API 项目列表做模糊匹配
    - 自动填充 project_id

    Returns:
        同 parse_intent，但 project_id / project_name 会被填充为匹配结果。
    """
    intent = parse_intent(query)

    if intent["action"] == "list":
        return intent

    # 如果已有 project_id 但没有 project_name，或者有关键词需要匹配
    keyword = intent.get("project_name") or ""
    pid = intent.get("project_id")
    pcode = intent.get("project_code")

    try:
        from data_modules.fetch_data import _get
        data = _get("project", "project_info", params={"id": "", "page": 1, "size": 200})
        projects = data.get("data", {}).get("records", []) if isinstance(data, dict) else []
    except Exception:
        projects = []

    if not projects:
        return intent

    # 按项目编号精确匹配（优先级最高）
    if pcode:
        for p in projects:
            if str(p.get("code", "")) == pcode:
                intent["project_id"] = str(p["id"])
                intent["project_name"] = p.get("name", "")
                intent["project_code"] = p.get("code", "")
                return intent

    # 按 ID 精确匹配
    if pid:
        for p in projects:
            if str(p.get("id", "")) == pid:
                intent["project_id"] = str(p["id"])
                intent["project_name"] = p.get("name", "")
                return intent
        # ID 未匹配到项目列表，标记未找到
        if not intent.get("project_name"):
            intent["_project_not_found"] = True
            return intent

    # 按关键词模糊匹配（名称包含）
    # 优先用解析出的关键词，回退到原始查询文本
    keywords_to_try = []
    if keyword and len(keyword) >= 2:
        keywords_to_try.append(keyword)
    # 回退：用原始查询文本（去掉常见停用词后）做模糊匹配
    raw_name = re.sub(
        r'(帮我|请|给|我|生成|查看|导出|看看|做|出|发|的|了|一个|一下|'
        r'项目|快报|月报|报告|月度|分析|看看|合并|跨月|合并版|合并报告|跨月合并|'
        r'PDF|pdf|HTML|html|Word|word|'
        r'docx|DOCX|打印版|打印|交互版|交互|指标|JSON|json)',
        '', query
    ).strip()
    # 去掉时间片段
    raw_name = re.sub(r'\d{4}-\d{2}', '', raw_name)
    raw_name = re.sub(r'(20\d{2})?年\d{1,2}月', '', raw_name)
    raw_name = re.sub(r'\d{1,2}月', '', raw_name)
    raw_name = re.sub(r'(今年|本月|上月)', '', raw_name).strip()
    if raw_name and raw_name != keyword and len(raw_name) >= 2:
        keywords_to_try.append(raw_name)

    for kw in keywords_to_try:
        # 正向：关键词是项目名的子串（如 "智联车载IoT" in "智联车载IoT软件底座项目"）
        matches = [p for p in projects if kw in p.get("name", "")]
        # 反向：项目名是关键词的子串（如 "智联车载IoT软件底座" in "智联车载IoT软件底座项目"）
        if not matches:
            matches = [p for p in projects if p.get("name", "") in kw]
        # 前缀匹配：项目名以关键词开头，或关键词以项目名开头
        if not matches:
            matches = [p for p in projects
                       if p.get("name", "").startswith(kw) or kw.startswith(p.get("name", ""))]
        if matches:
            # 多个匹配时优先选名称最短的（最精确）
            matches.sort(key=lambda p: len(p.get("name", "")))
            p = matches[0]
            intent["project_id"] = str(p["id"])
            intent["project_name"] = p.get("name", "")
            if len(matches) > 1:
                intent["_candidates"] = [
                    {"id": str(m["id"]), "name": m.get("name", "")} for m in matches
                ]
            # 如果之前误提取了 project_id，现在纠正
            if pid and not intent.get("_original_id_matched"):
                intent.pop("project_id", None)
                intent["project_id"] = str(p["id"])
            return intent

    return intent


# ──────────────────────────────────────────────
# 2. 页面模块调度
# ──────────────────────────────────────────────

def collect_page_contexts(snap: dict, metrics_list: list) -> list[dict[str, Any]]:
    """遍历所有页面模块，收集各领域渲染上下文。

    Args:
        snap: 统一数据快照
        metrics_list: create_metrics() 返回的 MetricResult 列表

    Returns:
        每个领域一个 dict:
        {page_id, title, core_question, domain_index, charts, conclusion, action, decision_brief}
    """
    from data_modules.metrics_engine import MetricResult
    from page_modules import get_all_modules

    metrics_map = {m.code: m for m in metrics_list}
    contexts = []

    for module in get_all_modules():
        try:
            data = module.fetch_data(snap)
            ctx = module.build_context(data, metrics_map)
            contexts.append({
                "page_id": module.PAGE_ID,
                "title": module.TITLE,
                "core_question": module.CORE_QUESTION,
                "domain_index": module.DOMAIN_INDEX,
                **ctx,
            })
        except Exception as e:
            contexts.append({
                "page_id": module.PAGE_ID,
                "title": module.TITLE,
                "core_question": module.CORE_QUESTION,
                "domain_index": module.DOMAIN_INDEX,
                "charts": [],
                "conclusion": f"模块加载失败: {e}",
                "action": "",
                "decision_brief": "数据异常",
            })

    return contexts


# ──────────────────────────────────────────────
# 3. 流水线编排
# ──────────────────────────────────────────────

class PipelineResult:
    """流水线执行结果。"""

    def __init__(self):
        self.success: bool = False
        self.project_id: str = ""
        self.project_name: str = ""
        self.month: str = ""
        self.snapshot_path: str = ""
        self.outputs: dict[str, str] = {}
        self.data_quality: dict[str, Any] = {}
        self.page_contexts: list[dict] = []
        self.errors: list[str] = []
        self.validation_report_path: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "month": self.month,
            "snapshot_path": self.snapshot_path,
            "outputs": self.outputs,
            "data_quality": self.data_quality,
            "page_count": len(self.page_contexts),
            "validation_report_path": self.validation_report_path,
            "errors": self.errors,
        }


def run_pipeline(
    project_id: str,
    project_name: str,
    settlement_month: str,
    output_dir: str = "output",
    formats: Optional[list[str]] = None,
    as_of_date: Optional[str] = None,
    page_ids: Optional[list[str]] = None,
    focus: Optional[str] = None,
    columns: Optional[list[str]] = None,
    output_format: Optional[str] = None,
) -> PipelineResult:
    """执行完整流水线：数据拉取 → 校验 → 指标计算 → 页面模块 → 文档生成。

    Args:
        project_id: 项目 ID
        project_name: 项目名称
        settlement_month: 结算月份 YYYY-MM
        output_dir: 输出目录
        formats: 输出格式列表，默认 ["html", "pdf", "metrics"]
        as_of_date: 数据截至日期

    Returns:
        PipelineResult 对象
    """
    from data_modules.fetch_data import fetch
    from data_modules.metrics_engine import create_metrics, load_snapshot
    from export_modules.report_runner import (
        generate_html, generate_pdf_reportlab, generate_docx,
        generate_pptx, generate_metrics_json, generate_validation,
    )

    if formats is None:
        formats = ["html", "pdf", "docx", "metrics"]

    result = PipelineResult()
    result.project_id = project_id
    result.project_name = project_name
    result.month = settlement_month

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── Step 1: 数据拉取 ──
    print(f"[Pipeline] Step 1/4: 拉取数据 (project={project_name}, month={settlement_month})")
    try:
        snap = fetch(
            project_id=project_id,
            project_name=project_name,
            settlement_month=settlement_month,
            as_of_date=as_of_date,
        )
    except Exception as e:
        result.errors.append(f"数据拉取失败: {e}")
        return result

    # ── 项目存在性校验：防止无效项目 ID/名称静默生成空报告 ──
    _proj_block = snap.get("project", {})
    if _proj_block.get("_data_status") == "missing":
        result.errors.append(
            f"项目不存在：ID={project_id}，名称={project_name}。"
            f"请检查项目 ID 或名称是否正确，可回复「列出项目」查看可用项目列表。"
        )
        return result

    # 保存 snapshot
    from export_modules.report_runner import safe_filename
    snap_path = out / f"{safe_filename(project_name)}_{settlement_month}_snapshot.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    result.snapshot_path = str(snap_path)

    # 记录数据质量
    dq = snap.get("data_quality", {})
    result.data_quality = {
        "missing_fields": dq.get("missing_fields", []),
        "warnings": dq.get("warnings", []),
        "source_versions": dq.get("source_versions", {}),
    }

    # ── Step 2: 指标计算 ──
    print(f"[Pipeline] Step 2/4: 计算指标")
    metrics_list, conflicts = create_metrics(snap)

    # ── Step 3: 页面模块调度 ──
    print(f"[Pipeline] Step 3/4: 收集页面模块上下文")
    result.page_contexts = collect_page_contexts(snap, metrics_list)
    print(f"  → {len(result.page_contexts)} 个领域模块已加载")

    # ── Step 4: 文档生成 ──
    fmt_list = formats or ["html", "pdf", "docx", "metrics"]
    if output_format:
        # output_format 优先于 formats
        from export_modules.report_runner import _resolve_output_format
        fmt_list = _resolve_output_format(output_format)
        if "metrics" not in fmt_list:
            fmt_list.append("metrics")  # 始终生成指标 JSON
    # "all" 展开
    if "all" in fmt_list:
        fmt_list = ["html", "pdf", "docx", "pptx", "metrics"]
    # 生成 HTML 时，确保同时生成 PDF/DOCX/PPTX 作为导出按钮的下载源
    if "html" in fmt_list:
        for fmt in ("pdf", "docx", "pptx"):
            if fmt not in fmt_list:
                fmt_list.append(fmt)
    print(f"[Pipeline] Step 4/5: 生成文档 ({', '.join(fmt_list)})")

    # ── 先生成 PDF/DOCX/PPTX，再传给 HTML 作为下载链接 ──
    _export_files = {}

    if "pdf" in fmt_list:
        try:
            path = generate_pdf_reportlab(snap, out, page_ids=page_ids)
            result.outputs["pdf"] = str(path)
            _export_files["pdf"] = Path(path).name
            print(f"  PDF:  {path}")
        except Exception as e:
            result.errors.append(f"PDF 生成失败: {e}")

    if "docx" in fmt_list:
        try:
            path = generate_docx(snap, out, page_ids=page_ids)
            result.outputs["docx"] = str(path)
            _export_files["docx"] = Path(path).name
            print(f"  DOCX: {path}")
        except ImportError as e:
            result.errors.append(f"DOCX 生成失败（缺少 python-docx）: {e}")
        except Exception as e:
            result.errors.append(f"DOCX 生成失败: {e}")

    if "pptx" in fmt_list:
        try:
            path = generate_pptx(snap, out, page_ids=page_ids)
            result.outputs["pptx"] = str(path)
            _export_files["pptx"] = Path(path).name
            print(f"  PPTX: {path}")
        except ImportError as e:
            result.errors.append(f"PPTX 生成失败（缺少 python-pptx）: {e}")
        except Exception as e:
            result.errors.append(f"PPTX 生成失败: {e}")

    if "metrics" in fmt_list:
        try:
            path = generate_metrics_json(snap, out)
            result.outputs["metrics"] = str(path)
            print(f"  JSON: {path}")
        except Exception as e:
            result.errors.append(f"Metrics 生成失败: {e}")

    if "html" in fmt_list:
        try:
            proj_info = snap.get("project", {})
            pn = proj_info.get("project_name", "unknown")
            sm = proj_info.get("stat_month", "")
            pdf_name = f"{safe_filename(pn)}_{sm}_交互快报.pdf"
            path = generate_html(snap, out, pdf_filename=pdf_name,
                                 page_ids=page_ids, focus=focus, columns=columns,
                                 export_files=_export_files)
            result.outputs["html"] = str(path)
            print(f"  HTML: {path}")
        except Exception as e:
            result.errors.append(f"HTML 生成失败: {e}")

    # ── Step 5: 数据校验报告 ──
    print(f"[Pipeline] Step 5/5: 生成数据校验报告")
    try:
        val_path = generate_validation(snap, out)
        result.validation_report_path = str(val_path)
        result.outputs["validation"] = str(val_path)
        print(f"  校验报告: {val_path}")
    except Exception as e:
        result.errors.append(f"数据校验报告生成失败: {e}")
        print(f"  校验报告生成失败: {e}")

    result.success = len(result.errors) == 0
    return result


# ──────────────────────────────────────────────
# 3b. 跨月合并流水线
# ──────────────────────────────────────────────

def run_pipeline_multi(
    project_id: str,
    project_name: str,
    month_range: list[str],
    output_dir: str = "output",
    formats: Optional[list[str]] = None,
    page_ids: Optional[list[str]] = None,
    output_format: Optional[str] = None,
    focus: Optional[str] = None,
) -> PipelineResult:
    """跨月合并流水线：拉取多月数据，生成合并报告。

    Args:
        project_id: 项目 ID
        project_name: 项目名称
        month_range: 月份列表，如 ["2026-06", "2026-07"]
        output_dir: 输出目录
        formats: 输出格式列表
        page_ids: 页面选择，如 ["p01","p03"]，None=全部
        output_format: 输出格式字符串，如 "all"/"html pdf"/"pptx"
        focus: 关键词过滤，只保留匹配的指标

    Returns:
        PipelineResult 对象
    """
    from data_modules.fetch_data import fetch, fetch_quarterly_board
    from export_modules.report_runner import (
        generate_merged_html, generate_merged_pdf, generate_merged_docx,
        generate_validation,
        _resolve_output_format,
    )

    if formats is None:
        formats = ["html", "pdf", "docx", "metrics"]
    if output_format:
        formats = _resolve_output_format(output_format)
    # "all" 展开
    if formats == ["all"] or "all" in formats:
        formats = ["html", "pdf", "docx", "pptx"]

    result = PipelineResult()
    result.project_id = project_id
    result.project_name = project_name
    result.month = "~".join(month_range)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── 逐月拉取数据 ──
    from export_modules.report_runner import safe_filename
    snapshots = []
    for month in month_range:
        print(f"\n[Multi-Pipeline] 拉取数据: month={month}")
        try:
            snap = fetch(
                project_id=project_id,
                project_name=project_name,
                settlement_month=month,
            )
            # 保存每月 snapshot
            snap_path = out / f"{safe_filename(project_name)}_{month}_snapshot.json"
            snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
            snapshots.append(snap)
            print(f"  ✓ {month} 数据拉取成功")
        except Exception as e:
            result.errors.append(f"{month} 数据拉取失败: {e}")
            print(f"  ✗ {month} 数据拉取失败: {e}")

    if not snapshots:
        return result

    # ── 项目存在性校验 ──
    _first_proj = snapshots[0].get("project", {})
    if _first_proj.get("_data_status") == "missing":
        result.errors.append(
            f"项目不存在：ID={project_id}，名称={project_name}。"
            f"请检查项目 ID 或名称是否正确，可回复「列出项目」查看可用项目列表。"
        )
        return result

    # ── 判断导出模式：按季度 or 按月份 ──
    first_m = snapshots[0].get("project", {}).get("stat_month", "")
    last_m = snapshots[-1].get("project", {}).get("stat_month", "")
    q_start, q_end = _get_quarter_range(first_m)
    
    # 判断是否为完整季度
    is_quarterly = (
        len(snapshots) == 3 and
        first_m == q_start and
        last_m == q_end
    )
    
    aggregated_data = {}
    quarter_label = ""
    
    if is_quarterly:
        # ── 按季度导出模式 ──
        quarter_label = f"Q{(int(first_m[5:7]) - 1) // 3 + 1}"
        print(f"\n[Multi-Pipeline] 按季度导出模式 ({quarter_label}: {q_start} ~ {q_end})")
        try:
            aggregated_data = fetch_quarterly_board(project_id, q_start, q_end)
            if aggregated_data:
                print(f"  ✓ 季度聚合数据拉取成功 ({quarter_label})")
            else:
                print(f"  ⚠ 季度聚合数据为空")
        except Exception as e:
            print(f"  ✗ 季度聚合数据拉取失败: {e}")
            result.errors.append(f"季度聚合数据拉取失败: {e}")
    else:
        # ── 按月份合并模式 ──
        range_label = f"{first_m}~{last_m}"
        print(f"\n[Multi-Pipeline] 按月份合并模式 ({range_label})")
        try:
            aggregated_data = fetch_quarterly_board(project_id, first_m, last_m)
            if aggregated_data:
                print(f"  ✓ 月份范围聚合数据拉取成功 ({range_label})")
            else:
                print(f"  ⚠ 月份范围聚合数据为空")
        except Exception as e:
            print(f"  ✗ 月份范围聚合数据拉取失败: {e}")
            result.errors.append(f"月份范围聚合数据拉取失败: {e}")

    # ── 生成合并报告 ──
    print(f"\n[Multi-Pipeline] 生成跨月合并报告 ({len(snapshots)} 个月, 格式: {', '.join(formats)})")

    # 提前计算 PDF 文件名，供 HTML 导出按钮使用
    first_proj = snapshots[0].get("project", {})
    last_proj = snapshots[-1].get("project", {})
    _pn = first_proj.get("project_name", "unknown")
    _fm = first_proj.get("stat_month", "")
    _lm = last_proj.get("stat_month", "")
    _ml = _fm if len(snapshots) == 1 else f"{_fm}_{_lm}"
    pdf_name = f"{safe_filename(_pn)}_{_ml}_跨月合并快报.pdf"

    # 先生成 PDF/DOCX，再传给 HTML 作为下载链接
    _export_files = {}
    if "pdf" in formats:
        try:
            path = generate_merged_pdf(snapshots, out, quarter_label=quarter_label,
                                       aggregated_data=aggregated_data,
                                       page_ids=page_ids)
            result.outputs["pdf"] = str(path)
            _export_files["pdf"] = Path(path).name
            print(f"  PDF:  {path}")
        except Exception as e:
            result.errors.append(f"合并 PDF 生成失败: {e}")

    if "docx" in formats:
        try:
            path = generate_merged_docx(snapshots, out, quarter_label=quarter_label,
                                        aggregated_data=aggregated_data,
                                        page_ids=page_ids)
            result.outputs["docx"] = str(path)
            _export_files["docx"] = Path(path).name
            print(f"  DOCX: {path}")
        except ImportError as e:
            result.errors.append(f"合并 DOCX 生成失败（缺少 python-docx）: {e}")
        except Exception as e:
            result.errors.append(f"合并 DOCX 生成失败: {e}")

    if "html" in formats:
        try:
            path = generate_merged_html(snapshots, out, pdf_filename=pdf_name,
                                        quarter_label=quarter_label,
                                        export_files=_export_files,
                                        aggregated_data=aggregated_data,
                                        page_ids=page_ids,
                                        focus=focus)
            result.outputs["html"] = str(path)
            print(f"  HTML: {path}")
        except Exception as e:
            result.errors.append(f"合并 HTML 生成失败: {e}")

    # ── 数据校验报告（始终生成） ──
    print(f"\n[Multi-Pipeline] 生成数据校验报告")
    try:
        # 对每个月份的 snapshot 分别校验
        for snap in snapshots:
            val_path = generate_validation(snap, out)
            result.outputs[f"validation_{snap.get('project', {}).get('stat_month', '')}"] = str(val_path)
            print(f"  校验报告: {val_path}")
        if not result.outputs.get("validation"):
            # 至少保留一个主校验报告路径
            first_val = [v for k, v in result.outputs.items() if k.startswith("validation_")]
            if first_val:
                result.outputs["validation"] = first_val[0]
    except Exception as e:
        result.errors.append(f"数据校验报告生成失败: {e}")
        print(f"  校验报告生成失败: {e}")

    result.success = len(result.errors) == 0
    return result


# ──────────────────────────────────────────────
# 4. 项目发现
# ──────────────────────────────────────────────

def discover_projects() -> list[dict]:
    """从 API 拉取全部项目列表。"""
    from data_modules.fetch_data import _get
    try:
        data = _get("project", "project_info", params={"id": "", "page": 1, "size": 200})
        records = data.get("data", {}).get("records", []) if isinstance(data, dict) else []
        return [
            {"id": r.get("id"), "code": r.get("code", ""), "name": r.get("name", "")}
            for r in records
        ]
    except Exception:
        return []


# ──────────────────────────────────────────────
# 5. 凭证加载
# ──────────────────────────────────────────────

def load_credentials(cred_file: Optional[Path] = None):
    """加载 API 凭证。
    优先使用环境变量 PMO_API_USERNAME/PMO_API_PASSWORD（自动登录模式）。
    如存在 credentials.bat 则向后兼容加载旧格式。"""
    if os.environ.get("QUALITY_API_TOKEN") or os.environ.get("PMO_API_USERNAME"):
        return
    if cred_file is None:
        cred_file = _SKILL_DIR / "credentials.bat"
    if not cred_file.exists():
        return
    try:
        for line in cred_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("set ") and "=" in line:
                pair = line[4:]
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if value.startswith("%") and value.endswith("%"):
                        ref = value[1:-1]
                        value = os.environ.get(ref, "")
                    if key and value:
                        os.environ.setdefault(key, value)
    except Exception:
        pass


# 模块导入时自动加载凭证
load_credentials()


# ──────────────────────────────────────────────
# 6. CLI 入口
# ──────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="项目管理快报 — 调度引擎")
    sub = parser.add_subparsers(dest="command")

    # list
    sub.add_parser("list", help="列出所有项目")

    # parse（意图解析测试）
    p_parse = sub.add_parser("parse", help="测试意图解析")
    p_parse.add_argument("query", nargs="+", help="自然语言查询")

    # run
    p_run = sub.add_parser("run", help="执行完整流水线")
    p_run.add_argument("--project-id", required=True)
    p_run.add_argument("--project-name", required=True)
    p_run.add_argument("--month", required=True)
    p_run.add_argument("--output-dir", default="output")
    p_run.add_argument("--format", nargs="+", default=None,
                       help="输出格式: html pdf docx pptx metrics all（空格分隔，如 --format all 或 --format html pdf）")
    p_run.add_argument("--pages", default=None, help="页面模块选择，逗号分隔，如 p01,p03,p05")
    p_run.add_argument("--focus", default=None, help="行过滤关键词，如 --focus 解决方案")
    p_run.add_argument("--columns", default=None, help="列过滤，逗号分隔，如 --columns 本年累计,同比")

    # modules（列出所有页面模块）
    sub.add_parser("modules", help="列出所有页面模块")

    args = parser.parse_args()

    if args.command == "list":
        projects = discover_projects()
        print(f"共 {len(projects)} 个项目：")
        for p in projects:
            print(f"  {p.get('id', '?'):>8}  {p.get('code', '?'):<16}  {p.get('name', '?')}")

    elif args.command == "parse":
        query = " ".join(args.query)
        intent = parse_intent(query)
        print(f"查询: {query}")
        print(f"意图: {json.dumps(intent, ensure_ascii=False, indent=2)}")

    elif args.command == "run":
        # 解析页面选择参数
        page_ids = None
        if hasattr(args, 'pages') and args.pages:
            page_ids = [p.strip() for p in args.pages.split(',')]
        
        # 解析行过滤和列过滤参数
        focus = getattr(args, 'focus', None)
        columns = None
        if hasattr(args, 'columns') and args.columns:
            columns = [c.strip() for c in args.columns.split(',')]
        
        # 解析输出格式（支持 --format all）
        fmt_list = args.format
        output_format = None
        if fmt_list and len(fmt_list) == 1 and fmt_list[0].lower() == "all":
            output_format = "all"
            fmt_list = None
        
        # 检测季度格式 (如 2026Q2)
        quarter_match = re.match(r'^(\d{4})Q([1-4])$', args.month)
        if quarter_match:
            year = int(quarter_match.group(1))
            q = int(quarter_match.group(2))
            # 计算季度的起止月份
            start_month = (q - 1) * 3 + 1
            end_month = q * 3
            month_range = [f"{year}-{m:02d}" for m in range(start_month, end_month + 1)]
            print(f"[INFO] 季度模式: {args.month} = {month_range[0]} ~ {month_range[-1]} ({len(month_range)} 个月)")
            result = run_pipeline_multi(
                project_id=args.project_id,
                project_name=args.project_name,
                month_range=month_range,
                output_dir=args.output_dir,
                formats=fmt_list,
                page_ids=page_ids,
                output_format=output_format,
                focus=focus,
            )
        # 检测是否为跨月范围 (如 2026-06到2026-08)
        elif re.search(r'(\d{4}-\d{2})\s*[到至:~\-]+\s*(\d{4}-\d{2})', args.month):
            range_match = re.search(r'(\d{4}-\d{2})\s*[到至:~\-]+\s*(\d{4}-\d{2})', args.month)
            start_m, end_m = range_match.group(1), range_match.group(2)
            month_range = _expand_month_range(start_m, end_m)
            print(f"[INFO] 跨月模式: {month_range[0]} ~ {month_range[-1]} ({len(month_range)} 个月)")
            result = run_pipeline_multi(
                project_id=args.project_id,
                project_name=args.project_name,
                month_range=month_range,
                output_dir=args.output_dir,
                formats=fmt_list,
                page_ids=page_ids,
                output_format=output_format,
                focus=focus,
            )
        else:
            result = run_pipeline(
                project_id=args.project_id,
                project_name=args.project_name,
                settlement_month=args.month,
                output_dir=args.output_dir,
                formats=fmt_list,
                page_ids=page_ids,
                focus=focus,
                columns=columns,
                output_format=output_format,
            )
        print(f"\n{'='*60}")
        print(f"  状态: {'成功' if result.success else '失败'}")
        print(f"  项目: {result.project_name} ({result.project_id})")
        print(f"  月份: {result.month}")
        print(f"  目标: {args.output_dir}/")
        print(f"  数据质量: missing={len(result.data_quality.get('missing_fields', []))}, "
              f"warnings={len(result.data_quality.get('warnings', []))}")
        print(f"  页面模块: {len(result.page_contexts)} 个")
        for key, path in result.outputs.items():
            print(f"  {key:>8}: {path}")
        if result.errors:
            print(f"  错误:")
            for err in result.errors:
                print(f"    - {err}")

    elif args.command == "modules":
        from page_modules import PAGE_REGISTRY
        print(f"共 {len(PAGE_REGISTRY)} 个页面模块：")
        for page_id, (mod_path, cls_name, title, idx) in sorted(PAGE_REGISTRY.items(), key=lambda x: x[1][3]):
            print(f"  {idx}. {title:<20} ({page_id}) → {mod_path}.{cls_name}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
