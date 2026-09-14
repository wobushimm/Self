import json
import re
import unicodedata
from datetime import datetime


def main(
    employees=None,
    headers=None,
    structured_output=None,
    candidate_json=None,
    query=None
):
    """
    动态人员信息查询。

    数据优先级：
    1. Excel真实数据；
    2. 候选条件识别代码节点；
    3. 字段识别LLM。

    主要能力：
    1. 动态识别Excel表头；
    2. 精确查询；
    3. 模糊包含查询；
    4. 数字和日期比较；
    5. 人员详情查询；
    6. 敏感字段过滤。
    """

    # ==============================
    # 0. 统一返回格式
    # ==============================
    def response(
        message,
        status,
        count=0,
        record=None
    ):
        return {
            "message": str(message),
            "status": str(status),
            "count": int(count),
            "record": ""
        }

    # 公司工作室将大列表以 JSON 字符串传入；本地列表输入也兼容。
    try:
      if isinstance(employees, str):
        employees = json.loads(employees) if employees.strip() else []
      else:  
        employees = employees or []
    except Exception:
      return response(
        "人员数据解析失败：employees 不是有效的 JSON 数据。",
        "error"
    )

    try:
     if isinstance(headers, str):
        headers = json.loads(headers) if headers.strip() else []
     else:
        headers = headers or []
    except Exception:
     return response(
        "Excel表头解析失败：headers 不是有效的 JSON 数据。",
        "error"
    )

    structured_output = structured_output or {}
    query = str(query or "").strip()

    # ==============================
    # 2. 检查人员数据
    # ==============================
    if not isinstance(employees, list):
        return response(
            "人员数据格式错误，employees必须是数组。",
            "error"
        )

    if not employees:
        return response(
            "没有可查询的人员数据。",
            "error"
        )

    valid_employees = []

    for employee in employees:
        if isinstance(employee, dict):
            valid_employees.append(employee)

    employees = valid_employees

    if not employees:
        return response(
            "人员数据中没有有效记录。",
            "error"
        )

    # ==============================
    # 3. 检查并清理Excel表头
    # ==============================
    if not isinstance(headers, list):
        return response(
            "Excel表头格式错误，headers必须是数组。",
            "error"
        )

    clean_headers = []

    for header in headers:
        clean_header = str(header).strip()

        if (
            clean_header
            and clean_header not in clean_headers
        ):
            clean_headers.append(clean_header)

    headers = clean_headers

    if not headers:
        return response(
            "没有识别到Excel表头。",
            "error"
        )

    # ==============================
    # 4. 兼容structured_output为JSON字符串
    # ==============================
    if isinstance(structured_output, str):
        structured_text = structured_output.strip()

        structured_text = (
            structured_text
            .replace("```json", "")
            .replace("```JSON", "")
            .replace("```", "")
            .strip()
        )

        if not structured_text:
            structured_output = {}
        else:
            try:
                structured_output = json.loads(
                    structured_text
                )
            except Exception:
                return response(
                    "字段识别结果不是有效的JSON格式。",
                    "error"
                )

    if not isinstance(structured_output, dict):
        return response(
            "字段识别结果格式错误。",
            "error"
        )

    # ==============================
    # 5. 解析candidate_json
    # ==============================
    authoritative_candidates = []

    if candidate_json:
        if isinstance(candidate_json, str):
            candidate_text = candidate_json.strip()

            if candidate_text:
                try:
                    authoritative_candidates = json.loads(
                        candidate_text
                    )
                except Exception:
                    return response(
                        "候选条件不是有效的JSON格式。",
                        "error"
                    )

        elif isinstance(candidate_json, list):
            authoritative_candidates = candidate_json

        else:
            return response(
                "候选条件格式错误。",
                "error"
            )

    if not isinstance(
        authoritative_candidates,
        list
    ):
        return response(
            "候选条件必须是数组。",
            "error"
        )

    # ==============================
    # 6. 检查候选条件是否存在歧义
    # ==============================
    ambiguous_candidates = []

    for candidate in authoritative_candidates:
        if not isinstance(candidate, dict):
            continue

        if (
            candidate.get("match_type")
            == "ambiguous_value_match"
        ):
            ambiguous_candidates.append(candidate)

    if ambiguous_candidates:
        descriptions = []

        for candidate in ambiguous_candidates:
            value = str(
                candidate.get("value", "")
            ).strip()

            possible_fields = candidate.get(
                "possible_fields",
                []
            )

            if not isinstance(possible_fields, list):
                possible_fields = []

            field_text = "、".join(
                str(field)
                for field in possible_fields
            )

            descriptions.append(
                f"“{value}”可能属于字段：{field_text}"
            )

        return response(
            "无法唯一确定查询字段。"
            + "；".join(descriptions)
            + "。请在问题中明确说明字段名称。",
            "error"
        )

    # ==============================
    # 7. 提取可信候选条件
    # ==============================
    confirmed_conditions = []

    allowed_candidate_types = {
        "exact_value_match",
        "gender_pattern_match"
    }

    for candidate in authoritative_candidates:
        if not isinstance(candidate, dict):
            continue

        match_type = str(
            candidate.get("match_type", "")
        ).strip()

        if match_type not in allowed_candidate_types:
            continue

        field = str(
            candidate.get("field", "")
        ).strip()

        operator = str(
            candidate.get(
                "operator",
                "equals"
            )
        ).strip().lower()

        value = str(
            candidate.get("value", "")
        ).strip()

        if not field or not value:
            continue

        confirmed_condition = {
            "field": field,
            "operator": operator,
            "value": value
        }

        if confirmed_condition not in confirmed_conditions:
            confirmed_conditions.append(
                confirmed_condition
            )

    # ==============================
    # 8. 获取LLM查询计划
    # ==============================
    intent = str(
        structured_output.get("intent", "")
    ).strip().lower()

    allowed_intents = {
        "detail",
        "list",
        "count"
    }

    if intent == "unsupported":
        if confirmed_conditions:
            intent = "list"
        else:
            return response(
                "问题中涉及的字段不存在、不允许查询，或无法确定查询条件。",
                "error"
            )

    if intent not in allowed_intents:
        return response(
            "未识别到有效查询意图。",
            "error"
        )

    llm_conditions = structured_output.get(
        "conditions",
        []
    )

    # 复合OR：组内AND，组间OR；普通conditions保持全AND语义。
    raw_condition_groups = structured_output.get(
        "condition_groups",
        []
    )

    if raw_condition_groups is None:
        raw_condition_groups = []

    if not isinstance(raw_condition_groups, list):
        return response(
            "复合OR条件格式错误，condition_groups必须是数组。",
            "error"
        )

    if raw_condition_groups and llm_conditions:
        return response(
            "使用condition_groups时，conditions必须为空数组。",
            "error"
        )

    has_or_groups = bool(raw_condition_groups)

    if has_or_groups:
        llm_conditions = []

        for group_index, group in enumerate(
            raw_condition_groups,
            start=1
        ):
            if not isinstance(group, dict):
                return response(
                    f"第{group_index}个OR条件组格式错误。",
                    "error"
                )

            group_conditions = group.get("conditions", [])

            if (
                not isinstance(group_conditions, list)
                or not group_conditions
            ):
                return response(
                    f"第{group_index}个OR条件组必须包含conditions数组。",
                    "error"
                )

            for group_condition in group_conditions:
                if not isinstance(group_condition, dict):
                    return response(
                        f"第{group_index}个OR条件组存在无效条件。",
                        "error"
                    )

                group_condition = dict(group_condition)
                group_condition["_or_group"] = group_index
                llm_conditions.append(group_condition)

    requested_fields = structured_output.get(
        "requested_fields",
        []
    )

    if llm_conditions is None:
        llm_conditions = []

    if requested_fields is None:
        requested_fields = []

    if not isinstance(llm_conditions, list):
        return response(
            "查询条件格式错误，conditions必须是数组。",
            "error"
        )

    if not isinstance(requested_fields, list):
        requested_fields = []

    # ==============================
    # 9. 候选条件覆盖LLM冲突条件
    # ==============================
    # OR分支不能把候选条件盲目追加到任一分支，否则会改变“或”的语义。
    merged_conditions = (
        []
        if has_or_groups
        else list(confirmed_conditions)
    )

    discarded_llm_fields = set()

    for llm_condition in llm_conditions:
        if not isinstance(llm_condition, dict):
            continue

        llm_field = str(
            llm_condition.get("field", "")
        ).strip()

        llm_operator = str(
            llm_condition.get(
                "operator",
                "equals"
            )
        ).strip().lower()

        llm_value = str(
            llm_condition.get("value", "")
        ).strip()

        if not llm_field or not llm_value:
            continue

        llm_normalized = {
            "field": llm_field,
            "operator": llm_operator,
            "value": llm_value
        }

        if "_or_group" in llm_condition:
            llm_normalized["_or_group"] = llm_condition["_or_group"]

        # 与确定性条件完全一致时不重复添加
        if llm_normalized in merged_conditions:
            continue

        conflicts_with_candidate = False

        for confirmed in confirmed_conditions:
            confirmed_field = confirmed["field"]
            confirmed_value = confirmed["value"]

            value_overlaps = (
                llm_value in confirmed_value
                or confirmed_value in llm_value
            )

            # 相似值被LLM放到了其他字段
            if (
                value_overlaps
                and llm_field != confirmed_field
            ):
                conflicts_with_candidate = True
                discarded_llm_fields.add(
                    llm_field
                )
                break

            # 同一字段，但LLM修改了真实完整值
            if (
                llm_field == confirmed_field
                and value_overlaps
                and llm_value != confirmed_value
            ):
                conflicts_with_candidate = True
                discarded_llm_fields.add(
                    llm_field
                )
                break

        if conflicts_with_candidate:
            continue

        if llm_normalized not in merged_conditions:
            merged_conditions.append(
                llm_normalized
            )

    conditions = merged_conditions

    if not conditions:
        return response(
            "未识别到有效查询条件。",
            "error"
        )

    # ==============================
    # 10. 敏感字段限制
    # ==============================
    sensitive_keywords = [
        "身份证",
        "证件号码",
        "证件号",
        "护照",
        "手机号",
        "手机号码",
        "联系电话",
        "电话号码",
        "家庭地址",
        "家庭住址",
        "详细地址",
        "住址",
        "银行卡",
        "银行账号",
        "银行账户",
        "工资",
        "月薪",
        "年薪",
        "薪资",
        "薪酬",
        "奖金"
    ]

    def is_sensitive(field):
        field_text = str(
            field or ""
        ).strip()

        return any(
            keyword in field_text
            for keyword in sensitive_keywords
        )
         # 用户问题中直接要求敏感信息时，停止查询，不再降级为普通人员查询
    matched_sensitive_fields = [
        keyword
        for keyword in sensitive_keywords
        if keyword in query
    ]

    if matched_sensitive_fields:
        return response(
            "“{}”属于敏感信息，系统不支持查询或返回该字段。".format(
                "、".join(matched_sensitive_fields)
            ),
            "unsupported"
        )
    # ==============================
    # 11. 允许的查询操作
    # ==============================
    allowed_operators = {
        "equals",
        "contains",
        "gte",
        "lte",
        "gt",
        "lt"
    }

    # ==============================
    # 12. 文本标准化
    # ==============================
    def normalize_text(value):
        text = unicodedata.normalize(
            "NFKC",
            str(value if value is not None else "")
        )
        return text.replace("\ufeff", "").replace("\u200b", "").strip()

    def normalize_match_text(value):
        """
        用于模糊匹配：

        1. 统一全角和半角；
        2. 忽略英文大小写；
        3. 忽略空格、换行和制表符。

        不进行同义词替换、拼音匹配、
        错别字纠正或语义推断。
        """
        text = unicodedata.normalize(
            "NFKC",
            str(
                value
                if value is not None
                else ""
            )
        ).casefold()

        return re.sub(
            r"\s+",
            "",
            text
        )

    # ==============================
    # 13. 获取字段中的真实值
    # ==============================
    def get_field_values(field):
        values = []

        for employee in employees:
            actual_value = normalize_text(
                employee.get(field, "")
            )

            if actual_value:
                values.append(actual_value)

        return values

    # ==============================
    # 14. 校验查询字段和值
    # ==============================
    safe_conditions = []

    for condition_index, condition in enumerate(
        conditions,
        start=1
    ):
        if not isinstance(condition, dict):
            return response(
                f"第{condition_index}个查询条件格式错误。",
                "error"
            )

        field = str(
            condition.get("field", "")
        ).strip()

        operator = str(
            condition.get(
                "operator",
                "equals"
            )
        ).strip().lower()

        value = str(
            condition.get("value", "")
        ).strip()

        if not field:
            return response(
                f"第{condition_index}个查询条件缺少字段名称。",
                "error"
            )

        if field not in headers:
            return response(
                f"Excel中不存在字段“{field}”。"
                f"当前可用字段为：{'、'.join(headers)}。",
                "error"
            )

        if is_sensitive(field):
            return response(
                f"字段“{field}”属于敏感信息，不能查询。",
                "error"
            )

        if operator not in allowed_operators:
            return response(
                f"不支持查询操作“{operator}”。",
                "error"
            )

        if not value:
            return response(
                f"字段“{field}”缺少查询值。",
                "error"
            )

        field_values = get_field_values(field)

        if not field_values:
            return response(
                f"Excel字段“{field}”中没有可查询的数据。",
                "error"
            )

        # --------------------------
        # equals精确值存在性校验
        # --------------------------
        if operator == "equals":
            # Use the same Unicode/whitespace normalization as the final filter.
            value_key = normalize_match_text(value)
            existing_values = set(field_values)
            existing_value_keys = {
                normalize_match_text(actual_value): actual_value
                for actual_value in existing_values
            }

            if value_key not in existing_value_keys:
                similar_values = [
                    actual_value
                    for actual_value in existing_values
                    if (
                        value_key in normalize_match_text(actual_value)
                        or normalize_match_text(actual_value) in value_key
                    )
                ]

                similar_values = sorted(
                    similar_values
                )[:5]

                if similar_values:
                    return response(
                        f"Excel字段“{field}”中不存在完整值"
                        f"“{value}”。"
                        f"可能相关的真实值为："
                        f"{'、'.join(similar_values)}。",
                        "error"
                    )

                return response(
                    f"Excel字段“{field}”中不存在值"
                    f"“{value}”。"
                    "字段识别结果可能有误。",
                    "error"
                )

        # --------------------------
        # contains模糊值存在性校验
        # --------------------------
        if operator == "contains":
            normalized_target = normalize_match_text(
                value
            )

            if not normalized_target:
                return response(
                    f"字段“{field}”缺少有效的模糊查询关键词。",
                    "error"
                )

            contains_match = any(
                normalized_target
                in normalize_match_text(actual_value)
                for actual_value in field_values
            )

            if not contains_match:
                return response(
                    f"Excel字段“{field}”中没有包含"
                    f"“{value}”的数据。",
                    "not_found"
                )

        normalized_condition = {
            "field": field,
            "operator": operator,
            "value": value
        }

        if "_or_group" in condition:
            normalized_condition["_or_group"] = condition["_or_group"]

        if normalized_condition not in safe_conditions:
            safe_conditions.append(
                normalized_condition
            )

    if not safe_conditions:
        return response(
            "未识别到有效查询条件。",
            "error"
        )

    # ==============================
    # 15. 数字和日期提取
    # ==============================
    def extract_number(value):
        """
        支持：
        25
        25岁
        4.8年
        """
        text = normalize_text(value)

        match = re.search(
            r"-?\d+(?:\.\d+)?",
            text
        )

        if not match:
            return None

        try:
            return float(match.group())
        except Exception:
            return None

    def extract_date(value):
        """
        解析常见日期格式。
        """
        text = normalize_text(value)

        date_formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y.%m.%d",
            "%Y年%m月%d日",
            "%Y-%m",
            "%Y/%m",
            "%Y年%m月"
        ]

        for date_format in date_formats:
            try:
                return datetime.strptime(
                    text,
                    date_format
                )
            except Exception:
                continue

        return None

    # ==============================
    # 16. 比较函数
    # ==============================
    def compare(
        actual,
        operator,
        target
    ):
        actual_text = normalize_text(actual)
        target_text = normalize_text(target)

        # Exact queries ignore Unicode form and invisible whitespace differences.
        if operator == "equals":
            return normalize_match_text(actual_text) == normalize_match_text(target_text)

        # 模糊查询使用标准化包含匹配
        if operator == "contains":
            normalized_actual = normalize_match_text(
                actual_text
            )
            normalized_target = normalize_match_text(
                target_text
            )

            return (
                bool(normalized_target)
                and normalized_target in normalized_actual
            )

        # 日期比较
        actual_date = extract_date(actual_text)
        target_date = extract_date(target_text)

        if (
            actual_date is not None
            and target_date is not None
        ):
            if operator == "gte":
                return actual_date >= target_date

            if operator == "lte":
                return actual_date <= target_date

            if operator == "gt":
                return actual_date > target_date

            if operator == "lt":
                return actual_date < target_date

        # 数字比较
        actual_number = extract_number(actual_text)
        target_number = extract_number(target_text)

        if (
            actual_number is not None
            and target_number is not None
        ):
            if operator == "gte":
                return actual_number >= target_number

            if operator == "lte":
                return actual_number <= target_number

            if operator == "gt":
                return actual_number > target_number

            if operator == "lt":
                return actual_number < target_number

        # 比较值必须同为可解析日期或数值，禁止退化为字符串比较。
        # 这样可避免"9"被错误判定为大于"10"。
        
        # 考核等级比较：A > B+ > B > C+ > C > D+ > D > F
        _GRADE_RANK = {
            "A+": 13, "A": 12, "A-": 11,
            "B+": 10, "B": 9, "B-": 8,
            "C+": 7, "C": 6, "C-": 5,
            "D+": 4, "D": 3, "D-": 2,
            "F": 1, "E": 1,
        }
        actual_grade = _GRADE_RANK.get(actual_text.strip())
        target_grade = _GRADE_RANK.get(target_text.strip())
        if actual_grade is not None and target_grade is not None:
            if operator == "gte":
                return actual_grade >= target_grade
            if operator == "lte":
                return actual_grade <= target_grade
            if operator == "gt":
                return actual_grade > target_grade
            if operator == "lt":
                return actual_grade < target_grade
        
        return False

    # ==============================
    # 17. 执行人员筛选
    # ==============================
    matched_people = []

    grouped_conditions = {}

    for condition in safe_conditions:
        group_id = condition.get("_or_group", 0)
        grouped_conditions.setdefault(
            group_id,
            []
        ).append(condition)

    for employee in employees:
        # 同一组的条件全部满足；任一组满足即命中。
        matched = any(
            all(
                compare(
                    employee.get(condition["field"], ""),
                    condition["operator"],
                    condition["value"]
                )
                for condition in group_conditions
            )
            for group_conditions in grouped_conditions.values()
        )

        if matched:
            matched_people.append(employee)

    # ==============================
    # 18. 没有查询结果
    # ==============================
    if not matched_people:
        operator_text_map = {
            "equals": "等于",
            "contains": "包含",
            "gte": "大于等于",
            "lte": "小于等于",
            "gt": "大于",
            "lt": "小于"
        }

        condition_text = "、".join(
            f'{condition["field"]}'
            f'{operator_text_map.get(condition["operator"], condition["operator"])}'
            f'“{condition["value"]}”'
            for condition in safe_conditions
        )

        return response(
            f"未查询到符合以下条件的人员："
            f"{condition_text}。",
            "not_found"
        )

    total_count = len(matched_people)

    #==============================
    # 19. 人数统计
    # ==============================
    if intent == "count":
        msg = f"共查询到{total_count}名符合条件的人员。"
        return {
            "status": "success",
            "message": msg,
            "count": total_count,
            "record": json.dumps(matched_people, ensure_ascii=False)
        }


    # ==============================
    # 20. 清理并确定返回字段
    # ==============================
    safe_requested_fields = []

    # ------------------------------
    # 20.1 综合详情查询
    # ------------------------------
    # 用户询问“人员信息、详细信息、详情”等，
    # 自动返回全部真实且非敏感的Excel字段。
    detail_all_field_keywords = [
        "人员信息",
        "个人信息",
        "详细信息",
        "完整信息",
        "基本信息",
        "人员详情",
        "个人详情",
        "完整资料",
        "详细资料",
        "个人资料",
        "全部信息",
        "所有信息"
    ]

    wants_all_detail_fields = (
        intent == "detail"
        and any(
            keyword in query
            for keyword in detail_all_field_keywords
        )
    )

    if wants_all_detail_fields:
        for header in headers:
            field = str(header).strip()

            if not field:
                continue

            if is_sensitive(field):
                continue

            if field not in safe_requested_fields:
                safe_requested_fields.append(field)

    # ------------------------------
    # 20.2 接收LLM识别的返回字段
    # ------------------------------
    for requested_field in requested_fields:
        field = str(
            requested_field
        ).strip()

        if not field:
            continue

        if field in discarded_llm_fields:
            continue

        if field not in headers:
            continue

        if is_sensitive(field):
            continue

        if field not in safe_requested_fields:
            safe_requested_fields.append(field)

    # ------------------------------
    # 20.3 从用户问题中识别真实表头
    # ------------------------------
    for header in headers:
        field = str(header).strip()

        if not field:
            continue

        if is_sensitive(field):
            continue

        if field in query:
            if field not in safe_requested_fields:
                safe_requested_fields.append(field)

    # ------------------------------
    # 20.4 常见表达和Excel字段映射
    # ------------------------------
    field_alias_groups = {
        "年龄": [
            "年龄",
            "员工年龄",
            "人员年龄"
        ],
        "部门": [
            "部门",
            "所属部门",
            "所在部门"
        ],
        "岗位": [
            "岗位",
            "职位",
            "职务",
            "岗位名称"
        ],
        "学历": [
            "学历",
            "最高学历"
        ],
        "政治面貌": [
            "政治面貌"
        ],
        "司龄": [
            "司龄",
            "司龄(年)",
            "工作年限"
        ],
        "入职日期": [
            "入职日期",
            "入职时间"
        ]
    }

    query_aliases = {
        "年龄": [
            "年龄",
            "多大",
            "几岁",
            "多少岁"
        ],
        "部门": [
            "部门",
            "哪个部门",
            "哪里工作"
        ],
        "岗位": [
            "岗位",
            "职位",
            "职务",
            "做什么工作"
        ],
        "学历": [
            "学历",
            "什么学历",
            "最高学历"
        ],
        "政治面貌": [
            "政治面貌",
            "党员情况"
        ],
        "司龄": [
            "司龄",
            "工作几年",
            "工作多少年"
        ],
        "入职日期": [
            "入职日期",
            "入职时间",
            "什么时候入职",
            "哪年入职"
        ]
    }

    for standard_field, aliases in query_aliases.items():
        query_mentions_field = any(
            alias in query
            for alias in aliases
        )

        if not query_mentions_field:
            continue

        possible_headers = field_alias_groups.get(
            standard_field,
            []
        )

        matched_header = None

        for possible_header in possible_headers:
            if possible_header in headers:
                matched_header = possible_header
                break

        if (
            matched_header
            and not is_sensitive(matched_header)
            and matched_header not in safe_requested_fields
        ):
            safe_requested_fields.append(
                matched_header
            )

    # ==============================
    # 21. 候选条件字段加入展示字段
    # ==============================
    for condition in safe_conditions:
        candidate_field = condition["field"]

        if (
            candidate_field in headers
            and not is_sensitive(candidate_field)
            and candidate_field
            not in safe_requested_fields
        ):
            safe_requested_fields.append(
                candidate_field
            )
    for org_field in ["一级部门", "二级部门"]:
      if (
          org_field in headers
          and not is_sensitive(org_field)
          and org_field not in safe_requested_fields
      ):
          safe_requested_fields.append(org_field)        



    # ==============================
    # 22. 自动寻找姓名字段
    # ==============================
    possible_name_fields = [
        "姓名",
        "员工姓名",
        "人员姓名",
        "名字",
        "员工名称",
        "人员名称"
    ]

    name_field = None

    for possible_field in possible_name_fields:
        if possible_field in headers:
            name_field = possible_field
            break

    # 名单和详情默认包含姓名
    if (
        name_field
        and name_field not in safe_requested_fields
    ):
        safe_requested_fields.insert(
            0,
            name_field
        )

    # ==============================
    # 23. 默认展示字段
    # ==============================
    if not safe_requested_fields:
        default_fields = [
            "姓名",
            "员工姓名",
            "人员姓名",
            "部门",
            "所属部门",
            "岗位",
            "职位",
            "职务"
        ]

        for default_field in default_fields:
            if (
                default_field in headers
                and not is_sensitive(default_field)
                and default_field
                not in safe_requested_fields
            ):
                safe_requested_fields.append(
                    default_field
                )

    if not safe_requested_fields:
        return response(
            "未识别到允许展示的字段。",
            "error"
        )

    # ==============================
    # 24. 构造安全输出记录
    # ==============================
    output_records = []

    for employee in matched_people:
        output_record = {}

        for field in safe_requested_fields:
            output_record[field] = normalize_text(
                employee.get(field, "")
            )

        output_records.append(output_record)

    #==============================
    # 25. 构造查询说明
    # ==============================
    message = (
        f"共查询到{total_count}名符合条件的人员。"
    )

    return {
        "status": "success",
        "message": message,
        "count": total_count,
        "record": json.dumps(output_records, ensure_ascii=False)
    }
