import json
import re

def main(budget_records=None, plan=None, query=None, detail_headers=None, detail_keys=None, source_status=None, source_message=None):
    try: rows = json.loads(budget_records) if isinstance(budget_records, str) else (budget_records or [])
    except Exception: rows = []
    try: plan = json.loads(plan) if isinstance(plan, str) else (plan or {})
    except Exception: plan = {}

    def text(v): return "" if v is None else str(v).strip()
    def num(v):
        try: return float(v or 0)
        except Exception: return 0.0
    def median(group, field, include_zero=False):
        values = sorted(num(row.get(field)) for row in group if include_zero or num(row.get(field)) > 0)
        return values[len(values)//2] if values else 0.0
    def mode(group, field):
        counts = {}
        for row in group:
            value = text(row.get(field))
            if value: counts[value] = counts.get(value, 0) + 1
        return sorted(counts, key=lambda value: (-counts[value], value))[0] if counts else ""
    def bounded_int(v, low, high, default):
        value = int(num(v))
        return value if low <= value <= high else default
    def md(v): return text(v).replace("|", "｜").replace("\n", " ")
    def table(headers, body):
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"]*len(headers)) + "|"]
        lines += ["| " + " | ".join(md(v) for v in row) + " |" for row in body]
        return "\n".join(lines)

    employees = [r for r in rows if isinstance(r, dict) and not text(r.get("姓名")).startswith("待招") and not text(r.get("姓名")).startswith("待优化")]
    query_text = text(query)

    def parse_budget(value):
        hit = re.search(r"(?:今年)?(?:新增)?预算(?:结余)?(?:为|是|有|[:：])?\s*([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)?", value)
        if not hit: return None
        amount = float(hit.group(1))
        return amount*10000 if hit.group(2) in ("万", "万元") else amount

    def parse_requests(value):
        result = []
        pattern = r"([一-龥A-Za-z0-9（）()·_-]{1,30}?(?:部|中心|室|组))\s*(?:计划)?待招\s*([0-9]+)\s*人"
        for hit in re.finditer(pattern, value):
            name = hit.group(1).strip("，,。；;、 ")
            name = {"研发部":"研发中心"}.get(name, name)
            for sep in ("，", ",", "。", "；", ";", "、"):
                if sep in name: name = name.split(sep)[-1].strip()
            secondary = [r for r in employees if text(r.get("二级部门")) == name]
            primary = [r for r in employees if text(r.get("一级部门")) == name]
            result.append({"一级部门": text(secondary[0].get("一级部门")) if secondary else (name if primary else ""), "二级部门": name if secondary or not primary else "", "待招人数": int(hit.group(2))})
        return result

    budget_from_query = parse_budget(query_text)
    if budget_from_query is not None: plan["budget_balance"] = budget_from_query
    hard_requests = parse_requests(query_text)
    model_departments = plan.get("departments") or []
    model_by_name = {text(x.get("二级部门") or x.get("一级部门")): x for x in model_departments if isinstance(x, dict)}
    requests = []
    for hard in hard_requests:
        model = model_by_name.get(hard["二级部门"] or hard["一级部门"], {})
        merged = dict(model); merged.update(hard)
        merged["priority"] = text(model.get("priority")).lower() if text(model.get("priority")).lower() in ("high", "medium", "low") else "medium"
        merged["schedules"] = model.get("schedules") or []
        requests.append(merged)
    if not requests: requests = [x for x in model_departments if isinstance(x, dict)]

    def peer_group(req):
        d1, d2, job = text(req.get("一级部门")), text(req.get("二级部门")), text(req.get("岗位偏好"))
        levels = []
        if d2 and job: levels.append(([r for r in employees if text(r.get("二级部门")) == d2 and text(r.get("岗位")) == job], "同二级部门同岗位"))
        if d2: levels.append(([r for r in employees if text(r.get("二级部门")) == d2], "同二级部门"))
        if d1 and job: levels.append(([r for r in employees if text(r.get("一级部门")) == d1 and text(r.get("岗位")) == job], "同一级部门同岗位"))
        if d1: levels.append(([r for r in employees if text(r.get("一级部门")) == d1], "同一级部门"))
        for group, label in levels:
            if group: return group, label
        return [], "无可比样本"

    allowed_overrides = {"基本工资","交通补贴","专业证书补贴","单位社保公积金月金额","单位社保公积金比例","餐补","租房补贴","非货币性福利","探亲福利费","专利奖金","补偿金","体检费","团建费","补充医疗","入职月份","在岗月数","绩效核算月数","绩效折扣比例","社保公积金年度上涨比例","工会经费计提比例"}
    def clean_overrides(value):
        # v6紧凑格式：[{"字段":"基本工资","数值":18000}]；同时兼容v5对象格式。
        if isinstance(value, list):
            result = {}
            for item in value:
                if not isinstance(item, dict): continue
                key, raw = text(item.get("字段")), item.get("数值")
                if key in allowed_overrides and raw is not None and str(raw).strip() != "": result[key] = num(raw)
            return result
        if isinstance(value, dict):
            return {k:num(v) for k,v in value.items() if k in allowed_overrides and v is not None and str(v).strip() != ""}
        return {}

    global_overrides = clean_overrides(plan.get("全局指定参数"))

    def profile(req, group, schedule):
        overrides = dict(global_overrides)
        overrides.update(clean_overrides(req.get("指定参数")))
        overrides.update(clean_overrides(schedule.get("指定参数")))
        entry_default = bounded_int(schedule.get("建议入职月份"), 1, 12, 1)
        entry = bounded_int(overrides.get("入职月份", entry_default), 1, 12, entry_default)
        if "在岗月数" in overrides and "入职月份" not in overrides:
            months = bounded_int(overrides["在岗月数"], 1, 12, 13-entry)
            entry = 13-months
        else:
            months = 13-entry
        perf_default = bounded_int(schedule.get("绩效核算月数"), 0, months, 0)
        perf_months = bounded_int(overrides.get("绩效核算月数", perf_default), 0, months, perf_default)

        # 经常性月度参数采用可比人员中位数；不直接复用现有“人工成本合计”。
        basic = median(group, "基本工资(元)")
        transport = median(group, "交通补贴", True)
        certificate = median(group, "专业证书补贴", True)
        meal_month = median(group, "餐补（输入）", True)
        rent_month = median(group, "租房补贴", True)
        discount = median(group, "绩效折扣比例", True)
        growth = median(group, "社保公积金年度上涨比例") or 1.0
        company = text(req.get("公司主体")) or mode(group, "公司主体")
        union_rate = median(group, "工会经费计提比例", True)
        if union_rate <= 0: union_rate = 0.016 if "北京" in company else (0.02 if "重庆" in company else 0)

        # 用户指定值覆盖底表参考值；只覆盖明确出现的字段。
        basic = overrides.get("基本工资", basic)
        transport = overrides.get("交通补贴", transport)
        certificate = overrides.get("专业证书补贴", certificate)
        meal_month = overrides.get("餐补", meal_month)
        rent_month = overrides.get("租房补贴", rent_month)
        discount = overrides.get("绩效折扣比例", discount)
        growth = overrides.get("社保公积金年度上涨比例", growth)
        union_rate = overrides.get("工会经费计提比例", union_rate)

        # 按公司/同类人员政策取典型固定值；偶发奖金和离职补偿不进入基准招聘成本。
        checkup = median(group, "体检费（输入）", True)
        team = median(group, "团建费（输入）", True)
        medical = median(group, "补充医疗（输入）", True)
        patent = overrides.get("专利奖金", 0.0)
        compensation = overrides.get("补偿金", 0.0)
        visit_default = 0.0 if ("北京" in company or "重庆" in company) else median(group, "探亲福利费（输入）", True)
        noncash_default = 0.0 if ("北京" in company or "重庆" in company) else (median(group, "非货币性福利", True) or 3000.0)
        visit = overrides.get("探亲福利费", visit_default)
        noncash = overrides.get("非货币性福利", noncash_default)
        checkup = overrides.get("体检费", checkup)
        team = overrides.get("团建费", team)
        medical = overrides.get("补充医疗", medical)

        if "单位社保公积金月金额" in overrides:
            social_month = overrides["单位社保公积金月金额"]
        else:
            social_month = basic*overrides.get("单位社保公积金比例", 0.48)
        salary = (basic+transport+certificate)*months
        social = social_month*months*growth
        performance = basic*perf_months*discount
        transfer = rent_month*months
        meal = meal_month*months
        union = (salary+performance+patent)*union_rate
        total = salary+social+performance+transfer+noncash+meal+visit+patent+checkup+team+medical+union+compensation
        return locals()

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    departments, slots = [], []
    for req_index, req in enumerate(requests):
        need = max(0, int(num(req.get("待招人数"))))
        group, reference = peer_group(req)
        schedules = req.get("schedules") or []
        departments.append({"req": req, "need": need, "group": group, "reference": reference})
        for person_index in range(need):
            schedule = schedules[person_index] if person_index < len(schedules) and isinstance(schedules[person_index], dict) else {"建议入职月份": 1, "建议在岗月数": 12, "绩效核算月数": 0, "说明": "模型未提供时间时按1月入职测算"}
            p = profile(req, group, schedule) if group else None
            slots.append((priority_rank.get(text(req.get("priority")).lower(), 1), person_index, req_index, p, schedule))

    slots.sort(key=lambda x: (x[0], x[1], x[2]))
    budget = max(0, num(plan.get("budget_balance"))); remaining = budget
    selected = []; counts = {i: 0 for i in range(len(departments))}
    for _, _, req_index, p, schedule in slots:
        if p and p["total"] <= remaining+0.000001:
            remaining -= p["total"]; counts[req_index] += 1; selected.append((req_index, p, schedule))

    keys = detail_keys if isinstance(detail_keys, list) and detail_keys else []
    headers = detail_headers if isinstance(detail_headers, list) and len(detail_headers) == len(keys) else keys
    if "人工成本合计" in keys:
        limit = keys.index("人工成本合计")+1; keys = keys[:limit]; headers = headers[:limit]
    if not keys:
        keys = headers = ["序号", "姓名", "一级部门", "二级部门", "岗位", "基本工资(元)", "在岗月数", "人工成本合计"]

    detail_body=[]; per_department={i:0 for i in range(len(departments))}
    for seq,(req_index,p,schedule) in enumerate(selected,1):
        per_department[req_index]+=1; info=departments[req_index]; req=info["req"]; group=info["group"]
        d1,d2=text(req.get("一级部门")),text(req.get("二级部门"))
        record={
            "序号":seq,"姓名":f"待招-{d2 or d1}-{per_department[req_index]:02d}","年龄":"","一级部门":d1,"二级部门":d2,
            "岗位":text(req.get("岗位偏好")) or mode(group,"岗位"),"学历":text(req.get("学历要求")) or mode(group,"学历"),
            "性别":"","政治面貌":"","司龄(年)":"","入职日期":f"建议{p['entry']}月入职","民族":"",
            "人员类型":text(req.get("人员类型")) or mode(group,"人员类型"),"公司主体":p["company"],
            "费用类别":text(req.get("费用类别")) or mode(group,"费用类别"),"基本工资(元)":p["basic"],"交通补贴":p["transport"],
            "专业证书补贴":p["certificate"],"单位社保公积金(元)":p["social_month"],"餐补（输入）":p["meal_month"],
            "租房补贴":p["rent_month"],"探亲福利费（输入）":p["visit"],"专利奖金（输入）":p["patent"],"体检费（输入）":p["checkup"],
            "团建费（输入）":p["team"],"补充医疗（输入）":p["medical"],"补偿金（输入）":p["compensation"],"在岗月数":p["months"],
            "绩效核算月数":p["perf_months"],"绩效折扣比例":p["discount"],"社保公积金年度上涨比例":p["growth"],
            "工会经费计提比例":p["union_rate"],"年度税前工资（含基本工资、交补、专业证书补贴）":p["salary"],
            "年度单位社保公积金合计":p["social"],"年度绩效（8折）":p["performance"],"转移重庆租房福利":p["transfer"],
            "非货币性福利":p["noncash"],"餐补":p["meal"],"探亲福利费":p["visit"],"专利奖金":p["patent"],"体检费":p["checkup"],
            "团建费":p["team"],"补充医疗":p["medical"],"工会经费":p["union"],"补偿金":p["compensation"],"人工成本合计":p["total"]}
        detail_body.append([record.get(key,"") for key in keys])

    summary_headers=["一级部门","二级部门","岗位","待招人数","建议招聘人数","未满足人数","参考样本数","参考范围","优先级","预计人工成本"]
    summary_body=[]
    for i,info in enumerate(departments):
        req=info["req"]; cost=sum(p["total"] for idx,p,_ in selected if idx==i)
        summary_body.append([text(req.get("一级部门")),text(req.get("二级部门")),text(req.get("岗位偏好")),info["need"],counts[i],info["need"]-counts[i],len(info["group"]),info["reference"],text(req.get("priority")),round(cost,2)])

    advice=[["新增预算结余",round(budget,2)],["预计人工成本",round(budget-remaining,2)],["预计剩余预算",round(remaining,2)],["待招总人数",sum(x["need"] for x in departments)],["建议招聘总人数",len(selected)],["计算范围","当前只计算人工成本合计，暂不计算业务线投入。"],["参数覆盖口径","用户指定值优先；未指定项参考底表；年度结果和人工成本合计自动重算。"]]
    for i,item in enumerate(plan.get("recommendations") or [],1): advice.append([f"优化建议{i}",item])
    if not employees: advice.extend([["底表解析状态",text(source_status) or "error"],["底表解析信息",text(source_message) or "未获得现有人员记录"]])
    if not selected: advice.append(["结果提示","当前预算、建议入职时间或参考成本下没有可执行招聘名额。"])
    return {"result":"\n\n".join(["# 模拟待招人员明细\n"+table(headers,detail_body or [["无可执行招聘方案"]+[""]*(len(headers)-1)]),"# 部门招聘汇总\n"+table(summary_headers,summary_body or [["未识别部门"]+[""]*(len(summary_headers)-1)]),"# 人工成本口径与招聘建议\n"+table(["项目","内容"],advice)])}
