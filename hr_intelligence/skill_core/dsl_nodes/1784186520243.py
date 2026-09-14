import json
import re


def main(query=None, employees=None, headers=None):
    """
    根据用户问题和 Excel 真实数据确定候选查询条件。

    支持：
    - 直接说部门名称，例如“查询市场部人员”
    - 明确字段，例如“二级部门为研发一部”
    - 层级表达，例如“研发中心下研发一部”
    - 日期、数值、AND / OR 等条件交给后续查询计划节点解析
    """

    query = str(query or "").strip()
    employees = employees or []
    headers = headers or []

    def format_candidates(candidates):
        if not candidates:
            return "没有找到可由Excel真实数据直接确认的条件。"

        lines = [
            "以下条件由Excel真实数据直接匹配得到，",
            "属于确定性事实，不允许LLM修改："
        ]

        for index, item in enumerate(candidates, start=1):
            lines.append(
                f"{index}. 字段：{item.get('field', '')}；"
                f"值：{item.get('value', '')}；"
                f"匹配方式：{item.get('match_type', '')}"
            )

        return "\n".join(lines)

    def result(status, message, candidates=None):
        candidates = candidates or []

        return {
            "status": status,
            "message": message,
            "candidate_count": len(candidates),
            "candidate_json": json.dumps(
                candidates,
                ensure_ascii=False
            ),
            "candidate_text": format_candidates(candidates)
        }

    if not query:
        return result("error", "用户问题为空。")

    if not isinstance(employees, list):
        return result("error", "人员数据格式错误。")

    if not employees:
        return result("error", "没有可用于识别查询条件的人员数据。")

    if not isinstance(headers, list):
        return result("error", "Excel表头格式错误。")

    # 不允许通过人员检索返回的敏感字段
    sensitive_keywords = [
        "身份证",
        "证件号",
        "手机号",
        "手机号码",
        "联系电话",
        "电话号码",
        "家庭地址",
        "家庭住址",
        "住址",
        "银行卡",
        "银行账号",
        "工资",
        "薪资",
        "薪酬",
        "奖金"
    ]

    def is_sensitive(field):
        field = str(field or "")
        return any(keyword in field for keyword in sensitive_keywords)

    # 这些字段不适合直接按“值”识别，避免误匹配
    excluded_value_fields = {
        "序号",
        "年龄",
        "司龄",
        "司龄(年)",
        "入职日期"
    }

    # 清理真实表头
    real_fields = []

    for header in headers:
        field = str(header or "").strip()

        if not field:
            continue

        if field not in real_fields:
            real_fields.append(field)

    # 字段 -> 值集合；值 -> 字段集合
    field_value_index = {}
    value_field_index = {}

    for field in real_fields:
        if is_sensitive(field):
            continue

        if field in excluded_value_fields:
            continue

        values = set()

        for employee in employees:
            if not isinstance(employee, dict):
                continue

            value = str(employee.get(field, "")).strip()

            if not value:
                continue

            # 纯数字不参与普通文本匹配。
            # 防止“2023-01-01”中的数字被误识别为其他字段值。
            if re.fullmatch(r"\d+(?:\.\d+)?", value):
                continue

            # 单字值不参与普通匹配，性别后续单独处理
            if len(value) < 2:
                continue

            values.add(value)

        field_value_index[field] = values

        for value in values:
            value_field_index.setdefault(value, []).append(field)

    candidates = []
    forced_values = set()

    def add_candidate(field, value, match_type, confidence=1.0):
        candidates.append({
            "field": field,
            "operator": "equals",
            "value": value,
            "match_type": match_type,
            "confidence": confidence
        })

    # ==================================================
    # 1. 用户明确写出字段名时，字段名优先
    # 例如：二级部门为研发一部
    # ==================================================
    for field, values in field_value_index.items():
        field_pattern = re.compile(
            re.escape(field)
            + r"\s*(?:为|是|等于|：|:)\s*"
        )

        for field_match in field_pattern.finditer(query):
            value_start = field_match.end()

            for value in sorted(values, key=len, reverse=True):
                if query.startswith(value, value_start):
                    add_candidate(
                        field,
                        value,
                        "explicit_field_match"
                    )

                    forced_values.add(value)
                    break

    # ==================================================
    # 2. 一级部门下二级部门的层级识别
    # 例如：研发中心下研发一部
    # ==================================================
    first_dept_field = "一级部门"
    second_dept_field = "二级部门"

    if (
        first_dept_field in field_value_index
        and second_dept_field in field_value_index
    ):
        hierarchy_pairs = set()

        for employee in employees:
            if not isinstance(employee, dict):
                continue

            first_dept = str(
                employee.get(first_dept_field, "")
            ).strip()

            second_dept = str(
                employee.get(second_dept_field, "")
            ).strip()

            if first_dept and second_dept:
                hierarchy_pairs.add(
                    (first_dept, second_dept)
                )

        for first_dept, second_dept in hierarchy_pairs:
            hierarchy_pattern = re.compile(
                re.escape(first_dept)
                + r"\s*(?:下|下面|下属)\s*"
                + re.escape(second_dept)
            )

            if hierarchy_pattern.search(query):
                add_candidate(
                    first_dept_field,
                    first_dept,
                    "hierarchy_match"
                )

                add_candidate(
                    second_dept_field,
                    second_dept,
                    "hierarchy_match"
                )

                forced_values.add(first_dept)
                forced_values.add(second_dept)

    # ==================================================
    # 3. 普通真实值匹配
    # ==================================================
    matched_spans = []

    for value in sorted(
        value_field_index.keys(),
        key=len,
        reverse=True
    ):
        if value in forced_values:
            continue

        start = query.find(value)

        if start < 0:
            continue

        end = start + len(value)

        covered = any(
            start >= old_start and end <= old_end
            for old_start, old_end in matched_spans
        )

        if covered:
            continue

        fields = value_field_index[value]

        if len(fields) == 1:
            add_candidate(
                fields[0],
                value,
                "exact_value_match"
            )

            matched_spans.append((start, end))

        else:
            # 同名部门同时出现在一级、二级部门时，
            # 默认按一级部门查询，使“查询市场部人员”可直接使用。
            department_fields = {"一级部门", "二级部门"}

            if set(fields).issubset(department_fields):
                preferred_field = (
                    "一级部门"
                    if "一级部门" in fields
                    else "二级部门"
                )

                add_candidate(
                    preferred_field,
                    value,
                    "department_default_match"
                )

                matched_spans.append((start, end))

            else:
                candidates.append({
                    "field": "",
                    "operator": "equals",
                    "value": value,
                    "possible_fields": fields,
                    "match_type": "ambiguous_value_match",
                    "confidence": 0.0
                })

    # ==================================================
    # 4. 性别单字匹配
    # ==================================================
    gender_fields = [
        field
        for field in real_fields
        if field in ["性别", "员工性别"]
    ]

    if len(gender_fields) == 1:
        gender_field = gender_fields[0]

        gender_patterns = {
            "男": [
                r"男性",
                r"男员工",
                r"男职工",
                r"男生"
            ],
            "女": [
                r"女性",
                r"女员工",
                r"女职工",
                r"女生"
            ]
        }

        for gender_value, patterns in gender_patterns.items():
            if any(
                re.search(pattern, query)
                for pattern in patterns
            ):
                add_candidate(
                    gender_field,
                    gender_value,
                    "gender_pattern_match"
                )

    # ==================================================
    # 5. 去重
    # ==================================================
    unique_candidates = []
    seen = set()

    for item in candidates:
        key = (
            item.get("field", ""),
            item.get("operator", ""),
            item.get("value", "")
        )

        if key not in seen:
            seen.add(key)
            unique_candidates.append(item)

    candidates = unique_candidates

    # ==================================================
    # 6. 只有未解决的字段歧义才报错
    # ==================================================
    ambiguous = [
        item
        for item in candidates
        if item.get("match_type")
        == "ambiguous_value_match"
    ]

    if ambiguous:
        descriptions = []

        for item in ambiguous:
            descriptions.append(
                f'“{item["value"]}”可能属于字段：'
                + "、".join(
                    item.get("possible_fields", [])
                )
            )

        return result(
            "ambiguous",
            "；".join(descriptions),
            candidates
        )

    if not candidates:
        return result(
            "no_match",
            "未在用户问题中匹配到Excel真实字段值。",
            []
        )

    return result(
        "success",
        f"已确定识别{len(candidates)}个查询条件。",
        candidates
    )