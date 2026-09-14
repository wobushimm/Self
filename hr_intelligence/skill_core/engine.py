"""人力智算本地执行引擎。

本模块是 Skill 的核心调度引擎，完全在本地运行，不连接任何外部平台。
原工作流的 Python 代码节点在本地 dsl_nodes 中加载执行；
两个 LLM 节点（字段识别、招聘优化计划）由确定性 Python 解析器替代。
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from skill_core.dsl_nodes import node_main

_SENSITIVE = (
    "身份证", "证件号码", "证件号", "护照", "手机号", "手机号码", "联系电话",
    "电话号码", "家庭地址", "家庭住址", "详细地址", "住址", "银行卡", "银行账号",
    "银行账户", "工资", "月薪", "年薪", "薪资", "薪酬", "奖金",
)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("|", "｜").replace("\r", " ").replace("\n", " ")


def _extract_excel_text(file_path: Path) -> str:
    """模拟原 Excel 提取器输出，连续展开工作簿内全部 Sheet。"""
    if file_path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("本地版当前支持 .xlsx 和 .xlsm 文件。")
    workbook = load_workbook(file_path, data_only=True, read_only=True)
    lines: list[str] = []
    try:
        for sheet in workbook.worksheets:
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                values = [_cell_text(value) for value in row]
                while values and not values[-1]:
                    values.pop()
                if values:
                    lines.append(f"Row {row_number}: " + " | ".join(values))
    finally:
        workbook.close()
    return "\n".join(lines)


def _contains_sensitive(query: str) -> bool:
    return any(word in query for word in _SENSITIVE)


def _query_intent(query: str, candidates: list[dict[str, Any]]) -> str:
    if _contains_sensitive(query):
        return "unsupported"
    if any(word in query for word in ("多少人", "几人", "人数", "数量", "总数")):
        return "count"
    name_hit = any(item.get("field") in {"姓名", "员工姓名", "人员姓名"} for item in candidates)
    if name_hit and any(word in query for word in ("详情", "信息", "资料", "情况", "学历", "年龄", "岗位", "部门", "入职")):
        return "detail"
    if any(word in query for word in ("详情", "详细信息", "个人信息", "人员信息")) and name_hit:
        return "detail"
    return "list"


def _field_alias(query: str, headers: list[str]) -> Optional[str]:
    for header in sorted(headers, key=len, reverse=True):
        if header and header in query:
            return header
    aliases = {
        "职位": ("岗位", "职位", "职务"),
        "部门": ("部门", "所属部门", "一级部门", "二级部门"),
        "司龄": ("司龄(年)", "司龄"),
        "入职时间": ("入职日期", "入职时间"),
    }
    for phrase, choices in aliases.items():
        if phrase in query:
            available = [field for field in choices if field in headers]
            if len(available) == 1:
                return available[0]
    return None


def _explicit_conditions(text: str, headers: list[str]) -> list[dict[str, str]]:
    conditions: list[dict[str, str]] = []
    def clean_value(value: str) -> str:
        value = value.strip()
        value = re.sub(r"(?:的)?(?:人员|员工|名单)?(?:有多少人|多少人|有哪些人|有哪些|有谁|分别是谁|详情|信息)?$", "", value)
        return value.strip()
    compare_words = {
        "不少于": "gte", "大于等于": "gte", "至少": "gte", "不低于": "gte",
        "不超过": "lte", "小于等于": "lte", "至多": "lte", "不高于": "lte",
        "大于": "gt", "超过": "gt", "高于": "gt", "晚于": "gt", "之后": "gt",
        "小于": "lt", "低于": "lt", "早于": "lt", "之前": "lt",
    }
    # 后置比较词：数字在前、比较词在后（如"3年以上""30及以上""2020年之前"）
    post_compare_words = {
        "及以上": "gte", "及以下": "lte", "以上": "gte", "以下": "lte",
        "之前": "lt", "之后": "gt", "以后": "gt", "起": "gte", "后": "gt",
    }
    # 别名分组：用户常用说法 → 实际表头名
    _ALIAS_GROUPS = [
        ("司龄(年)", ("司龄(年)", "司龄")),
        ("入职时间", ("入职日期", "入职时间")),
        ("岗位", ("岗位", "职位", "职务")),
    ]
    alias_to_header: dict[str, str] = {}
    for actual_header, aliases in _ALIAS_GROUPS:
        if actual_header in headers:
            for alias in aliases:
                alias_to_header[alias] = actual_header

    def _find_header_in_text(header: str) -> Optional[tuple[int, int]]:
        """在文本中查找表头或其别名，返回 (起始位置, 匹配文本长度)；优先最长匹配。"""
        best_pos: Optional[int] = None
        best_len = 0
        if header in text:
            best_pos = text.find(header)
            best_len = len(header)
        for alias, mapped in alias_to_header.items():
            if mapped == header and alias != header and alias in text:
                pos = text.find(alias)
                if len(alias) > best_len or best_pos is None:
                    best_pos = pos
                    best_len = len(alias)
        if best_pos is None:
            return None
        return (best_pos, best_len)

    for header in sorted(headers, key=len, reverse=True):
        if not header or _contains_sensitive(header):
            continue
        found = _find_header_in_text(header)
        if found is None:
            continue
        pos, match_len = found
        tail = text[pos + match_len:]
        fuzzy = re.match(r"\s*(?:、|，|,)*\s*(?:均|都)?\s*(?:中|里|名称中)?\s*(?:包含|含有|带有|带着|带|有)\s*([^，,。；;或、的]+)", tail)
        if fuzzy:
            value = clean_value(fuzzy.group(1))
            if value:
                conditions.append({"field": header, "operator": "contains", "value": value})
                continue
        exact = re.match(r"\s*(?:、|，|,)*\s*(?:均|都)?\s*(?:为|是|等于|完全等于|[:：])\s*([^，,。；;或、的]+)", tail)
        if exact:
            raw_value = exact.group(1).strip()
            # 检查值末尾是否附带后置比较词（如"B以上" → value="B", op="gte"）
            split_done = False
            for pw in sorted(post_compare_words, key=len, reverse=True):
                if raw_value.endswith(pw) and len(raw_value) > len(pw):
                    actual_value = clean_value(raw_value[:-len(pw)])
                    if actual_value:
                        conditions.append({"field": header, "operator": post_compare_words[pw], "value": actual_value})
                        split_done = True
                        break
            if split_done:
                continue
            if not split_done:
                value = clean_value(raw_value)
                if value:
                    conditions.append({"field": header, "operator": "equals", "value": value})
                    continue
        # 前置比较词：比较词在前、数字在后（如"大于3年""至少30岁""不低于5年"）
        matched_pre = False
        for phrase, operator in compare_words.items():
            hit = re.match(
                rf"\s*(?:在|从)?\s*{re.escape(phrase)}\s*((?:\d{{4}}[-/.年]\d{{1,2}}(?:[-/.月]\d{{1,2}}[日号]?)?)|(?:-?\d+(?:\.\d+)?(?:岁|年|月|元)?))",
                tail,
            )
            if hit:
                conditions.append({"field": header, "operator": operator, "value": hit.group(1)})
                matched_pre = True
                break
        if matched_pre:
            continue
        # 后置比较词：数字在前、比较词在后（如"3年以上""30及以上""2020年之前"）
        post_hit = re.match(
            r"\s*(?:在|从)?\s*((?:\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}[日号]?)?)|(?:-?\d+(?:\.\d+)?))\s*(?:岁|年|月|元|号)?(及以上|及以下|以上|以下|之前|之后|以后|起|后)",
            tail,
        )
        if post_hit:
            value = post_hit.group(1).strip()
            operator = post_compare_words.get(post_hit.group(2), "gte")
            if value:
                conditions.append({"field": header, "operator": operator, "value": value})
    return conditions


def _dedupe_conditions(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen = set()
    for item in items:
        field = str(item.get("field", "")).strip()
        value = str(item.get("value", "")).strip()
        operator = str(item.get("operator", "equals")).strip().lower()
        key = (field, operator, value)
        if field and value and key not in seen:
            seen.add(key)
            result.append({"field": field, "operator": operator, "value": value})
    return result


def _conditions_for_segment(
    segment: str,
    employees: list[dict[str, Any]],
    headers: list[str],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    candidate_result = node_main("1784186520243", query=segment, employees=employees, headers=headers)
    try:
        candidates = json.loads(candidate_result.get("candidate_json") or "[]")
    except Exception:
        candidates = []
    usable = [item for item in candidates if item.get("match_type") != "ambiguous_value_match"]
    conditions = _explicit_conditions(segment, headers)
    conditions.extend(usable)
    return _dedupe_conditions(conditions), candidates


def _expand_group_shorthands(query: str, headers: list[str]) -> str:
    """展开查询中的分组简写，如"三年考核等级"→"2022年考核等级、2023年考核等级、2024年考核等级"。

    检测模式：
    - "N年X" → 找到所有形如 "YEAR+X" 的表头，用"、"连接替换简写
    - "所有X" → 同上
    """
    # 按年份前缀分组表头：suffix → [year_headers]
    year_groups: dict[str, list[str]] = {}
    for h in headers:
        m = re.match(r"^(\d{4})年(.+)$", h)
        if m:
            suffix = m.group(2)
            year_groups.setdefault(suffix, []).append(h)
    # 每组按年份排序
    for suffix in year_groups:
        year_groups[suffix].sort()

    # 替换"N年X"或"所有X"简写
    # 模式1: "N年X" — N是中文数字或阿拉伯数字
    cn_nums = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
    for suffix, group_headers in year_groups.items():
        # "N年suffix" 或 "所有suffix"
        for pattern in [
            rf"([一二两三四五\d]+)年{re.escape(suffix)}",
            rf"所有{re.escape(suffix)}",
        ]:
            m = re.search(pattern, query)
            if m:
                expanded = "、".join(group_headers)
                query = query[:m.start()] + expanded + query[m.end():]
                break  # 同一 suffix 只替换一次
    return query


def _expand_shared_value_query(query: str, headers: list[str]) -> str:
    """展开"X、Y、Z均为V"共享值模式。

    将 ``"2022年考核等级、2023年考核等级、2024年考核等级均为B+"``
    展开为 ``"2022年考核等级为B+，2023年考核等级为B+，2024年考核等级为B+"``，
    使后续 _explicit_conditions 能正确解析每个字段的条件。
    """
    # 找到查询中出现的所有表头（按长度降序，避免短表头误匹配长表头的子串）
    found_headers: list[tuple[int, int, str]] = []  # (start, end, header)
    for h in sorted(headers, key=len, reverse=True):
        if not h or _contains_sensitive(h):
            continue
        start = 0
        while True:
            idx = query.find(h, start)
            if idx < 0:
                break
            # 检查是否与已找到的区间重叠
            overlaps = any(not (idx >= e or idx + len(h) <= s) for s, e, _ in found_headers)
            if not overlaps:
                found_headers.append((idx, idx + len(h), h))
            start = idx + 1
    if len(found_headers) < 2:
        return query
    # 按位置排序
    found_headers.sort(key=lambda x: x[0])
    # 查找连续由"、"连接且后接"均为/都为/全是"的表头序列
    for keyword in ("均为", "都为", "全是"):
        kw_idx = query.find(keyword)
        if kw_idx < 0:
            continue
        # 向前查找由"、"连接且直达关键词的表头链
        # 策略：按位置正序遍历，检查相邻表头间是否只有"、"
        chain: list[str] = []
        for i in range(len(found_headers) - 1, -1, -1):
            s, e, h = found_headers[i]
            if i == len(found_headers) - 1:
                # 最后一个表头：其结尾到关键词之间应无内容
                if query[e:kw_idx].strip() != "":
                    break
                chain.append(h)
            else:
                # 非最后表头：其结尾到下一个表头开头之间应只有"、"
                next_s = found_headers[i + 1][0]
                gap = query[e:next_s].strip()
                if gap == "、":
                    chain.append(h)
                else:
                    break
        chain.reverse()
        if len(chain) < 2:
            continue
        # 向后提取共享值
        val_start = kw_idx + len(keyword)
        val_match = re.match(r"\s*([^，,。且]+)", query[val_start:])
        if not val_match:
            continue
        value = val_match.group(1).strip()
        # 构造展开文本
        first_start = found_headers[0][0]
        expanded = "，".join(f"{h}为{value}" for h in chain)
        return query[:first_start] + expanded + query[val_start + val_match.end():]
    return query


def _build_query_plan(
    query: str,
    employees: list[dict[str, Any]],
    headers: list[str],
) -> tuple[dict[str, Any], str]:
    """替代原 Dify 工作流中的"字段识别"LLM 节点，用确定性规则生成查询计划。"""
    # 预处理 1: 展开分组简写（如"三年考核等级"→"2022年考核等级、2023年考核等级、2024年考核等级"）
    query = _expand_group_shorthands(query, headers)
    # 预处理 2: 展开"X、Y、Z均为V"共享值模式
    query = _expand_shared_value_query(query, headers)

    full_candidate = node_main("1784186520243", query=query, employees=employees, headers=headers)
    candidate_json = full_candidate.get("candidate_json", "[]")
    try:
        all_candidates = json.loads(candidate_json or "[]")
    except Exception:
        all_candidates = []

    requested = [field for field in headers if field and field in query and not _contains_sensitive(field)]
    intent = _query_intent(query, all_candidates)
    if intent == "list":
        for name in ("姓名", "员工姓名", "人员姓名"):
            if name in headers and name not in requested:
                requested.insert(0, name)
                break

    parts = [part.strip() for part in re.split(r"(?:，|,)?\s*(?:或者|或|任一|其中之一)\s*", query) if part.strip()]
    if len(parts) > 1:
        groups = []
        for part in parts:
            conditions, _ = _conditions_for_segment(part, employees, headers)
            if conditions:
                groups.append({"conditions": conditions})
        plan = {"intent": intent, "conditions": [], "condition_groups": groups, "requested_fields": requested}
    else:
        conditions, _ = _conditions_for_segment(query, employees, headers)
        plan = {"intent": intent, "conditions": conditions, "condition_groups": [], "requested_fields": requested}
    if not plan["conditions"] and not plan["condition_groups"]:
        plan["intent"] = "unsupported"
    # 过滤掉 ambiguous_value_match，避免下游节点因歧义值直接报错
    safe_candidates = [
        c for c in all_candidates
        if c.get("match_type") != "ambiguous_value_match"
    ]
    safe_json = json.dumps(safe_candidates, ensure_ascii=False)
    return plan, safe_json


def _number_with_unit(raw: str, unit: str = "") -> float:
    value = float(raw)
    return value * 10000 if unit in {"万", "万元"} else value


def _extract_override(query: str, label: str, aliases: tuple[str, ...], ratio: bool = False) -> Optional[float]:
    for alias in aliases:
        hit = re.search(rf"{re.escape(alias)}(?:为|是|按|[:：])?\s*([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元|%|％)?", query)
        if hit:
            value = _number_with_unit(hit.group(1), hit.group(2) or "")
            if ratio and hit.group(2) in {"%", "％"}:
                value /= 100
            return value
    return None


def _build_hire_plan(query: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """替代原 Dify 工作流中的"生成招聘优化计划"LLM 节点，用确定性规则生成招聘计划。"""
    employees = [row for row in rows if isinstance(row, dict) and not str(row.get("姓名", "")).startswith(("待招", "待优化"))]
    budget_hit = re.search(r"(?:预算|预算结余)(?:为|是|有|[:：])?\s*([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)?", query)
    budget = _number_with_unit(budget_hit.group(1), budget_hit.group(2) or "") if budget_hit else 0.0

    override_specs = {
        "基本工资": (("基本工资", "月薪"), False),
        "交通补贴": (("交通补贴",), False),
        "专业证书补贴": (("专业证书补贴", "证书补贴"), False),
        "单位社保公积金月金额": (("单位社保公积金月金额", "社保公积金月金额"), False),
        "单位社保公积金比例": (("单位社保公积金比例", "社保公积金比例"), True),
        "餐补": (("餐补",), False),
        "租房补贴": (("租房补贴",), False),
        "非货币性福利": (("非货币性福利",), False),
        "探亲福利费": (("探亲福利费",), False),
        "专利奖金": (("专利奖金",), False),
        "补偿金": (("补偿金",), False),
        "体检费": (("体检费",), False),
        "团建费": (("团建费",), False),
        "补充医疗": (("补充医疗",), False),
        "入职月份": (("入职月份", "月份入职"), False),
        "在岗月数": (("在岗月数",), False),
        "绩效核算月数": (("绩效核算月数",), False),
        "绩效折扣比例": (("绩效折扣比例", "绩效折扣"), True),
        "社保公积金年度上涨比例": (("社保公积金年度上涨比例", "社保上涨比例"), True),
        "工会经费计提比例": (("工会经费计提比例", "工会比例"), True),
    }
    overrides = []
    for label, (aliases, ratio) in override_specs.items():
        value = _extract_override(query, label, aliases, ratio)
        if value is not None:
            overrides.append({"字段": label, "数值": value})

    departments: list[dict[str, Any]] = []
    known = []
    for level in ("二级部门", "一级部门"):
        for row in employees:
            value = str(row.get(level, "") or "").strip()
            if value and (level, value) not in known:
                known.append((level, value))
    known.sort(key=lambda item: len(item[1]), reverse=True)

    count_patterns = list(re.finditer(r"(?:新增|增员|待招|招聘|招|扩编|补充)\s*([0-9]+)\s*(?:名|人|个)", query))
    general_count = int(count_patterns[0].group(1)) if count_patterns else 0
    matched_names = [(level, name) for level, name in known if name in query]
    if not matched_names and general_count and employees:
        primary_values = sorted({str(row.get("一级部门", "") or "").strip() for row in employees if row.get("一级部门")})
        if len(primary_values) == 1:
            matched_names = [("一级部门", primary_values[0])]

    job_values = sorted({str(row.get("岗位", "") or "").strip() for row in employees if row.get("岗位")}, key=len, reverse=True)
    job = next((value for value in job_values if value in query), "")
    if not job:
        job_hit = re.search(r"(?:招聘|招)\s*[0-9]+\s*(?:名|人|个)?\s*([^，,。；;]+?)(?:人员|员工|，|,|。|；|;|$)", query)
        if job_hit:
            job = job_hit.group(1).strip()
    if job in {"人", "人员", "员工", "名", "个"}:
        job = ""

    for level, name in matched_names:
        local = re.search(rf"{re.escape(name)}[^，,。；;]{{0,12}}?(?:新增|增员|待招|招聘|招|扩编|补充)\s*([0-9]+)\s*(?:名|人|个)", query)
        need = int(local.group(1)) if local else general_count
        if need <= 0:
            continue
        sample = next((row for row in employees if str(row.get(level, "") or "").strip() == name), {})
        entry_hit = re.search(r"([1-9]|1[0-2])\s*月(?:份)?入职", query)
        entry = int(entry_hit.group(1)) if entry_hit else 1
        perf_hit = re.search(r"绩效核算月数(?:为|是|[:：])?\s*([0-9]+)", query)
        perf = int(perf_hit.group(1)) if perf_hit else 0
        schedules = [{"建议入职月份": entry, "建议在岗月数": 13 - entry, "绩效核算月数": perf, "说明": "按用户条件和底表样本测算", "指定参数": []} for _ in range(need)]
        departments.append({
            "一级部门": name if level == "一级部门" else str(sample.get("一级部门", "") or ""),
            "二级部门": name if level == "二级部门" else "",
            "待招人数": need,
            "岗位偏好": job,
            "学历要求": "",
            "人员类型": "",
            "公司主体": "",
            "费用类别": "",
            "priority": "high" if "优先" in query else "medium",
            "说明": "由本地规则从用户请求提取",
            "指定参数": [],
            "schedules": schedules,
        })

    warnings = []
    if budget <= 0:
        warnings.append("未识别到有效预算结余，预算约束按0元处理。")
    if not departments:
        warnings.append("未识别到同时包含部门和招聘人数的招聘需求。")
    return {
        "budget_balance": budget,
        "全局指定参数": overrides,
        "departments": departments,
        "recommendations": ["优先安排可比样本充分且预算可覆盖的岗位。"],
        "warnings": warnings,
    }


def _safe_sheet_title(title: str, used: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "-", title).strip()[:31] or "结果"
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"-{index}"
        candidate = base[: 31 - len(suffix)] + suffix
        index += 1
    used.add(candidate)
    return candidate


def _parse_markdown_value(value: str) -> Any:
    text = value.strip()
    compact = text.replace(",", "")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", compact):
        number = float(compact)
        return int(number) if number.is_integer() else number
    return text


def _markdown_to_xlsx(markdown: str, output_path: Path) -> None:
    """把原 md_exporter 的标题/表格输入转换为多 Sheet Excel。"""
    sections: list[tuple[str, list[str]]] = []
    title = "结果"
    lines: list[str] = []
    for raw in markdown.splitlines():
        if raw.startswith("# "):
            if lines:
                sections.append((title, lines))
            title, lines = raw[2:].strip(), []
        else:
            lines.append(raw)
    if lines or not sections:
        sections.append((title, lines))

    workbook = Workbook()
    workbook.remove(workbook.active)
    used: set[str] = set()
    for section_title, section_lines in sections:
        sheet = workbook.create_sheet(_safe_sheet_title(section_title, used))
        table_lines = [line for line in section_lines if line.strip().startswith("|")]
        rows = []
        for line in table_lines:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            rows.append([_parse_markdown_value(cell) for cell in cells])
        if not rows:
            sheet.append([section_title])
            sheet.append(["未生成表格数据"])
        else:
            for row in rows:
                sheet.append(row)
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="4472C4")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            sheet.row_dimensions[1].height = 36
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for row in sheet.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                        header = str(sheet.cell(1, cell.column).value or "")
                        if any(word in header for word in ("序号", "人数", "计数项")):
                            cell.number_format = "#,##0"
                        else:
                            cell.number_format = "#,##0.00"
        for column in range(1, sheet.max_column + 1):
            values = [str(sheet.cell(row, column).value or "") for row in range(1, min(sheet.max_row, 200) + 1)]
            sheet.column_dimensions[get_column_letter(column)].width = min(max(max(map(len, values), default=8) + 2, 10), 32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def _write_query_result_xlsx(records: list[dict], output_path: Path) -> None:
    """将人员查询结果写入 Excel 文件。"""
    if not records:
        return
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "查询结果"
    # 表头
    headers = list(records[0].keys())
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4472C4")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 36
    # 数据行
    for rec in records:
        sheet.append([_cell_text(rec.get(h)) for h in headers])
    # 格式
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in range(1, sheet.max_column + 1):
        values = [str(sheet.cell(row, column).value or "") for row in range(1, min(sheet.max_row, 200) + 1)]
        sheet.column_dimensions[get_column_letter(column)].width = min(max(max(map(len, values), default=8) + 2, 10), 32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def run_chatflow(
    file_path: str | Path,
    query: str,
    conversation_id: str = "",
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    user: Optional[str] = None,
    output_dir: str | Path = "output",
) -> dict[str, Any]:
    """核心调度入口：意图路由 → Excel 解析 → 三路分流处理 → 输出文件。

    原工作流节点映射：
    - route_intent: 意图识别（代码节点）
    - 1784166032256: 底表解析与成本计算（代码节点）
    - 1784186520243: 候选条件识别（代码节点）
    - 1784166258491: 字段识别 → 由 _build_query_plan() 确定性替代
    - 1783912721113: 动态查询执行（代码节点）
    - 1778747174285: 查询结果汇总展示（代码节点）
    - cost_query: 成本预算五表输出（代码节点）
    - hire_prepare + hire_llm: 招聘计划 → 由 _build_hire_plan() 确定性替代
    - hire_build: 招聘明细与建议生成（代码节点）
    """
    del base_url, api_key, user
    source = Path(file_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"文件不存在：{source}")
    query = str(query or "").strip()
    if not query:
        raise ValueError("查询内容不能为空。")

    # ── Step 1: 意图路由（对应 route_intent 节点） ──
    route = node_main("route_intent", query=query)["route"]

    # ── Step 2: 底表解析与成本计算（对应 1784166032256 节点） ──
    excel_text = _extract_excel_text(source)
    calculated = node_main("1784166032256", excel_text=excel_text, query=query)
    if calculated.get("status") != "success":
        raise RuntimeError(calculated.get("message") or "人员底表解析失败。")

    files: list[str] = []
    raw: dict[str, Any] = {"route": route, "calculation": calculated.get("message")}
    out = Path(output_dir).expanduser().resolve()

    # ── Step 3: 三路分流处理 ──
    if route == "info":
        # ── 人员信息检索 ──
        plan, candidate_json = _build_query_plan(query, calculated["employees"], calculated["headers"])
        query_result = node_main(
            "1783912721113",
            employees=calculated["employees"],
            headers=calculated["headers"],
            structured_output=plan,
            candidate_json=candidate_json,
            query=query,
        )
        answer = node_main(
            "1778747174285",
            status=query_result.get("status"),
            message=query_result.get("message"),
            count=query_result.get("count", 0),
            record=query_result.get("record", ""),
            query=query,
        )["text"]
        # 查询成功时输出结果 Excel
        if query_result.get("status") == "success":
            try:
                raw_records = query_result.get("record", "")
                if isinstance(raw_records, str):
                    parsed_records = json.loads(raw_records) if raw_records.strip() else []
                else:
                    parsed_records = raw_records or []
                if parsed_records:
                    result_xlsx = out / "查询结果.xlsx"
                    _write_query_result_xlsx(parsed_records, result_xlsx)
                    files.append(str(result_xlsx))
            except Exception as _xlsx_err:
                import logging as _log_mod
                _log_mod.getLogger("agent.hr_intelligence").warning(
                    f"查询结果 Excel 生成失败: {_xlsx_err}")
        raw.update({"query_plan": plan, "query_result": query_result})
    elif route == "cost":
        # ── 人力成本预算导出 ──
        markdown = node_main(
            "cost_query",
            budget_records=calculated["budget_records"],
            query=query,
            detail_headers=calculated["headers"],
            detail_keys=calculated["detail_keys"],
        )["result"]
        target = out / "人力成本预算.xlsx"
        _markdown_to_xlsx(markdown, target)
        files.append(str(target))
        answer = f"已生成 {len(calculated['budget_records'])} 条人员成本明细及预算汇总。"
    else:
        # ── 招聘优化 ──
        plan = _build_hire_plan(query, calculated["budget_records"])
        markdown = node_main(
            "hire_build",
            budget_records=calculated["budget_records"],
            plan=plan,
            query=query,
            detail_headers=calculated["headers"],
            detail_keys=calculated["detail_keys"],
            source_status=calculated["status"],
            source_message=calculated["message"],
        )["result"]
        target = out / "招聘优化方案.xlsx"
        _markdown_to_xlsx(markdown, target)
        files.append(str(target))
        answer = "已按预算、部门需求、用户明确参数和底表可比样本生成招聘优化方案。"
        raw["hire_plan"] = plan

    return {
        "answer": answer,
        "conversation_id": conversation_id or f"local-{uuid.uuid4().hex[:12]}",
        "files": files,
        "raw": raw,
    }
