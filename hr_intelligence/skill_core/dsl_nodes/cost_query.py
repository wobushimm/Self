import json

def main(budget_records=None, query=None, detail_headers=None, detail_keys=None):
    rows=budget_records or []
    if isinstance(rows,str):
        try: rows=json.loads(rows)
        except: rows=[]
    if not rows: return {"result":"未识别到可输出的成本明细。"}
    def n(value):
        try: return float(value or 0)
        except: return 0.0
    def display(value, header=""):
        if isinstance(value,(int,float)):
            if abs(value)<1e-9:
                return "0.00" if "非货币性福利" in header else ""
            if "投入" in header and "占比" in header: return (f"{value:.2f}").rstrip("0").rstrip(".")
            return f"{value:,.2f}"
        return str(value or "").replace("|","｜").replace("\n"," ")
    def table(headers, body):
        lines=["| "+" | ".join(headers)+" |","|"+"|".join(["---"]*len(headers))+"|"]
        lines += ["| "+" | ".join(display(value, headers[index] if index < len(headers) else "") for index,value in enumerate(row))+" |" for row in body]
        return "\n".join(lines)
    metrics=[
        ("年度税前工资（含基本工资、交补、专业证书补贴）","求和项:年度税前工资（含基本工资、交补、专业证书补贴）"),("年度单位社保公积金合计","求和项:年度单位社保公积金合计"),("年度绩效（8折）","求和项:年度绩效（8折）"),("转移重庆租房福利","求和项:转移重庆租房福利"),("非货币性福利","求和项:非货币性福利"),("餐补","求和项:餐补"),("探亲福利费","求和项:探亲福利费"),("专利奖金","求和项:专利奖金"),("体检费","求和项:体检费"),("团建费","求和项:团建费"),("补充医疗","求和项:补充医疗"),("工会经费","求和项:工会经费"),("补偿金","求和项:补偿金"),("人工成本合计","求和项:人工成本合计")]
    def totals(group, fields): return [sum(n(row.get(field)) for row in group) for field in fields]
    def pivot(title, levels):
        headers=["行标签","计数项:姓名"]+[label for _,label in metrics]
        fields=[field for field,_ in metrics]; body=[]
        def descend(group, remaining, depth=0):
            field=remaining[0]; buckets={}
            for row in group: buckets.setdefault(str(row.get(field,"未填写") or "未填写"),[]).append(row)
            for label,items in sorted(buckets.items()):
                body.append(["　"*depth+label,len(items)]+totals(items,fields))
                if len(remaining)>1: descend(items,remaining[1:],depth+1)
        descend(rows,levels)
        body.append(["总计",len(rows)]+totals(rows,fields))
        return "# "+title+"\n"+table(headers,body)
    text=str(query or "")
    line="交通"
    if "汽车" in text: line="汽车"
    elif "研发平台" in text or "预研项目" in text: line="研发平台"
    elif "测试平台" in text or "自动化测试平台" in text: line="测试平台"
    ratio_field={"交通":"投入交通工时占比","汽车":"投入汽车工时占比","研发平台":"投入预研项目工时占比","测试平台":"投入自动化测试平台工时占比"}[line]
    # 目标业务线不展示个人明细：按一级部门、二级部门逐层汇总业务线相关指标。
    target_metric_fields=[ratio_field,f"税前工资-{line}",f"单位社保公积金-{line}",f"年度绩效-{line}",f"工会经费-{line}",f"专利奖金-{line}",f"福利费-{line}"]
    target_metric_headers=["求和项:投入比例","求和项:税前工资","求和项:单位社保公积金","求和项:年度绩效","求和项:工会经费","求和项:专利奖金","求和项:福利费"]
    if line!="测试平台":
        target_metric_fields.append(f"补偿金-{line}")
        target_metric_headers.append("求和项:补偿金")
    target_metric_fields.append(f"合计-{line}")
    target_metric_headers.append("求和项:人工成本合计")
    target_headers=["行标签","计数项:姓名"]+target_metric_headers
    target_body=[]
    # 仅保留当前业务线实际有投入或成本的人员及其所属部门。
    active_rows=[row for row in rows if abs(n(row.get(f"合计-{line}")))>1e-9 or abs(n(row.get(ratio_field)))>1e-9]
    primary_groups={}
    for row in active_rows: primary_groups.setdefault(str(row.get("一级部门","") or "未填写"),[]).append(row)
    for primary,primary_rows in sorted(primary_groups.items()):
        target_body.append([primary,len(primary_rows)]+totals(primary_rows,target_metric_fields))
        secondary_groups={}
        for row in primary_rows: secondary_groups.setdefault(str(row.get("二级部门","") or "未填写"),[]).append(row)
        for secondary,secondary_rows in sorted(secondary_groups.items()):
            target_body.append(["　"+secondary,len(secondary_rows)]+totals(secondary_rows,target_metric_fields))
    target_body.append(["总计",len(active_rows)]+totals(active_rows,target_metric_fields))
    # 与模板“预算输出需求3”保持同一宽表口径。
    business_headers=["行标签","计数项:姓名","求和项:投入交通工时占比","求和项:投入汽车工时占比","求和项:投入预研项目工时占比","求和项:投入自动化测试平台工时占比"]
    business_fields=["投入交通工时占比","投入汽车工时占比","投入预研项目工时占比","投入自动化测试平台工时占比"]
    for item in ["交通","汽车","研发平台"]:
        business_headers += [f"求和项:税前工资-{item}",f"求和项:单位社保公积金-{item}",f"求和项:年度绩效-{item}",f"求和项:工会经费-{item}",f"求和项:专利奖金-{item}",f"求和项:福利费-{item}",f"求和项:补偿金-{item}",f"求和项:合计-{item}"]
        business_fields += [f"税前工资-{item}",f"单位社保公积金-{item}",f"年度绩效-{item}",f"工会经费-{item}",f"专利奖金-{item}",f"福利费-{item}",f"补偿金-{item}",f"合计-{item}"]
    item="测试平台"
    business_headers += [f"求和项:税前工资-{item}",f"求和项:单位社保公积金-{item}",f"求和项:年度绩效-{item}",f"求和项:工会经费-{item}",f"求和项:专利奖金-{item}",f"求和项:福利费-{item}",f"求和项:合计-{item}"]
    business_fields += [f"税前工资-{item}",f"单位社保公积金-{item}",f"年度绩效-{item}",f"工会经费-{item}",f"专利奖金-{item}",f"福利费-{item}",f"合计-{item}"]
    groups={}
    for row in rows: groups.setdefault((str(row.get("一级部门","未填写")),str(row.get("二级部门","未填写"))),[]).append(row)
    business_body=[]
    for (primary,secondary),items in sorted(groups.items()): business_body.append([primary+" / "+secondary,len(items)]+totals(items,business_fields))
    business_body.append(["总计",len(rows)]+totals(rows,business_fields))
    detail_keys=detail_keys if isinstance(detail_keys,list) and detail_keys else list(rows[0].keys())
    detail_headers=detail_headers if isinstance(detail_headers,list) and len(detail_headers)==len(detail_keys) else detail_keys
    detail_body=[[row.get(field,"") for field in detail_keys] for row in rows]
    # 0903 详表：AJ 起的金额计算、工时比例及业务线拆分字段汇总；AE:AI 为逐人参数，不相加。
    detail_total_fields=set(detail_keys[35:])
    detail_total=[]
    for field in detail_keys:
        if field=="序号": detail_total.append("合计")
        elif field=="姓名": detail_total.append(f"{len(rows)}人")
        elif field in detail_total_fields: detail_total.append(sum(n(row.get(field)) for row in rows))
        else: detail_total.append("")
    detail_body.append(detail_total)
    sections=["# 人工成本详表（A到CF）\n"+table(detail_headers,detail_body),"# 目标业务线成本明细-"+line+"业务\n"+table(target_headers,target_body),pivot("预算输出需求1-按公司、一级、二级部门输出",["公司主体","一级部门","二级部门"]),pivot("预算输出需求2-按费用类别输出",["费用类别","一级部门","二级部门"]),"# 预算输出需求3-按业务线输出\n"+table(business_headers,business_body)]
    return {"result":"\n\n".join(sections)}
