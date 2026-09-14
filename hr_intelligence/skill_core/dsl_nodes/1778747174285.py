import json


def main(status=None, message=None, count=0, record=None, query=None):
    try:
       if isinstance(record, str):
        record = json.loads(record) if record.strip() else []
       else:
        record = record or []
    except Exception:
        record = []

    status = str(status or "")
    message = str(message or "")

    try:
        count = int(count or 0)
    except Exception:
        count = 0

    if status == "success":
        lines = [f"查询成功，符合条件的人数：{count} 人。"]
        if not record:
            return {"text": "\n".join(lines)}

        # One person: display the returned safe fields as a concise profile.
        if count == 1 and len(record) == 1:
            lines.append("\n人员信息：")
            for field, value in record[0].items():
                display = str(value).strip() if value not in (None, "") else "暂无数据"
                lines.append(f"- {field}：{display}")
            return {"text": "\n".join(lines)}

        # Multiple people: list every matched person once, without repeated status/count columns.
        lines.append("\n人员名单：")
        for index, item in enumerate(record, start=1):
            name = str(item.get("姓名") or item.get("员工姓名") or item.get("人员姓名") or "未提供姓名").strip()
            details = []
            for field in ["一级部门", "二级部门", "部门", "所属部门", "岗位", "职位", "人员类型", "公司主体"]:
                value = item.get(field)
                if value not in (None, ""):
                    details.append(str(value).strip())
            suffix = f"（{'｜'.join(details)}）" if details else ""
            lines.append(f"{index}. {name}{suffix}")
        return {"text": "\n".join(lines)}

    if status == "unsupported":
      return {
        "text": f"无法查询。\n原因：{message}"
     }

    if status == "not_found":
        return {"text": f"未查询到符合条件的人员。\n原因：{message}"}

    return {"text": f"查询失败。\n原因：{message}"}
