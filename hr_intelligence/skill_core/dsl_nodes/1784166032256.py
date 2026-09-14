import re

def main(excel_text=None, query=None):
    if not excel_text:
        return {"status":"error","message":"请上传人员输入底表。","headers":[],"detail_keys":[],"employees":[],"budget_records":[]}

    # 兼容Excel提取插件可能返回的三种文本格式：
    # 1. Row 3: A | B | C
    # 2. | A | B | C |（Markdown表格）
    # 3. A\tB\tC（制表符分隔）
    rows=[]
    sequence=0
    for raw_line in str(excel_text).splitlines():
        line=raw_line.strip()
        if not line:
            continue

        values=None
        row_number=None
        row_match=re.match(r"^Row\s*(\d+)\s*[:：]\s*(.*)$",line,re.I)
        if row_match:
            row_number=int(row_match.group(1))
            values=[x.strip() for x in row_match.group(2).strip().strip("|").split("|")]
        elif "|" in line:
            values=[x.strip() for x in line.strip("|").split("|")]
        elif "\t" in line:
            values=[x.strip() for x in line.split("\t")]

        if not values:
            continue

        # 跳过Markdown表头下方的 --- 分隔行。
        normalized=[re.sub(r"[:\-\s]","",str(x)) for x in values]
        if values and all(not item for item in normalized):
            continue

        sequence+=1
        rows.append((row_number or sequence,values))

    header_index=next((i for i,(_,row) in enumerate(rows) if "姓名" in row),-1)
    if header_index<0:
        return {"status":"error","message":"未识别到包含“姓名”的表头。","headers":[],"detail_keys":[],"employees":[],"budget_records":[]}

    headers=rows[header_index][1]
    required=["序号","姓名","司龄(年)","公司主体","基本工资(元)","交通补贴","专业证书补贴","餐补","租房补贴","探亲福利费","专利奖金","体检费","团建费","补充医疗","补偿金"]
    missing=[x for x in required if x not in headers]
    if missing:
        return {"status":"error","message":"输入表缺少字段："+"、".join(missing),"headers":[],"detail_keys":[],"employees":[],"budget_records":[]}

    index={}
    for i,name in enumerate(headers):
        if name not in index:
            index[name]=i

    def get(values,name,default=""):
        return values[index[name]] if name in index else default

    def num(value):
        try:
            return float(str(value or "").replace(",","").replace("元","").replace("%","").strip() or 0)
        except:
            return 0.0

    def blank(value):
        return str(value or "").strip() in {"","-","\\","无","None"}

    input_headers=["序号","姓名","年龄","一级部门","二级部门","岗位","学历","性别","政治面貌","司龄(年)","入职日期","民族","人员类型","公司主体","费用类别","2022年考核等级","2023年考核等级","2024年考核等级","基本工资(元)","交通补贴","专业证书补贴","单位社保公积金(元)","餐补","租房补贴","探亲福利费","专利奖金","体检费","团建费","补充医疗","补偿金","在岗月数","绩效核算月数","绩效折扣比例","社保公积金年度上涨比例","工会经费计提比例"]
    input_keys=["序号","姓名","年龄","一级部门","二级部门","岗位","学历","性别","政治面貌","司龄(年)","入职日期","民族","人员类型","公司主体","费用类别","2022年考核等级","2023年考核等级","2024年考核等级","基本工资(元)","交通补贴","专业证书补贴","单位社保公积金(元)","餐补（输入）","租房补贴","探亲福利费（输入）","专利奖金（输入）","体检费（输入）","团建费（输入）","补充医疗（输入）","补偿金（输入）","在岗月数","绩效核算月数","绩效折扣比例","社保公积金年度上涨比例","工会经费计提比例"]
    calc_headers=["年度税前工资（含基本工资、交补、专业证书补贴）","年度单位社保公积金合计","年度绩效（8折）","转移重庆租房福利","非货币性福利","餐补","探亲福利费","专利奖金","体检费","团建费","补充医疗","工会经费","补偿金","人工成本合计"]
    ratio_headers=["投入交通工时占比","投入汽车工时占比","投入预研项目工时占比","投入自动化测试平台工时占比"]

    allocation_headers=[]
    for business in ["交通","汽车","研发平台"]:
        allocation_headers += [f"税前工资-{business}",f"单位社保公积金-{business}",f"年度绩效-{business}",f"工会经费-{business}",f"专利奖金-{business}",f"福利费-{business}",f"补偿金-{business}",f"合计-{business}"]
    allocation_headers += ["税前工资-测试平台","单位社保公积金-测试平台","年度绩效-测试平台","工会经费-测试平台","专利奖金-测试平台","福利费-测试平台","合计-测试平台"]

    output_headers=input_headers+calc_headers+ratio_headers+allocation_headers
    output_keys=input_keys+calc_headers+ratio_headers+allocation_headers

    aliases={"交通":"交通","汽车":"汽车","研发平台":"研发平台","预研项目":"研发平台","测试平台":"测试平台","自动化测试平台":"测试平台"}
    prompt_ratios={}
    for alias,business in aliases.items():
        hit=re.search(alias+r"[^0-9％%]{0,12}([0-9]+(?:\.[0-9]+)?)\s*[％%]",str(query or ""))
        if hit:
            prompt_ratios[business]=float(hit.group(1))/100

    records=[]
    for _,values in rows[header_index+1:]:
        values=(values+[""]*len(headers))[:len(headers)]
        # Excel提取器会连续返回工作簿内的多个Sheet。
        # 人员主表的“序号”必须是正整数；预算汇总表中的部门、总计、人数等行全部跳过，
        # 防止其被按人员主表列位置解析并产生“某人的在岗月数大于12”等错位错误。
        serial_text=str(get(values,"序号") or "").strip()
        if not re.fullmatch(r"[1-9]\d*(?:\.0+)?",serial_text):
            continue
        if not any(values) or blank(get(values,"姓名")):
            continue

        company=str(get(values,"公司主体") or "")
        comp=num(get(values,"补偿金"))
        basic=num(get(values,"基本工资(元)"))
        transport=num(get(values,"交通补贴"))
        certificate=num(get(values,"专业证书补贴"))

        raw_months=get(values,"在岗月数")
        months=12.0 if blank(raw_months) else num(raw_months)
        perf_wage=num(get(values,"绩效工资",get(values,"绩效工资(元)",0)))
        perf_months=num(get(values,"绩效核算月数"))
        perf_discount=num(get(values,"绩效折扣比例"))
        social_growth=num(get(values,"社保公积金年度上涨比例")) or 1.0
        union_rate=num(get(values,"工会经费计提比例"))

        if not 0<=months<=12:
            return {"status":"error","message":f"{get(values,'姓名')} 的在岗月数必须为 0–12。","headers":output_headers,"detail_keys":output_keys,"employees":[],"budget_records":[]}

        salary=(basic+transport+certificate)*months
        social=basic*0.48*months*social_growth
        performance=0.0 if "新增" in str(get(values,"人员类型") or "") else basic*perf_months*perf_discount

        medical=num(get(values,"补充医疗"))
        patent=num(get(values,"专利奖金"))
        transfer=num(get(values,"租房补贴"))*months
        noncash=0.0 if ("北京" in company or "重庆" in company) else (num(get(values,"非货币性福利")) or 3000.0)
        meal=num(get(values,"餐补"))*months
        visit=0.0 if ("北京" in company or "重庆" in company) else num(get(values,"探亲福利费"))
        checkup=num(get(values,"体检费"))
        team=num(get(values,"团建费"))

        if not union_rate:
            union_rate=0.016 if "北京" in company else (0.02 if "重庆" in company else 0.0)

        union=(salary+performance+patent)*union_rate
        welfare=transfer+noncash+meal+visit+checkup+team+medical
        total=salary+social+performance+transfer+noncash+meal+visit+patent+checkup+team+medical+union+comp

        base={key:get(values,header) for key,header in zip(input_keys,input_headers)}
        base["单位社保公积金(元)"]=basic*0.48
        base.update({"年度税前工资（含基本工资、交补、专业证书补贴）":salary,"年度单位社保公积金合计":social,"年度绩效（8折）":performance,"转移重庆租房福利":transfer,"非货币性福利":noncash,"餐补":meal,"探亲福利费":visit,"专利奖金":patent,"体检费":checkup,"团建费":team,"补充医疗":medical,"工会经费":union,"补偿金":comp,"人工成本合计":total})

        source_ratio=lambda h:num(get(values,h))
        ratios={"交通":prompt_ratios.get("交通",source_ratio("投入交通工时占比")),"汽车":prompt_ratios.get("汽车",source_ratio("投入汽车工时占比")),"研发平台":prompt_ratios.get("研发平台",source_ratio("投入预研项目工时占比")),"测试平台":prompt_ratios.get("测试平台",source_ratio("投入自动化测试平台工时占比"))}

        base.update({"投入交通工时占比":ratios["交通"],"投入汽车工时占比":ratios["汽车"],"投入预研项目工时占比":ratios["研发平台"],"投入自动化测试平台工时占比":ratios["测试平台"]})

        allocation_factor=lambda r:(r*12/months) if months>0 else 0.0

        for business in ["交通","汽车","研发平台"]:
            r=allocation_factor(ratios[business])
            line_values=[salary*r,social*r,performance*r,union*r,patent*r,welfare*r,comp*r]
            for label,amount in zip(["税前工资","单位社保公积金","年度绩效","工会经费","专利奖金","福利费","补偿金"],line_values):
                base[f"{label}-{business}"]=amount
            base[f"合计-{business}"]=sum(line_values)

        r=allocation_factor(ratios["测试平台"])
        test_welfare=transfer+noncash+meal+visit+checkup+team
        line_values=[salary*r,social*r,performance*r,union*r,patent*r,test_welfare*r]
        for label,amount in zip(["税前工资","单位社保公积金","年度绩效","工会经费","专利奖金","福利费"],line_values):
            base[f"{label}-测试平台"]=amount
        base["合计-测试平台"]=sum(line_values)

        records.append(base)

    if not records:
        return {"status":"error","message":"未识别到有效人员数据。","headers":output_headers,"detail_keys":output_keys,"employees":[],"budget_records":[]}

    return {
        "status":"success",
        "message":f"已生成 {len(records)} 条 A:CF 成本详表。",
        "headers":output_headers,
        "detail_keys":output_keys,
        "employees":records,
        "budget_records":records
    }