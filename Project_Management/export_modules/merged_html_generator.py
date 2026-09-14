#!/usr/bin/env python3
"""跨月合并 HTML 快报生成器。

将多个月的 snapshot 合并为一份 HTML 报告：
- 顶部：项目信息 + 月份范围
- 对比总览：关键指标多月横向对比表 + 对比图表
- 各月详情：每月的完整快报内容（通过 Tab 切换）
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path

from data_modules.metrics_engine import create_metrics, number
from export_modules.html_generator import _echarts_js_tag


# ── 浏览器内交互脚本（与单月 html_generator 保持一致）──
_PAGE_SAVE_JS = r"""
(function(){
  function inEditable(el){
    return !!(el && el.closest && el.closest('[contenteditable="true"]'));
  }
  function getSelectionCtx(){
    var sel=window.getSelection();
    if(!sel || !sel.rangeCount || sel.isCollapsed) return null;
    var page=sel.anchorNode&&(sel.anchorNode.nodeType===1?sel.anchorNode:sel.anchorNode.parentElement);
    page=page&&page.closest?page.closest('[data-pmopage]'):null;
    return {sel:sel, range:sel.getRangeAt(0), pageLabel:page?((page.querySelector('h2')||{}).textContent||'').trim():''};
  }
  document.addEventListener('keydown', function(e){
    if(e.key!=='Enter') return;
    var td=e.target && e.target.closest && e.target.closest('td[contenteditable]');
    if(!td) return;
    e.preventDefault();
    td.blur();
  });
  document.addEventListener('paste', function(e){
    if(!inEditable(e.target)) return;
    e.preventDefault();
    var t=(e.clipboardData||window.clipboardData).getData('text/plain')||'';
    document.execCommand('insertText', false, t);
  });
  function applyForeColor(color){
    var ctx=getSelectionCtx();
    if(!ctx){ alert('请先划选要改色的文字'); return; }
    try{ document.execCommand('styleWithCSS', false, true); }catch(_e){}
    document.execCommand('foreColor', false, color);
  }
  function addAnnotation(){
    var ctx=getSelectionCtx();
    if(!ctx){ alert('请先在页面任意位置划选要批注的文字'); return; }
    var note=window.prompt('批注内容：','');
    if(note===null) return;
    note=String(note||'').trim();
    if(!note){ alert('批注内容不能为空'); return; }
    var id='anno-'+Date.now()+'-'+Math.floor(Math.random()*1000);
    var pageLabel=ctx.pageLabel||'';
    var mark=document.createElement('mark');
    mark.className='anno-hl';
    mark.setAttribute('data-anno-id', id);
    mark.title=(pageLabel?'['+pageLabel+'] ':'')+note;
    try{ ctx.range.surroundContents(mark); }
    catch(_e){ var frag=ctx.range.extractContents(); mark.appendChild(frag); ctx.range.insertNode(mark); }
    var rail=document.getElementById('pmo-anno-rail');
    if(rail){
      var item=document.createElement('div'); item.className='anno-item'; item.setAttribute('data-anno-id', id); item.setAttribute('data-page', pageLabel);
      var plabel=document.createElement('div'); plabel.className='anno-page-label'; plabel.textContent=pageLabel||'总览';
      var text=document.createElement('div'); text.className='anno-text'; text.setAttribute('contenteditable','true'); text.setAttribute('spellcheck','false'); text.textContent=note;
      var meta=document.createElement('div'); meta.className='anno-meta';
      meta.innerHTML='<button type="button" class="anno-jump">定位</button><button type="button" class="anno-del">删除</button>';
      item.appendChild(plabel); item.appendChild(text); item.appendChild(meta); rail.appendChild(item);
    }
    ctx.sel.removeAllRanges();
  }
  document.addEventListener('click', function(e){
    var jump=e.target && e.target.closest && e.target.closest('.anno-jump');
    if(jump){ var item=jump.closest('.anno-item'); var id=item && item.getAttribute('data-anno-id'); if(!id) return;
      var m=document.querySelector('mark.anno-hl[data-anno-id="'+id+'"]');
      if(m){ m.scrollIntoView({behavior:'smooth',block:'center'}); m.classList.add('anno-flash'); setTimeout(function(){m.classList.remove('anno-flash');},1200); } return; }
    var del=e.target && e.target.closest && e.target.closest('.anno-del');
    if(del){ var item2=del.closest('.anno-item'); var id2=item2 && item2.getAttribute('data-anno-id'); if(!id2) return;
      var m2=document.querySelector('mark.anno-hl[data-anno-id="'+id2+'"]');
      if(m2){ var parent=m2.parentNode; while(m2.firstChild) parent.insertBefore(m2.firstChild, m2); parent.removeChild(m2); parent.normalize(); }
      if(item2) item2.remove(); }
  });
  var colorBar=document.getElementById('color-bar');
  if(colorBar){ colorBar.addEventListener('click', function(e){ var btn=e.target && e.target.closest && e.target.closest('[data-color]'); if(!btn) return; applyForeColor(btn.getAttribute('data-color')); }); }
  var btnAnno=document.getElementById('btn-add-anno');
  if(btnAnno) btnAnno.addEventListener('click', addAnnotation);
  var btn=document.getElementById('btn-save-html');
  if(!btn) return;
  btn.addEventListener('click', function(){
    if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
    var clone=document.documentElement.cloneNode(true);
    var boxes=clone.querySelectorAll('.chart-canvas');
    for(var i=0;i<boxes.length;i++){ boxes[i].removeAttribute('_echarts_instance_'); boxes[i].innerHTML=''; }
    var flashes=clone.querySelectorAll('.anno-flash');
    for(var j=0;j<flashes.length;j++) flashes[j].classList.remove('anno-flash');
    var html='<!DOCTYPE html>\n'+clone.outerHTML;
    var blob=new Blob([html], {type:'text/html;charset=utf-8'});
    var a=document.createElement('a');
    var name=(document.title||'项目管理快报').replace(/[\\/:*?"<>|]/g,'_')+'.html';
    a.href=URL.createObjectURL(blob); a.download=name;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){URL.revokeObjectURL(a.href);}, 2000);
  });
})();
"""


# ── 页面选择工具（与单月 HTML 生成器保持一致） ──
PAGE_ID_ALIASES = {
    "p01": "p01_fine_management",
    "p02": "p02_investment_deviation",
    "p03": "p03_staff_health",
    "p04": "p04_product_quality",
    "p05": "p05_bug_efficiency",
    "p06": "p06_value_delivery",
}

# 对比指标名 → 所属页面映射
_COMPARISON_METRIC_PAGE = {
    "工时偏差率": "p02_investment_deviation",
    "预算偏差率": "p02_investment_deviation",
    "Sprint完成率": "p01_fine_management",
    "任务完成数": "p01_fine_management",
    "实际总工时": "p02_investment_deviation",
    "平均考勤时长": "p03_staff_health",
    "Bug关闭率": "p04_product_quality",
    "缺陷逃逸率": "p04_product_quality",
    "Bug总数": "p04_product_quality",
    "需求验证通过率": "p01_fine_management",
}

# 关键数据摘要维度 → 所属页面
_SUMMARY_DIMENSION_PAGE = {
    "产研管理": "p01_fine_management",
    "投入偏差": "p02_investment_deviation",
    "人员健康": "p03_staff_health",
    "质量": "p04_product_quality",
}

# 指标代码 → 所属页面映射（与单月 HTML 一致）
METRIC_PAGE_MAP = {
    "sprint_completion_rate": ["p01_fine_management"],
    "req_verify_rate": ["p01_fine_management", "p06_value_delivery"],
    "workhour_deviation_rate": ["p02_investment_deviation"],
    "budget_deviation_rate": ["p02_investment_deviation"],
    "rd_resource_input_ratio": ["p03_staff_health", "p06_value_delivery"],
    "defect_escape_rate": ["p04_product_quality"],
    "bug_close_rate": ["p04_product_quality", "p05_bug_efficiency"],
    "mttr": ["p05_bug_efficiency"],
    "requirement_cost": ["p06_value_delivery"],
}


def _resolve_page_ids(page_ids):
    """解析页面 ID 列表，支持简写。返回 None 表示全部包含。"""
    if page_ids is None:
        return None
    if isinstance(page_ids, str):
        page_ids = [page_ids]
    result = []
    for pid in page_ids:
        pid_lower = pid.lower().strip()
        if pid_lower == "all":
            return None
        result.append(PAGE_ID_ALIASES.get(pid_lower, pid_lower))
    return result


def _num(value, default=0.0):
    parsed = number(value)
    return default if parsed is None else parsed


def _fmt(value, unit="", decimals=1):
    """格式化数值用于显示。"""
    if value is None:
        return "—"
    if unit == "%":
        return f"{value * 100:.{decimals}f}%"
    if unit == "小时":
        return f"{value:.{decimals}f}h"
    if unit == "人月":
        return f"{value:.2f}"
    if unit == "个":
        return f"{value:.0f}"
    return f"{value:.{decimals}f}"


def _build_comparison_data(snapshots, allowed_pages=None):
    """构建多月对比数据。

    Args:
        snapshots: 多个月份的 snapshot 列表
        allowed_pages: 解析后的页面 ID 集合，None 表示全部

    Returns:
        dict: {
            "months": ["2026-06", "2026-07"],
            "metrics": [
                {"name": "指标名", "values": [v1, v2], "unit": "%"},
                ...
            ]
        }
    """
    months = []
    all_metrics = []

    # 定义要对比的核心指标（含页面归属列表）
    metric_defs = [
        ("工时偏差率", lambda m: (_num(m.get("actual_total_man_hour")) - _num(m.get("plan_total_man_hour"))) / _num(m.get("plan_total_man_hour", 1)) if _num(m.get("plan_total_man_hour")) else None, "%", ["p02_investment_deviation"]),
        ("预算偏差率", lambda m: _num(m.get("rd_cost_exec_rate_month")) - 1 if m.get("rd_cost_exec_rate_month") is not None else None, "%", ["p02_investment_deviation"]),
        ("Sprint完成率", lambda m: _num(m.get("task_finish_count", 0)) / _num(m.get("task_count", 1))
            if m.get("task_count") else None, "%", ["p01_fine_management"]),
        ("Bug关闭率", lambda m: _num(m.get("bug_close_rate")), "%", ["p04_product_quality", "p05_bug_efficiency"]),
        ("缺陷逃逸率", lambda m: _num(m.get("bug_leak_rate")), "%", ["p04_product_quality"]),
        ("MTTR", lambda m: (lambda d, t: d / t / 3600.0 if d is not None and t else None)(
            _num(m.get("bug_resolve_duration_exclude_reject_third")) or _num(m.get("bug_resolve_duration_seconds")),
            _num(m.get("bug_total_count"))), "小时", ["p05_bug_efficiency"]),
        ("需求验证通过率", lambda m: _num(m.get("req_verify_rate")), "%", ["p01_fine_management"]),
        ("任务完成数", lambda m: _num(m.get("task_finish_count")), "个", ["p01_fine_management"]),
        ("Bug总数", lambda m: _num(m.get("bug_total_count")), "个", ["p04_product_quality", "p05_bug_efficiency"]),
        ("实际总工时", lambda m: _num(m.get("actual_total_man_hour")), "小时", ["p02_investment_deviation"]),
        ("平均考勤时长", lambda m: _num(m.get("attendance_hour")), "小时", ["p03_staff_health"]),
    ]
    # 按页面过滤
    if allowed_pages is not None:
        metric_defs = [d for d in metric_defs if any(p in allowed_pages for p in d[3])]

    for snap in snapshots:
        proj = snap.get("project", {})
        bug = snap.get("bug_detail", {})
        req = snap.get("requirement_detail", {})
        months.append(proj.get("stat_month", ""))

        # 合并各数据源
        merged = {}
        merged.update(proj)
        merged["bug_total_count"] = bug.get("bug_total_count")
        merged["bug_close_rate"] = bug.get("bug_close_rate")
        merged["bug_leak_rate"] = bug.get("bug_leak_rate")
        merged["req_verify_rate"] = req.get("req_verify_rate")
        # MTTR 相关字段
        merged["bug_resolve_duration_exclude_reject_third"] = bug.get("bug_resolve_duration_exclude_reject_third")
        merged["bug_resolve_duration_seconds"] = bug.get("bug_resolve_duration_seconds")
        quality_cur = snap.get("quality_current", {})
        merged["rd_cost_exec_rate_month"] = quality_cur.get("rd_cost_exec_rate_month")

        if not all_metrics:
            # 第一次循环，初始化指标列表
            for name, extractor, unit, _page in metric_defs:
                val = extractor(merged)
                all_metrics.append({"name": name, "values": [val], "unit": unit})
        else:
            for i, (name, extractor, unit, _page) in enumerate(metric_defs):
                val = extractor(merged)
                all_metrics[i]["values"].append(val)

    return {"months": months, "metrics": all_metrics}


def _comparison_table(comp_data):
    """生成对比表格 HTML。"""
    months = comp_data["months"]
    metrics = comp_data["metrics"]

    # 表头
    header_cells = '<th>指标</th>'
    for m in months:
        header_cells += f'<th>{html.escape(m)}</th>'
    header_cells += '<th>变化趋势</th>'

    # 数据行
    rows_html = ""
    for metric in metrics:
        cells = f'<td class="metric-name">{html.escape(metric["name"])}</td>'
        unit = metric["unit"]
        for val in metric["values"]:
            if val is None:
                cells += '<td class="num">—</td>'
            elif unit == "%":
                cells += f'<td class="num">{val * 100:.1f}%</td>'
            elif unit == "小时":
                cells += f'<td class="num">{val:.1f}h</td>'
            elif unit == "个":
                cells += f'<td class="num">{val:.0f}</td>'
            else:
                cells += f'<td class="num">{val:.2f}</td>'

        # 趋势箭头（根据单位格式化差值）
        vals = [v for v in metric["values"] if v is not None]
        trend = "—"
        if len(vals) >= 2:
            diff = vals[-1] - vals[-2]
            if unit == "%":
                # 百分比指标：差值乘以 100 显示为百分点
                diff_display = diff * 100
                fmt_diff = f"{diff_display:+.1f}pp"
            elif unit == "小时":
                fmt_diff = f"{diff:+.1f}h"
            elif unit == "个":
                fmt_diff = f"{diff:+.0f}"
            else:
                fmt_diff = f"{diff:+.1f}"

            if abs(diff) < (0.0001 if unit == "%" else 0.01 if unit == "小时" else 0.5 if unit == "个" else 0.01):
                trend = '<span class="trend flat">→ 持平</span>'
            elif diff > 0:
                trend = f'<span class="trend up">↑ {fmt_diff}</span>'
            else:
                trend = f'<span class="trend down">↓ {fmt_diff}</span>'
        cells += f'<td class="trend-cell">{trend}</td>'
        rows_html += f"<tr>{cells}</tr>"

    return f'''<table class="comparison-table">
<thead><tr>{header_cells}</tr></thead>
<tbody>{rows_html}</tbody>
</table>'''


def _comparison_charts(comp_data):
    """生成对比图表的 ECharts option JSON。"""
    months = comp_data["months"]
    charts_html = ""

    # 选择关键指标做柱状图对比（过滤后指标少时全部展示）
    chart_metrics = [m for m in comp_data["metrics"]
                     if any(v is not None for v in m["values"])]

    if not chart_metrics:
        return ""

    # 指标较少时全部展示，否则优先展示关键指标
    if len(chart_metrics) <= 4:
        key_charts = chart_metrics
    else:
        key_charts = []
        for m in chart_metrics:
            if m["name"] in ("实际总工时", "Sprint完成率", "Bug总数", "Bug关闭率",
                             "工时偏差率", "预算偏差率", "MTTR"):
                key_charts.append(m)
        if not key_charts:
            key_charts = chart_metrics[:4]

    for i, m in enumerate(key_charts[:4]):
        chart_id = f"comp_chart_{i}"
        unit_label = {"%": "%", "小时": "h", "个": ""}.get(m["unit"], "")
        values = [0 if v is None else round(v * 100, 1) if m["unit"] == "%" else round(v, 1)
                  for v in m["values"]]
        option = {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": "15%", "right": "8%", "top": "15%", "bottom": "15%"},
            "xAxis": {"type": "category", "data": months},
            "yAxis": {"type": "value", "name": unit_label},
            "series": [{"type": "bar", "data": values, "barMaxWidth": 40,
                        "itemStyle": {"borderRadius": [4, 4, 0, 0]},
                        "label": {"show": True, "position": "top"}}],
        }
        payload = html.escape(json.dumps(option, ensure_ascii=False), quote=True)
        charts_html += f'''<article class="chart-card">
<h3>{html.escape(m["name"])}月度对比</h3>
<div id="{chart_id}" class="chart-canvas" data-option="{payload}"></div>
</article>'''

    return charts_html


def _quarterly_summary_html(aggregated_data: dict, months: list[str], quarter_label: str = "") -> str:
    """生成聚合数据汇总区域 HTML（支持季度模式和月份模式）。"""
    if not aggregated_data:
        return ""
    q = aggregated_data
    range_str = f"{months[0]} ~ {months[-1]}" if len(months) > 1 else months[0]
    
    # 根据模式设置不同的标题
    if quarter_label:
        # 季度模式
        display_label = f"{quarter_label} 季度汇总"
        range_detail = f"({range_str} 聚合数据)"
        data_note = "产品线看板 API 季度聚合口径，可加指标为各月之和，比率指标为重新计算。"
    else:
        # 月份模式
        display_label = f"{range_str} 汇总"
        range_detail = "(聚合数据)"
        data_note = "产品线看板 API 聚合口径（与平台看板一致），可加指标为各月之和，比率指标为重新计算。"

    def _fmt_pct(v):
        if v is None: return "—"
        try: return f"{float(v) * 100:.1f}%"
        except (ValueError, TypeError): return "—"
    def _fmt_num(v, unit=""):
        if v is None: return "—"
        try:
            fv = float(v)
        except (ValueError, TypeError):
            return "—"
        if unit == "h": return f"{fv:,.0f}h"
        if unit == "元": return f"{fv:,.0f}元"
        return f"{fv:,.0f}"

    # 计算偏差率
    ah = _num(q.get("actual_total_man_hour"))
    ph = _num(q.get("plan_total_man_hour")) or 1
    wdr = (ah - ph) / ph if ph else None
    adc = _num(q.get("actual_dev_cost")) + _num(q.get("actual_staff_cost"))
    pdc = _num(q.get("plan_dev_cost")) + _num(q.get("plan_staff_cost"))
    bdr = (adc - pdc) / pdc if pdc else None
    
    sprint_rate = _num(q.get('task_finish_count')) / _num(q.get('task_count')) if _num(q.get('task_count')) else None
    
    cards = [
        ("实际总工时", _fmt_num(ah, "h"), f"计划 {_fmt_num(ph, 'h')}"),
        ("工时偏差率", _fmt_pct(wdr), "±15% 以内为正常"),
        ("预算偏差率", _fmt_pct(bdr), "±10% 以内为正常"),
        ("Sprint完成率", _fmt_pct(sprint_rate), f"完成 {int(_num(q.get('task_finish_count')))}/{int(_num(q.get('task_count')))} 项"),
        ("Bug关闭率", _fmt_pct(_num(q.get("bug_close_rate"))), "目标 ≥95%"),
        ("缺陷逃逸率", _fmt_pct(_num(q.get("bug_leak_rate"))), "目标 ≤3%"),
        ("任务完成数", _fmt_num(q.get("task_finish_count")), f"延期 {int(_num(q.get('task_delay_count')))} 项"),
        ("Bug总数", _fmt_num(q.get("bug_total_count")), ""),
    ]

    cards_html = "".join(
        f'<div class="q-card"><h4>{name}</h4><strong>{val}</strong><span>{sub}</span></div>'
        for name, val, sub in cards
    )

    return f'''<section class="page quarterly-summary">
<div class="section-head"><div><h2>{html.escape(display_label)} <small style="font-size:14px;color:#667893">{html.escape(range_detail)}</small></h2>
<p>数据来源：{data_note}</p></div></div>
<div class="q-grid">{cards_html}</div>
</section>'''


def build_merged_report(snapshots: list[dict], output_path: Path, pdf_filename: str = "",
                        quarterly_data: dict = None, quarter_label: str = "",
                        export_files=None, aggregated_data: dict = None,
                        page_ids: list[str] = None,
                        focus: str = None) -> None:
    """生成跨月合并 HTML 报告。

    Args:
        snapshots: 多个月份的 snapshot 列表
        output_path: 输出 HTML 文件路径
        pdf_filename: PDF 文件名（用于导出按钮下载链接）
        quarterly_data: 季度聚合 API 返回数据（可选）
        aggregated_data: 实际月份范围聚合数据（与平台看板口径一致）
        page_ids: 页面选择，如 ["p01","p03"]，None=全部
        focus: 关键词过滤，只保留名称匹配的指标
    """
    if not snapshots:
        return

    # 基本信息（取第一个月）
    first_proj = snapshots[0].get("project", {})
    project_name = str(first_proj.get("project_name", ""))
    project_code = str(first_proj.get("project_code", first_proj.get("project_id", "待补")))
    manager = str(first_proj.get("manager_name", "待补"))
    product_line = str(first_proj.get("product_line") or first_proj.get("department_name") or "产品线")

    months = [s.get("project", {}).get("stat_month", "") for s in snapshots]
    month_range_label = f"{months[0]} ~ {months[-1]}" if len(months) > 1 else months[0]

    # 解析页面选择
    allowed_pages = _resolve_page_ids(page_ids)

    # 构建对比数据（按页面过滤）
    comp_data = _build_comparison_data(snapshots, allowed_pages=allowed_pages)

    # 按 focus 关键词进一步过滤对比指标
    if focus:
        focus_lower = focus.lower()
        comp_data["metrics"] = [m for m in comp_data["metrics"]
                                if focus_lower in m["name"].lower()]

    # 生成摘要区 HTML：focus 激活时跳过聚合数据汇总
    agg_data = aggregated_data or {}
    if focus:
        quarterly_section = ""
    else:
        quarterly_section = _quarterly_summary_html(agg_data, months, quarter_label=quarter_label)

    month_tabs = ""
    month_panels = ""
    for i, (snap, month) in enumerate(zip(snapshots, months)):
        active = " active" if i == 0 else ""
        month_tabs += f'<button class="month-tab{active}" data-month="{i}">{html.escape(month)}</button>'
        panel_html = _build_month_detail_panel(snap, month, i, allowed_pages=allowed_pages, focus=focus)
        month_panels += f'<div class="month-panel" id="month-panel-{i}" style="display:{("block" if i == 0 else "none")}">{panel_html}</div>'

    # 组装完整 HTML
    report_title = f"{project_name}_{month_range_label}_跨月合并快报"
    comparison_table_html = _comparison_table(comp_data)
    comparison_charts_html = _comparison_charts(comp_data)

    # ── 构建导出按钮（服务端生成的真实文件下载链接）──
    export_files = export_files or {}
    _btns = []
    if export_files.get('pdf'):
        _btns.append(f'<a class="button pdf" href="{html.escape(export_files["pdf"])}" download>▤ 导出PDF</a>')
    if export_files.get('docx'):
        _btns.append(f'<a class="button word" href="{html.escape(export_files["docx"])}" download>▣ 导出Word</a>')
    if export_files.get('pptx'):
        _btns.append(f'<a class="button ppt" href="{html.escape(export_files["pptx"])}" download>▥ 导出PPT</a>')
    export_buttons = ''.join(_btns)

    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{_echarts_js_tag()}
<title>{html.escape(report_title)}</title>
<style>
:root{{--blue:#2458d7;--navy:#173e79;--ink:#1c2941;--muted:#667895;--paper:#fff;--bg:#eff3f8;--green:#16a34a;--yellow:#d68c00;--red:#e32828;--gray:#8b98aa}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;font-size:15px}}
.hero{{background:linear-gradient(118deg,#173e79,#2864f3);color:#fff;padding:46px max(5vw,32px) 38px}}
.hero-inner{{max-width:1220px;margin:auto}}
.hero-grid{{display:grid;grid-template-columns:1.55fr repeat(3,.85fr);gap:22px;align-items:start}}
.hero h1{{font-size:38px;letter-spacing:1px;margin:0 0 15px}}
.hero .project{{font-size:20px;line-height:1.5;color:#e6eeff}}
.hero-meta{{font-size:16px;line-height:1.5;padding-top:8px}}
.hero-meta b{{display:block;font-size:19px;margin-top:4px}}
button,.button{{font:inherit;cursor:pointer;text-decoration:none}}
.hero-actions{{display:flex;gap:13px;margin-top:25px}}.hero-actions .button{{font-size:16px;padding:12px 22px;border-radius:9px;font-weight:800}}.hero-actions .pdf{{border:0;background:#fff;color:#2458d7}}.hero-actions .word{{border:2px solid #9bb8ff;background:#ffffff16;color:#fff}}
.page{{padding:29px max(5vw,32px);max-width:1220px;margin:auto}}
.section-head{{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}}
.section-head h2{{margin:0;font-size:27px}}
.section-head p{{color:var(--muted);margin:5px 0 0;font-size:16px}}

/* 对比表格 */
.comparison-table{{border-collapse:collapse;width:100%;background:#fff;border-radius:13px;overflow:hidden;box-shadow:0 4px 11px #2137550d}}
.comparison-table th{{background:#edf3fd;color:#3c577e;text-align:left;padding:12px 14px;font-size:14px;border-bottom:2px solid #d3deee}}
.comparison-table td{{padding:10px 14px;border-bottom:1px solid #e4eaf2;font-size:14px;line-height:1.45}}
.comparison-table .metric-name{{font-weight:600;color:#3c577e;white-space:nowrap}}
.comparison-table .num{{text-align:right;font-variant-numeric:tabular-nums}}
.comparison-table .trend-cell{{text-align:center;min-width:100px}}
.trend{{border-radius:999px;padding:3px 10px;font-weight:700;font-size:12px;white-space:nowrap}}
.trend.up{{color:#0d8231;background:#e9f9ee}}
.trend.down{{color:#c51f1f;background:#ffebeb}}
.trend.flat{{color:#62748f;background:#edf1f6}}

/* 图表 */
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}}
.chart-card{{background:#fff;border-radius:13px;box-shadow:0 4px 11px #2137550d;padding:20px}}
.chart-card h3{{margin:0 0 14px;font-size:18px}}
.chart-canvas{{width:100%;height:265px;display:block}}

/* 月份 Tab */
.month-tabs{{display:flex;gap:8px;margin:24px 0 16px;border-bottom:2px solid #e4eaf2;padding-bottom:0}}
.month-tab{{border:none;background:none;color:var(--muted);font-size:16px;font-weight:600;padding:10px 20px;cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;transition:all .2s}}
.month-tab:hover{{color:var(--blue)}}
.month-tab.active{{color:var(--blue);border-bottom-color:var(--blue)}}

/* 各月详情（复用单月快报样式） */
.month-panel{{margin-top:16px}}
.summary-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}}
.status-card{{border-radius:13px;padding:19px 25px;border:2px solid;background:#fff}}
.status-card b{{display:block;font-size:35px;margin-left:56px;margin-top:-43px}}
.status-card p{{font-weight:700;color:#667895;margin:13px 0 0 56px}}
.status-card:before{{content:"";display:block;width:40px;height:40px;border-radius:50%;box-shadow:inset 0 3px 5px #ffffffa8,0 2px 5px #0002}}
.status-card.green{{background:#effdf4;border-color:#79e6a5}}.status-card.green:before{{background:#0bbd27}}
.status-card.yellow{{background:#fffbd9;border-color:#f4d235}}.status-card.yellow:before{{background:#ffd318}}
.status-card.red{{background:#fff0f0;border-color:#ff9b9b}}.status-card.red:before{{background:#e22222}}
.status-card.gray{{background:#f7f9fc;border-color:#cfd9e8}}.status-card.gray:before{{background:#96a5ba}}
.metric-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin-top:18px}}
.metric-card{{background:#fff;border-radius:14px;padding:23px 25px;min-height:180px;box-shadow:0 6px 13px #233e6e12;border-left:6px solid var(--gray)}}
.metric-card.green{{border-color:var(--green)}}.metric-card.yellow{{border-color:var(--yellow)}}.metric-card.red{{border-color:var(--red)}}
.metric-card h3{{font-size:17px;color:#62748f;font-weight:500;line-height:1.35;margin:0 0 14px}}
.metric-card strong{{display:block;font-size:30px;line-height:1.05;color:var(--ink)}}
.metric-card.green strong{{color:var(--green)}}.metric-card.yellow strong{{color:var(--yellow)}}.metric-card.red strong{{color:var(--red)}}
.metric-card p{{font-size:14px;color:#657793;margin:12px 0 0;display:flex;align-items:center;gap:8px}}
.dot{{width:20px;height:20px;border-radius:50%;display:inline-block}}
.dot.green{{background:var(--green)}}.dot.yellow{{background:#ffcd16}}.dot.red{{background:var(--red)}}.dot.gray{{background:var(--gray)}}

.alert-table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:18px}}
.alert-table th{{background:#edf3fd;color:#3c577e;text-align:left}}
.alert-table th,.alert-table td{{padding:10px 9px;border-bottom:1px solid #e4eaf2;vertical-align:top;line-height:1.45}}
.tag{{border-radius:999px;padding:4px 9px;font-weight:700;white-space:nowrap}}
.tag.green{{color:#0d8231;background:#e9f9ee}}.tag.yellow{{color:#996500;background:#fff8dc}}.tag.red{{color:#c51f1f;background:#ffebeb}}.tag.gray{{color:#62748f;background:#edf1f6}}

.footer{{padding:21px 5vw;background:#172033;color:#d9e2f1;text-align:center;font-size:12px;margin-top:32px}}.pmo-anno-rail{{position:fixed;right:12px;top:70px;width:240px;max-height:calc(100vh - 90px);overflow-y:auto;display:flex;flex-direction:column;gap:8px;z-index:15;pointer-events:auto}}.pmo-anno-rail:empty{{display:none}}.anno-item{{background:#FFF9E8;border:1px solid #F0D78C;border-radius:8px;padding:8px 10px;font-size:12px}}.anno-page-label{{font-size:11px;color:#fff;background:#5B7AA5;border-radius:3px;padding:1px 6px;margin-bottom:4px;display:inline-block}}.anno-text{{line-height:1.5;margin-bottom:6px;min-height:1.4em}}.anno-meta{{display:flex;gap:6px}}.anno-meta button{{background:#eee;border:0;border-radius:4px;padding:3px 8px;font-size:12px;cursor:pointer;color:#345}}.anno-meta button:hover{{background:#ddd}}
.toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:0 0 16px;padding:10px 12px;background:#F2F7FC;border:1px solid #d6e3f0;border-radius:6px;position:sticky;top:0;z-index:20}}.toolbar button{{background:#1F497D;color:#fff;border:0;border-radius:4px;padding:8px 14px;font-size:14px;cursor:pointer}}.toolbar button:hover{{background:#16365a}}.toolbar .btn-secondary{{background:#5B7AA5}}.color-bar{{display:inline-flex;align-items:center;gap:6px;padding:2px 4px}}.color-swatch{{width:22px;height:22px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 1px #99a;cursor:pointer;padding:0}}.hint{{color:#666;font-size:13px;margin:0;flex:1;min-width:200px}}td[contenteditable],.analysis-body[contenteditable],[data-metric],[data-pmopage] .decision,[data-pmopage] .note-card,.q-card,.comparison-table td{{cursor:text;outline:none}}td[contenteditable]:focus,.analysis-body[contenteditable]:focus,[data-metric]:focus,[data-pmopage] .decision:focus,[data-pmopage] .note-card:focus,.q-card:focus,.comparison-table td:focus{{background:#FFF8E1;box-shadow:inset 0 0 0 2px #1F497D}}mark.anno-hl{{background:#FFE082;padding:0 2px;border-radius:2px;cursor:help}}mark.anno-flash{{outline:2px solid #F57C00}}
/* 季度汇总 */
.quarterly-summary{{margin-bottom:8px}}
.q-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}
.q-card{{background:#fff;border-radius:13px;padding:20px 22px;box-shadow:0 4px 11px #2137550d;border-left:5px solid var(--blue)}}
.q-card h4{{margin:0 0 8px;font-size:13px;color:var(--muted);font-weight:600}}
.q-card strong{{display:block;font-size:26px;color:var(--ink);margin-bottom:6px}}
.q-card span{{font-size:12px;color:var(--muted)}}

@media(max-width:980px){{.hero-grid{{grid-template-columns:1fr 1fr}}.summary-grid,.metric-grid,.chart-grid,.q-grid{{grid-template-columns:1fr}}}}
@media(max-width:600px){{.hero,.page{{padding-left:18px;padding-right:18px}}.hero h1{{font-size:28px}}.metric-grid,.summary-grid,.q-grid{{grid-template-columns:1fr}}}}
@media print{{.hero{{padding:13mm 12mm 9mm}}.hero-actions{{display:none}}.month-tabs{{display:none}}.month-panel{{display:block!important}}}}
.calib-panel{{margin-top:18px}}.calib-panel summary{{cursor:pointer;font-weight:700;color:#3c577e;font-size:14px;padding:8px 0;user-select:none}}.calib-panel .calib-body{{margin-top:8px;background:#fff;border-radius:13px;padding:16px 8px;box-shadow:0 4px 11px #2137550d;overflow:auto}}.calib-panel table{{border-collapse:collapse;width:100%}}.calib-panel .calib-group{{padding:8px 10px;border-bottom:1px solid #eef2f7;font-weight:600;color:#3c577e;font-size:13px}}.calib-panel .calib-formula{{padding:4px 10px 8px 18px;border-bottom:2px solid #e4eaf2;color:#8b98aa;font-size:12px;font-family:monospace}}.calib-panel .calib-label{{padding:6px 10px 6px 28px;border-bottom:1px solid #eef2f7;font-size:13px;color:#667895}}.calib-panel .calib-input{{padding:6px 4px;border-bottom:1px solid #eef2f7}}.calib-panel .calib-unit{{padding:6px 10px;border-bottom:1px solid #eef2f7;font-size:12px;color:#8b98aa}}.calib-panel input[type=number]{{width:95px;padding:5px 8px;border:1px solid #d3deee;border-radius:6px;font-size:13px;text-align:right}}
</style></head><body>
<div class="toolbar"><button type="button" id="btn-save-html">保存本页</button><button type="button" id="btn-add-anno" class="btn-secondary">加批注</button><div class="color-bar" id="color-bar"><span class="color-label">字体颜色</span><button type="button" class="color-swatch" data-color="#222222" style="background:#222"></button><button type="button" class="color-swatch" data-color="#C62828" style="background:#C62828"></button><button type="button" class="color-swatch" data-color="#EF6C00" style="background:#EF6C00" title="橙色"></button><button type="button" class="color-swatch" data-color="#2E7D32" style="background:#2E7D32" title="绿色"></button><button type="button" class="color-swatch" data-color="#1565C0" style="background:#1565C0" title="蓝色"></button><button type="button" class="color-swatch" data-color="#6A1B9A" style="background:#6A1B9A" title="紫色"></button></div><p class="hint">全页面数据可编辑，点击即可修改。划选任意文字可改字体颜色或加批注，批注可在右侧面板直接编辑修改。点「保存本页」下载到本机（含修改与批注），不写回服务器。</p></div>

<section class="hero" contenteditable="true" spellcheck="false"><div class="hero-inner"><div class="hero-grid">
<div><h1>跨月合并快报</h1><div class="project">{html.escape(product_line)} · {html.escape(project_name)}<br>（{html.escape(project_code)}）</div></div>
<div class="hero-meta">▣ 报告范围<b>{html.escape(month_range_label)}</b></div>
<div class="hero-meta">♟ 项目经理<b>{html.escape(manager)}</b></div>
<div class="hero-meta">▦ 涵盖月份<b>{len(snapshots)} 个月</b></div>
</div><div class="hero-actions">{export_buttons}</div></div></section>

<main contenteditable="true" spellcheck="false">
{quarterly_section}
<!-- 对比总览 -->
<section class="page" data-pmopage="overview">
<div class="section-head">
<div><h2>多月指标对比</h2><p>关键指标在各月间的变化趋势一览。</p></div>
</div>
{comparison_table_html}
<div class="chart-grid">{comparison_charts_html}</div>
</section>

<!-- 各月详情 -->
<section class="page" data-pmopage="monthly_detail">
<div class="section-head">
<div><h2>各月详细数据</h2><p>切换月份查看完整快报内容。</p></div>
</div>
<div class="month-tabs">{month_tabs}</div>
{month_panels}
</section>
</main>

<footer class="footer">项目管理快报 · 跨月合并版 · {html.escape(month_range_label)}</footer>

<script>
// Tab 切换
document.querySelectorAll('.month-tab').forEach(function(btn){{
  btn.addEventListener('click',function(){{
    document.querySelectorAll('.month-tab').forEach(function(b){{b.classList.remove('active')}});
    document.querySelectorAll('.month-panel').forEach(function(p){{p.style.display='none'}});
    btn.classList.add('active');
    var panel=document.getElementById('month-panel-'+btn.dataset.month);
    if(panel)panel.style.display='block';
  }});
}});

// ECharts 初始化
var charts=[];
document.querySelectorAll('div[data-option]').forEach(function(el){{
  try{{
    var opt=JSON.parse(el.getAttribute('data-option'));
    var c=echarts.init(el,null,{{renderer:'canvas'}});
    c.setOption(opt);charts.push(c);
  }}catch(e){{console.warn('Chart init error:',el.id,e)}}
}});
window.addEventListener('resize',function(){{charts.forEach(function(c){{c.resize()}})}});

/* ── 数据校准：跨月字段同步 ── */
window.__calibSync=function(el,monthIdx){{
  var id=el.id.replace('calib-m'+monthIdx+'_','');
  var val=el.value;
  document.querySelectorAll('input[id^="calib-m'+monthIdx+'_"]').forEach(function(inp){{
    if(inp!==el&&inp.id.endsWith('_'+id))inp.value=val;
  }});
}};

/* ── 数据校准重算（按当前激活月份） ── */
window.__recalc=function(monthIdx){{
  try{{
  if(monthIdx===undefined){{
    var ap=document.querySelector('.month-panel[style*="block"]');
    if(!ap)return;
    monthIdx=parseInt(ap.id.replace('month-panel-',''));
  }}
  var pfx='calib-m'+monthIdx+'_';
  var v=function(id){{var el=document.getElementById(pfx+id);return el?(parseFloat(el.value)||0):0}};
  var d={{company_headcount:v('company_headcount'),total_req_count:v('total_req_count'),actual_rd_man_hour:v('actual_rd_man_hour'),task_count:v('task_count'),task_finish_count:v('task_finish_count'),bug_total_count:v('bug_total_count'),field_bug_count:v('field_bug_count'),bug_closed_count:v('bug_closed_count'),bug_resolve_duration_sec:v('bug_resolve_duration_sec'),actual_total_man_hour:v('actual_total_man_hour'),plan_total_man_hour:v('plan_total_man_hour'),rd_actual_cost:v('rd_actual_cost'),rd_budget_cost:v('rd_budget_cost'),project_staff_count:v('project_staff_count'),monthly_work_days:v('monthly_work_days')}};
  var m={{}};
  m.rd_resource_input_ratio=d.company_headcount>0?(d.project_staff_count/d.company_headcount):null;
  m.requirement_cost=d.total_req_count>0?(d.actual_rd_man_hour/d.total_req_count):null;
  m.sprint_completion_rate=d.task_count>0?(d.task_finish_count/d.task_count):null;
  m.defect_escape_rate=d.bug_total_count>0?(d.field_bug_count/d.bug_total_count):null;
  m.bug_close_rate=d.bug_total_count>0?(d.bug_closed_count/d.bug_total_count):null;
  m.mttr=(d.bug_total_count>0&&d.bug_resolve_duration_sec>0)?(d.bug_resolve_duration_sec/d.bug_total_count/3600):null;
  m.workhour_deviation_rate=d.plan_total_man_hour>0?((d.actual_total_man_hour-d.plan_total_man_hour)/d.plan_total_man_hour):null;
  m.budget_deviation_rate=d.rd_budget_cost>0?(d.rd_actual_cost/d.rd_budget_cost-1):null;
  var totalInvestmentH=v('total_investment_hours');
  var overtimeH=v('overtime_hours');
  var totalStaffAllH=v('total_staff_all_hours');
  var newTotalInvestment=totalInvestmentH>0?(totalInvestmentH/174):0;
  var newCrossRatio=totalStaffAllH>0?((totalStaffAllH-d.actual_total_man_hour)/totalStaffAllH*100):0;
  var fmt=function(code,val){{
    if(val===null||isNaN(val))return'\u5f85\u63a5\u5165';
    if(code==='requirement_cost')return val.toFixed(2)+' \u4eba\u5929/\u9879';
    if(code==='mttr')return val.toFixed(1)+' \u5c0f\u65f6';
    if(code==='rd_resource_input_ratio')return (val*100).toFixed(1)+'%';
    if(['req_verify_rate','defect_escape_rate','bug_close_rate','sprint_completion_rate','workhour_deviation_rate','budget_deviation_rate'].indexOf(code)>=0)return (val*100).toFixed(1)+'%';
    return val.toFixed(2);
  }};
  var warn=function(code,val){{
    if(val===null||isNaN(val))return'gray';var a=Math.abs(val);
    if(code==='req_verify_rate')return val>=0.85?'green':val>=0.70?'yellow':'red';
    if(code==='sprint_completion_rate')return val>=0.85?'green':'yellow';
    if(code==='workhour_deviation_rate')return a<=0.15?'green':a<=0.25?'yellow':'red';
    if(code==='budget_deviation_rate')return a<=0.10?'green':a<=0.25?'yellow':'red';
    if(code==='defect_escape_rate')return val<=0.03?'green':val<=0.10?'yellow':'red';
    if(code==='bug_close_rate')return val>=0.95?'green':val>=0.80?'yellow':'red';
    if(code==='requirement_cost')return val<=3?'green':val<=5?'yellow':'red';
    if(code==='mttr')return val<=336?'green':val<=504?'yellow':'red';
    return'gray';
  }};
  var stateLabel={{green:'\u6b63\u5e38',yellow:'\u5173\u6ce8',red:'\u5e72\u9884',gray:'\u5f85\u8865'}};
  var panel=document.getElementById('month-panel-'+monthIdx);
  if(!panel)return;
  Object.keys(m).forEach(function(code){{
    var cards=panel.querySelectorAll('[data-metric="'+code+'"]');
    var lv=warn(code,m[code]);
    cards.forEach(function(card){{
      var el=card.querySelector('.metric-value');
      if(el)el.textContent=fmt(code,m[code]);
      ['green','yellow','red','gray'].forEach(function(c){{card.classList.remove(c);}});
      card.classList.add(lv);
      var dot=card.querySelector('.dot');if(dot){{['green','yellow','red','gray'].forEach(function(c){{dot.classList.remove(c);}});dot.classList.add(lv);}}
      var strong=card.querySelector('strong');if(strong){{var cs={{green:'var(--green)',yellow:'var(--yellow)',red:'var(--red)',gray:'var(--gray)'}};strong.style.color=cs[lv]||'';}}
    }});
  }});
  var alertRows=panel.querySelectorAll('.alert-table tbody tr');
  var nameMap={{'\u7f3a\u9677\u9003\u9038\u7387':'defect_escape_rate','Bug\u5173\u95ed\u7387':'bug_close_rate','MTTR':'mttr','\u9700\u6c42\u6210\u672c':'requirement_cost','Sprint\u5b8c\u6210\u7387':'sprint_completion_rate','\u7814\u53d1\u8d44\u6e90\u6295\u5165\u5360\u6bd4':'rd_resource_input_ratio','\u5de5\u65f6\u504f\u5dee\u7387':'workhour_deviation_rate','\u9884\u7b97\u504f\u5dee\u7387':'budget_deviation_rate'}};
  alertRows.forEach(function(row){{
    var name=row.cells[0]?.textContent.trim()||'';
    var code=nameMap[name];
    if(code&&m[code]!==undefined){{
      var valEl=row.cells[1];if(valEl)valEl.textContent=fmt(code,m[code]);
      var tagEl=row.cells[4];if(tagEl){{var lv=warn(code,m[code]);var sp=tagEl.querySelector('span');if(sp){{sp.className='tag '+lv;sp.textContent=stateLabel[lv]||lv;}}}}
    }}
  }});
  }}catch(e){{console.error('__recalc error:',e)}}
}};

</script>'''
    # ── 全局批注栏 + 交互脚本（批注/保存/颜色），与单月快报保持一致 ──
    _GLOBAL_RAIL = '<div class="pmo-anno-rail" id="pmo-anno-rail" aria-label="批注栏"></div>'
    _insert_pos = document.rfind("</body></html>")
    if _insert_pos >= 0:
        document = document[:_insert_pos] + _GLOBAL_RAIL + _PAGE_SAVE_JS + document[_insert_pos:]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")


def _build_month_detail_panel(snap: dict, month: str, index: int, allowed_pages=None, focus=None) -> str:
    """为单月 snapshot 构建详情面板 HTML。
    
    Args:
        allowed_pages: 解析后的页面 ID 集合，None 表示全部
        focus: 关键词过滤，只保留名称匹配的指标
    """
    proj = snap.get("project", {})
    quality_cur = snap.get("quality_current", {})
    bug_detail = snap.get("bug_detail", {})
    req_detail = snap.get("requirement_detail", {})
    manhour_common = snap.get("manhour_common", [])
    attendance_records = snap.get("attendance_records", [])

    metrics, _ = create_metrics(snap)
    # 按页面过滤指标
    if allowed_pages is not None:
        metrics = [m for m in metrics
                   if any(p in allowed_pages for p in METRIC_PAGE_MAP.get(m.code, []))]
    # 按 focus 关键词过滤指标
    if focus:
        focus_lower = focus.lower()
        metrics = [m for m in metrics if focus_lower in m.name.lower() or focus_lower in m.code.lower()]
    items = {item.code: item for item in metrics}

    # 状态统计
    counts = {level: sum(1 for item in metrics if item.warning_level == level)
              for level in ("normal", "concern", "intervention")}
    missing_count = sum(1 for item in metrics if item.warning_level == "unavailable")

    # 任务数据
    task_total = _num(proj.get("task_count"))
    task_done = _num(proj.get("task_finish_count"))
    task_delay = _num(proj.get("task_delay_count"))

    # 工时数据
    actual_hour = _num(proj.get("actual_total_man_hour"))
    plan_hour = _num(proj.get("plan_total_man_hour"))

    # 团队健康度
    daily_hours = defaultdict(float)
    for r in manhour_common:
        staff = r.get("staffName", "")
        date = r.get("reportDate", "")
        hours = _num(r.get("reportManHour"))
        if staff and date:
            daily_hours[(staff, date)] += hours
    overtime_hours = sum(max(0, total - 9) for total in daily_hours.values())
    total_available_hours = sum(_num(r.get("attendanceHour")) for r in attendance_records)

    proj_name = str(proj.get("project_name", ""))
    total_investment_hours = sum(
        _num(r.get("reportManHour")) for r in manhour_common
        if r.get("projectName") == proj_name
    )
    total_investment = total_investment_hours / 174

    avg_attendance = _num(proj.get("attendance_hour"))
    if not avg_attendance:
        proj_staff = set(
            r.get("staffName") for r in manhour_common
            if r.get("projectName") == proj_name and r.get("staffName")
        )
        proj_att_daily = defaultdict(float)
        for r in attendance_records:
            staff = r.get("staffName", "")
            date = r.get("workDate", "")
            if staff and date and staff in proj_staff:
                proj_att_daily[(staff, date)] += _num(r.get("attendanceHour"))
        proj_att_total = sum(proj_att_daily.values())
        proj_att_days = len(proj_att_daily)
        avg_attendance = proj_att_total / proj_att_days if proj_att_days else 0

    # Bug 数据
    total_bug = _num(bug_detail.get("bug_total_count"))
    closed_bug = _num(bug_detail.get("bug_closed_count"))

    # 校准面板所需的额外变量
    total_cost_actual = _num(proj.get("actual_dev_cost")) + _num(proj.get("actual_staff_cost"))
    total_cost_budget = _num(proj.get("plan_dev_cost")) + _num(proj.get("plan_staff_cost"))
    _staff_in_proj = set(
        r.get("staffName") for r in manhour_common
        if r.get("projectName") == proj_name and r.get("staffName")
    )
    total_staff_all_hours = sum(
        _num(r.get("reportManHour")) for r in manhour_common
        if r.get("staffName") in _staff_in_proj
    )
    _rd_note = items.get('rd_resource_input_ratio')
    _rd_note_text = _rd_note.note if _rd_note else ''
    _proj_staff_match = re.search(r'\u4eba\u5458\u603b\u6570\uff08(\d+)\u4eba\uff09', _rd_note_text)
    if not _proj_staff_match:
        _proj_staff_match = re.search(r'=\s*(\d+)', _rd_note_text)
    _proj_staff_count = int(_proj_staff_match.group(1)) if _proj_staff_match else len(_staff_in_proj)

    # ── 数据校准面板（覆盖全部 6 组指标） ──
    _calib_fields = {
        'company_headcount': {'label': '公司总人数', 'value': 0, 'unit': '人', 'decimals': 0},
        'total_req_count': {'label': '总需求数', 'value': _num(snap.get('resolved_total_requirement_count') or proj.get('totalReqCount') or task_done), 'unit': '个', 'decimals': 0},
        'actual_rd_man_hour': {'label': '研发工时', 'value': _num(proj.get('actual_rd_man_hour')), 'unit': '小时', 'decimals': 2},
        'task_count': {'label': '任务总数', 'value': _num(proj.get('task_count')), 'unit': '个', 'decimals': 0},
        'task_finish_count': {'label': '完成任务数', 'value': _num(proj.get('task_finish_count')), 'unit': '个', 'decimals': 0},
        'bug_total_count': {'label': 'Bug 总数', 'value': _num(bug_detail.get('bug_total_count')), 'unit': '个', 'decimals': 0},
        'field_bug_count': {'label': '线上 Bug 数', 'value': _num(bug_detail.get('field_bug_count')), 'unit': '个', 'decimals': 0},
        'bug_closed_count': {'label': '已关闭 Bug 数', 'value': _num(bug_detail.get('bug_closed_count')), 'unit': '个', 'decimals': 0},
        'bug_resolve_duration_sec': {'label': 'Bug 修复总时长', 'value': _num(bug_detail.get('bug_resolve_duration_exclude_reject_third') or bug_detail.get('bug_resolve_duration_seconds')), 'unit': '秒', 'decimals': 0},
        'actual_total_man_hour': {'label': '实际总工时', 'value': _num(proj.get('actual_total_man_hour')), 'unit': '小时', 'decimals': 2},
        'plan_total_man_hour': {'label': '计划总工时', 'value': _num(proj.get('plan_total_man_hour')), 'unit': '小时', 'decimals': 2},
        'rd_actual_cost': {'label': '研发成本实际', 'value': total_cost_actual, 'unit': '元', 'decimals': 0},
        'rd_budget_cost': {'label': '研发成本预算', 'value': total_cost_budget, 'unit': '元', 'decimals': 0},
        'project_staff_count': {'label': '项目人员数', 'value': _proj_staff_count, 'unit': '人', 'decimals': 0},
        'monthly_work_days': {'label': '月工作天数', 'value': 21.75, 'unit': '天', 'decimals': 2},
        'total_investment_hours': {'label': '本项目投入工时', 'value': total_investment_hours, 'unit': '小时', 'decimals': 1},
        'overtime_hours': {'label': '加班工时', 'value': overtime_hours, 'unit': '小时', 'decimals': 1},
        'total_staff_all_hours': {'label': '人员跨项目总工时', 'value': total_staff_all_hours, 'unit': '小时', 'decimals': 1},
    }
    def _calib_panel(title, groups, fields=_calib_fields):
        rows = ''
        for group_name, formula, field_ids in groups:
            rows += f'<tr><td class="calib-group" colspan="3">{group_name}</td></tr>'
            rows += f'<tr><td class="calib-formula" colspan="3">{formula}</td></tr>'
            for fid in field_ids:
                fd = fields[fid]
                v = f"{fd['value']:.{fd['decimals']}f}" if fd['value'] else '0'
                rows += f'<tr><td class="calib-label">{fd["label"]}</td><td class="calib-input"><input type="number" id="calib-m{index}_{fid}" value="{v}" step="any" oninput="__calibSync(this,{index});__recalc({index})"></td><td class="calib-unit">{fd["unit"]}</td></tr>'
        return f'<details class="calib-panel"><summary>{title}</summary><div class="calib-body"><table>{rows}</table></div></details>'
    _calib_groups = [
        ('Sprint 完成率', '完成任务数 / 任务总数', ['task_count', 'task_finish_count']),
        ('工时偏差率', '(实际总工时 - 计划总工时) / 计划总工时', ['actual_total_man_hour', 'plan_total_man_hour']),
        ('预算偏差率', '研发成本实际 / 研发成本预算 - 1', ['rd_actual_cost', 'rd_budget_cost']),
        ('人员投入健康度', '项目人员数 × 月工作天数 × 9', ['project_staff_count', 'monthly_work_days', 'total_investment_hours', 'overtime_hours', 'total_staff_all_hours']),
        ('缺陷逃逸率', '线上 Bug 数 / Bug 总数', ['bug_total_count', 'field_bug_count']),
        ('Bug 关闭率', '已关闭 Bug 数 / Bug 总数', ['bug_closed_count']),
        ('MTTR', 'Bug 修复总时长 / Bug 总数 / 3600', ['bug_resolve_duration_sec']),
        ('研发资源投入占比', '项目参与人员数 / 公司总人数', ['project_staff_count', 'company_headcount']),
        ('需求成本', '研发工时 / 总需求数（9h/人天）', ['total_req_count', 'actual_rd_man_hour']),
    ]
    calib_panel_html = _calib_panel('指标计算口径与数据校准', _calib_groups)

    # 指标卡片
    COLORS = {"normal": "green", "concern": "yellow", "intervention": "red", "unavailable": "gray"}
    LABELS = {"normal": "正常", "concern": "关注", "intervention": "干预", "unavailable": "待补"}
    TARGETS = {
        "req_verify_rate": "≥85%", "sprint_completion_rate": "≥85%", "workhour_deviation_rate": "±15%",
        "budget_deviation_rate": "±10%", "requirement_cost": "≤3人天",
        "defect_escape_rate": "≤3%", "bug_close_rate": "≥95%",
        "mttr": "P0≤4h P1≤24h", "rd_resource_input_ratio": "研发人力/组织总人数",
    }

    def _value(metric):
        if metric.display_value:
            return metric.display_value
        if metric.value is None:
            return "待接入"
        if metric.unit == "%":
            return f"{metric.value:.1%}"
        if metric.unit == "个":
            return f"{metric.value:.0f}"
        if metric.unit == "小时":
            return f"{metric.value:.2f} 小时"
        return f"{metric.value:.2f}"

    def _state(metric):
        level = metric.warning_level
        return LABELS.get(level, "待补"), COLORS.get(level, "gray")

    def _metric_card(metric):
        label, color = _state(metric)
        return f'''<article class="metric-card {color}" data-metric="{metric.code}">
<h3>{html.escape(metric.name)}</h3><strong class="metric-value">{html.escape(_value(metric))}</strong>
<p>目标：{html.escape(TARGETS.get(metric.code, "—"))}<span class="dot {color}"></span></p></article>'''

    cards = "".join(_metric_card(items[code]) for code in (
        "workhour_deviation_rate", "budget_deviation_rate", "sprint_completion_rate", "defect_escape_rate",
        "bug_close_rate", "req_verify_rate", "requirement_cost", "mttr", "rd_resource_input_ratio",
    ) if code in items)

    # 预警表格
    alerts = ""
    for metric in metrics:
        label, color = _state(metric)
        alerts += f'''<tr><td>{html.escape(metric.name)}</td><td>{html.escape(_value(metric))}</td>
<td>{html.escape(TARGETS.get(metric.code, "—"))}</td>
<td>{html.escape(metric.formula)}</td><td><span class="tag {color}">{label}</span></td></tr>'''

    # 关键数据摘要行（支持页面过滤）
    summary_rows = [
        ("产研管理", "任务完成/总数", f"{task_done:.0f} / {task_total:.0f}"),
        ("产研管理", "延期任务", f"{task_delay:.0f}"),
        ("投入偏差", "实际工时/计划工时", f"{actual_hour:.0f}h / {plan_hour:.0f}h"),
        ("人员健康", "人员总投入", f"{total_investment:.2f} 人月"),
        ("人员健康", "平均考勤时长", f"{avg_attendance:.1f}h"),
        ("人员健康", "加班工时", f"{overtime_hours:.1f}h"),
        ("人员健康", "总可用工时", f"{total_available_hours:.1f}h"),
        ("质量", "Bug 总数/已关闭", f"{total_bug:.0f} / {closed_bug:.0f}"),
    ]
    if allowed_pages is not None:
        summary_rows = [r for r in summary_rows
                        if _SUMMARY_DIMENSION_PAGE.get(r[0]) in allowed_pages]
    # focus 激活时，只保留与当前指标相关的摘要行
    if focus and metrics:
        focused_pages = set()
        for m in metrics:
            focused_pages.update(METRIC_PAGE_MAP.get(m.code, []))
        if focused_pages:
            summary_rows = [r for r in summary_rows
                            if _SUMMARY_DIMENSION_PAGE.get(r[0]) in focused_pages]
    summary_html = "".join(
        f'<tr><td>{html.escape(r[0])}</td><td>{html.escape(r[1])}</td><td>{html.escape(r[2])}</td></tr>'
        for r in summary_rows
    )

    panel = f'''
<div class="summary-grid">
<article class="status-card green"><b>{counts["normal"]}</b><p>正常指标</p></article>
<article class="status-card yellow"><b>{counts["concern"]}</b><p>关注指标</p></article>
<article class="status-card red"><b>{counts["intervention"]}</b><p>干预指标</p></article>
<article class="status-card gray"><b>{missing_count}</b><p>待补数据指标</p></article>
</div>

<div class="metric-grid">{cards}</div>

<div style="margin-top:24px">
<h3 style="margin:0 0 10px;font-size:18px">关键数据摘要</h3>
<table class="alert-table">
<thead><tr><th>维度</th><th>指标</th><th>值</th></tr></thead>
<tbody>{summary_html}</tbody>
</table>
</div>

<div style="margin-top:24px">
<h3 style="margin:0 0 10px;font-size:18px">PMO 预警清单</h3>
<table class="alert-table">
<thead><tr><th>指标名称</th><th>当前值</th><th>目标值</th><th>计算口径</th><th>预警等级</th></tr></thead>
<tbody>{alerts}</tbody>
</table>
</div>

{calib_panel_html}
'''
    return panel
