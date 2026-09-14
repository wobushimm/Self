def main(query=None):
    text = str(query or "").strip()
    hire_words = ["新增人员", "增员", "招聘优化", "招聘计划", "待招人数", "待招聘", "预算结余", "招聘人数", "人员扩编", "扩编"]
    cost_words = ["人力成本", "人工成本", "成本", "薪酬总额", "年度总薪酬", "费用合计", "预算"]
    if any(word in text for word in hire_words):
        route = "hire"
        label = "动态招聘规划"
    elif any(word in text for word in cost_words):
        route = "cost"
        label = "人力成本预算"
    else:
        route = "info"
        label = "人员信息检索"
    return {"route": route, "route_label": label, "message": "已识别为" + label}
