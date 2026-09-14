"""项目管理快报 — 智能体工具入口。

定义 Skill 对外暴露的工具函数，供 LangChain ReAct Agent 或自定义调度器调用。
每个工具函数内部调用 skill_runner 完成实际工作。

当接入公司智能体时，SKILL_TOOLS 列表注册到 Agent；
独立运行时，可直接调用函数或通过 CLI 入口 main() 使用。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

# 确保 Skill 根目录在 sys.path
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

logger = logging.getLogger(__name__)

try:
    from langchain_core.tools import tool
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False
    logger.warning("langchain_core 未安装: pip install langchain-core")


def _default_output_dir() -> str:
    """按当前请求用户分子目录落盘，便于下载 ACL。"""
    try:
        from storage.skill_output import user_output_dir, get_identity
        uid, _ = get_identity()
        return user_output_dir("report", uid or "anonymous")
    except Exception:
        p = _SKILL_DIR / "output" / "anonymous"
        p.mkdir(parents=True, exist_ok=True)
        return str(p)


def _format_pipeline_result(result: Any) -> str:
    """将 PipelineResult 格式化为对话可读文本。"""
    lines: list[str] = []
    success = getattr(result, "success", False)
    pname = getattr(result, "project_name", "") or ""
    pid = getattr(result, "project_id", "") or ""
    month = getattr(result, "month", "") or ""
    lines.append(f"{'✅ 成功' if success else '⚠️ 已完成（含告警/失败项）'}：项目管理快报")
    lines.append(f"项目：{pname}（ID={pid}）")
    lines.append(f"结算月：{month}")

    dq = getattr(result, "data_quality", None) or {}
    missing = dq.get("missing_fields") or []
    warnings = dq.get("warnings") or []
    if missing:
        # 不显示字段数（与 HTML 快报的指标数不一致），改为通用提示
        lines.append("有数据缺失（快报中已标「待补」）")
    if warnings:
        lines.append(f"数据告警：{len(warnings)} 条")

    outputs = getattr(result, "outputs", None) or {}
    if outputs:
        lines.append("")
        lines.append("生成文件：")
        for key, path in outputs.items():
            lines.append(f"- {key}: {path}")
    # 校验报告单独列出（确保始终显示）
    val = getattr(result, "validation_report_path", "") or ""
    if val:
        # 如果 outputs 中已有 validation 键，跳过重复添加
        if "validation" not in outputs:
            lines.append(f"- validation: {val}")

    errors = getattr(result, "errors", None) or []
    if errors:
        lines.append("")
        lines.append("错误/失败：")
        for e in errors[:8]:
            lines.append(f"- {e}")

    lines.append("")
    lines.append("可继续说「导出 PDF」或换项目/月份重新生成。")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# 工具函数定义
# ──────────────────────────────────────────────

def list_projects() -> list[dict]:
    """列出系统中所有可查询的项目。

    Returns:
        项目列表，每项包含 id、code、name 字段。
    """
    from data_modules.fetch_data import _get
    try:
        data = _get("project", "project_info", params={"id": "", "page": 1, "size": 200})
        records = data.get("data", {}).get("records", []) if isinstance(data, dict) else []
        return [
            {"id": r.get("id"), "code": r.get("code", ""), "name": r.get("name", "")}
            for r in records
        ]
    except Exception as e:
        return [{"error": str(e)}]


def fetch_project_data(
    project_id: str,
    project_name: str,
    settlement_month: str,
    as_of_date: Optional[str] = None,
) -> dict:
    """拉取指定项目的统一数据快照。

    Args:
        project_id: 项目 ID
        project_name: 项目名称
        settlement_month: 结算月份，格式 YYYY-MM
        as_of_date: 数据截至日期 YYYY-MM-DD，默认今天

    Returns:
        ProjectMonthlySnapshot dict
    """
    from data_modules.fetch_data import fetch
    return fetch(
        project_id=project_id,
        project_name=project_name,
        settlement_month=settlement_month,
        as_of_date=as_of_date,
    )


def generate_html_report(
    snapshot: dict,
    output_dir: str = "output",
) -> str:
    """从数据快照生成交互式 HTML 快报。

    Args:
        snapshot: ProjectMonthlySnapshot dict（由 fetch_project_data 返回）
        output_dir: 输出目录路径

    Returns:
        生成的 HTML 文件路径
    """
    from export_modules.report_runner import generate_html
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = generate_html(snapshot, out)
    return str(path)


def generate_pdf_report(
    snapshot: dict,
    output_dir: str = "output",
) -> str:
    """从数据快照生成 PDF 打印版快报。

    Args:
        snapshot: ProjectMonthlySnapshot dict
        output_dir: 输出目录路径

    Returns:
        生成的 PDF 文件路径
    """
    from export_modules.report_runner import generate_pdf_reportlab
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = generate_pdf_reportlab(snapshot, out)
    return str(path)


def generate_metrics(
    snapshot: dict,
    output_dir: str = "output",
) -> str:
    """从数据快照计算指标并输出 JSON。

    Args:
        snapshot: ProjectMonthlySnapshot dict
        output_dir: 输出目录路径

    Returns:
        生成的指标 JSON 文件路径
    """
    from export_modules.report_runner import generate_metrics_json
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = generate_metrics_json(snapshot, out)
    return str(path)


def run_full_pipeline(
    project_id: str,
    project_name: str,
    settlement_month: str,
    output_dir: str = "output",
    formats: Optional[list[str]] = None,
    page_ids: Optional[list[str]] = None,
    focus: Optional[str] = None,
    columns: Optional[list[str]] = None,
    output_format: Optional[str] = None,
) -> dict[str, str]:
    """完整流水线：拉取数据 → 计算指标 → 生成快报。

    Args:
        project_id: 项目 ID
        project_name: 项目名称
        settlement_month: 结算月份 YYYY-MM
        output_dir: 输出目录
        formats: 输出格式列表（向后兼容）
        page_ids: 页面选择，如 ["p01","p03"]，None=全部
        focus: 行过滤关键词
        columns: 列过滤
        output_format: 输出格式字符串，如 "all"/"html pdf"/"pptx"

    Returns:
        {"snapshot": 路径, "html": 路径, "pdf": 路径, ...}
    """
    from data_modules.fetch_data import fetch
    from export_modules.report_runner import (
        generate_html, generate_pdf_reportlab, generate_docx,
        generate_pptx, generate_metrics_json, generate_validation,
        _resolve_output_format,
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}

    # 1. 拉取数据
    from export_modules.report_runner import safe_filename
    snap = fetch(project_id=project_id, project_name=project_name, settlement_month=settlement_month)
    snap_path = out / f"{safe_filename(project_name)}_{settlement_month}_snapshot.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    result["snapshot"] = str(snap_path)

    # 2. 解析格式
    fmt_list = formats or ["html", "pdf", "docx", "metrics"]
    if output_format:
        fmt_list = _resolve_output_format(output_format)
        if "metrics" not in fmt_list:
            fmt_list.append("metrics")

    # 3. 生成快报
    if "html" in fmt_list:
        result["html"] = str(generate_html(snap, out, page_ids=page_ids, focus=focus, columns=columns))
    if "pdf" in fmt_list:
        try:
            result["pdf"] = str(generate_pdf_reportlab(snap, out, page_ids=page_ids))
        except Exception as e:
            result["pdf"] = f"ERROR: {e}"
    if "docx" in fmt_list:
        try:
            result["docx"] = str(generate_docx(snap, out, page_ids=page_ids))
        except Exception as e:
            result["docx"] = f"ERROR: {e}"
    if "pptx" in fmt_list:
        try:
            result["pptx"] = str(generate_pptx(snap, out, page_ids=page_ids))
        except Exception as e:
            result["pptx"] = f"ERROR: {e}"
    if "metrics" in fmt_list:
        result["metrics"] = str(generate_metrics_json(snap, out))

    # 4. 数据校验报告（始终生成）
    try:
        result["validation"] = str(generate_validation(snap, out))
    except Exception as e:
        result["validation"] = f"ERROR: {e}"

    return result


def generate_page_report_tool(
    project_id: str,
    project_name: str,
    settlement_month: str,
    page_ids: Optional[list[str]] = None,
    focus: Optional[str] = None,
    columns: Optional[list[str]] = None,
    output_format: str = "all",
    output_dir: str = "output",
) -> dict[str, str]:
    """按需切片导出：选择页面、过滤行列、多格式输出。

    Args:
        project_id: 项目 ID
        project_name: 项目名称
        settlement_month: 结算月份 YYYY-MM
        page_ids: 页面 ID 列表，如 ["p01","p03"]，None=全部
        focus: 行过滤关键词
        columns: 列过滤
        output_format: html/pdf/docx/pptx/all
        output_dir: 输出目录

    Returns:
        {format: filepath} 字典
    """
    return run_full_pipeline(
        project_id=project_id,
        project_name=project_name,
        settlement_month=settlement_month,
        output_dir=output_dir,
        page_ids=page_ids,
        focus=focus,
        columns=columns,
        output_format=output_format,
    )


def list_available_pages() -> list[dict]:
    """列出所有可用页面模块。

    Returns:
        页面列表，每项包含 page_id、title、core_question
    """
    from page_modules import PAGE_REGISTRY
    result = []
    for page_id, (mod_path, cls_name, title, domain_index) in sorted(
        PAGE_REGISTRY.items(), key=lambda x: x[1][3]
    ):
        result.append({
            "page_id": page_id,
            "title": title,
            "domain_index": domain_index,
        })
    return result


def _run_project_mgmt_skill_impl(query: str) -> str:
    """自然语言入口实现（供 @tool 与无 LangChain 回退共用）。"""
    import importlib.util

    # 避免与经营快报等同名 skill_runner 冲突：按文件路径加载本 Skill 模块
    # 支持热更新：检查源文件 mtime，源码变更后自动重新加载
    def _load_local(mod_key: str, filename: str):
        path = _SKILL_DIR / filename
        name = f"pm_{mod_key}"
        if name in sys.modules:
            cached = sys.modules[name]
            try:
                src_mtime = path.stat().st_mtime
                if src_mtime > getattr(cached, "_loaded_at", 0):
                    logger.info(f"检测到 {name} 源码更新，执行热重载")
                    spec2 = importlib.util.spec_from_file_location(name, path)
                    cached = importlib.util.module_from_spec(spec2)
                    cached.__file__ = str(path)
                    sys.modules[name] = cached
                    assert spec2.loader is not None
                    spec2.loader.exec_module(cached)
                    cached._loaded_at = src_mtime
                    return cached
            except Exception:
                pass
            return cached
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        mod.__file__ = str(path)
        sys.modules[name] = mod
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        try:
            mod._loaded_at = path.stat().st_mtime
        except Exception:
            pass
        return mod

    skill_runner = _load_local("skill_runner", "skill_runner.py")
    resolve_intent = skill_runner.resolve_intent
    run_pipeline = skill_runner.run_pipeline
    run_pipeline_multi = skill_runner.run_pipeline_multi

    intent = resolve_intent(query or "")
    out_dir = _default_output_dir()

    if intent.get("action") == "list":
        projects = list_projects()
        if projects and projects[0].get("error"):
            return f"拉取项目列表失败：{projects[0]['error']}"
        if not projects:
            return "未查询到可生成快报的项目，请确认数据源连接正常。"
        total = len(projects)
        page_size = 12

        # ── 生成带分页按钮的 HTML 页面 ──
        import html as _html
        out_dir = _default_output_dir()
        html_path = Path(out_dir) / "项目列表.html"
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        _rows_html = "\n".join(
            f'<tr data-idx="{i}"><td>{i}</td>'
            f'<td>{_html.escape(str(p.get("id","")))}</td>'
            f'<td>{_html.escape(str(p.get("code","") or "-"))}</td>'
            f'<td>{_html.escape(str(p.get("name","")))}</td></tr>'
            for i, p in enumerate(projects, 1)
        )

        _html_content = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>项目列表</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f5f7fa;color:#1f2937;padding:24px}}
.container{{max-width:900px;margin:0 auto}}
h1{{font-size:20px;font-weight:600;margin-bottom:4px}}
.subtitle{{color:#6b7280;font-size:13px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
th{{background:#f0f4f8;padding:10px 14px;text-align:left;font-size:13px;font-weight:600;color:#4b5563;border-bottom:2px solid #e5e7eb}}
td{{padding:9px 14px;font-size:13px;border-bottom:1px solid #f0f0f0}}
tr:hover{{background:#f8fafc}}
tr:nth-child(even){{background:#fafbfc}}
tr:nth-child(even):hover{{background:#f0f4f8}}
.pagination{{display:flex;justify-content:center;align-items:center;gap:6px;margin-top:18px;flex-wrap:wrap}}
.pagination button{{min-width:36px;height:36px;border:1px solid #d1d5db;border-radius:8px;background:#fff;color:#374151;font-size:13px;cursor:pointer;transition:all .15s}}
.pagination button:hover:not(:disabled){{background:#2563eb;color:#fff;border-color:#2563eb}}
.pagination button.active{{background:#2563eb;color:#fff;border-color:#2563eb;font-weight:600}}
.pagination button:disabled{{opacity:.4;cursor:default}}
.page-info{{font-size:13px;color:#6b7280;margin:0 8px}}
.tip{{margin-top:16px;padding:12px 16px;background:#eff6ff;border-radius:8px;font-size:13px;color:#1e40af}}
</style></head><body>
<div class="container">
<h1>项目列表</h1>
<div class="subtitle">共 {total} 个项目 · 回复「项目名/ID + 月份」生成快报</div>
<table><thead><tr><th style="width:60px">序号</th><th style="width:80px">ID</th><th style="width:140px">编号</th><th>项目名称</th></tr></thead>
<tbody>{_rows_html}</tbody></table>
<div class="pagination" id="pagination"></div>
<div class="tip">💡 直接回复项目名称关键词或 ID 即可生成快报，如「车载终端 2026年8月」</div>
</div>
<script>
(function(){{
  var PAGE_SIZE={page_size},total={total};
  var rows=document.querySelectorAll('tbody tr');
  var totalPages=Math.ceil(total/PAGE_SIZE);
  var curPage=1;
  function showPage(n){{
    curPage=n;
    var start=(n-1)*PAGE_SIZE,end=Math.min(start+PAGE_SIZE,total);
    for(var i=0;i<rows.length;i++){{
      rows[i].style.display=(i>=start&&i<end)?'':'none';
    }}
    renderPagination();
  }}
  function renderPagination(){{
    var el=document.getElementById('pagination');
    var h='';
    h+='<button '+(curPage<=1?'disabled ':'')+' onclick="void(0)" id="prevBtn">‹ 上一页</button>';
    var pages=[];
    if(totalPages<=7){{for(var i=1;i<=totalPages;i++)pages.push(i);}}
    else{{
      pages.push(1);
      if(curPage>3)pages.push('...');
      for(var i=Math.max(2,curPage-1);i<=Math.min(totalPages-1,curPage+1);i++)pages.push(i);
      if(curPage<totalPages-2)pages.push('...');
      pages.push(totalPages);
    }}
    for(var j=0;j<pages.length;j++){{
      var p=pages[j];
      if(p==='...')h+='<span class="page-info">…</span>';
      else h+='<button class="'+(p===curPage?'active':'')+'" data-p="'+p+'">'+p+'</button>';
    }}
    h+='<button '+(curPage>=totalPages?'disabled ':'')+' onclick="void(0)" id="nextBtn">下一页 ›</button>';
    h+='<span class="page-info">第 '+curPage+'/'+totalPages+' 页</span>';
    el.innerHTML=h;
    el.querySelectorAll('button[data-p]').forEach(function(b){{
      b.addEventListener('click',function(){{showPage(+this.dataset.p);}});
    }});
    var prev=document.getElementById('prevBtn');
    var next=document.getElementById('nextBtn');
    if(prev)prev.addEventListener('click',function(){{if(curPage>1)showPage(curPage-1);}});
    if(next)next.addEventListener('click',function(){{if(curPage<totalPages)showPage(curPage+1);}});
  }}
  showPage(1);
}})();
</script></body></html>'''

        html_path.write_text(_html_content, encoding="utf-8")

        # 同时返回简要文本摘要（聊天窗口展示）
        total_pages = max(1, (total + page_size - 1) // page_size)
        lines = [f"共 {total} 个项目（回复「项目名/ID + 月份」生成快报）：", ""]
        _id_w = max(len(str(p.get('id', '?'))) for p in projects)
        _id_w = max(_id_w, 2)
        _code_w = max(len(str(p.get('code', '-') or '-')) for p in projects)
        _code_w = max(_code_w, 4)
        lines.append(f"| {'序号':>4} | {'ID':^{_id_w}} | {'编号':<{_code_w}} | 项目名称")
        lines.append(f"|{'-'*6}|{'-'*(_id_w+2)}|{'-'*(_code_w+2)}|{'-'*20}")
        for i, p in enumerate(projects[:page_size], 1):
            _id = str(p.get('id', '?'))
            _code = str(p.get('code') or '-')
            _name = p.get('name', '?')
            lines.append(f"| {i:>4} | {_id:^{_id_w}} | {_code:<{_code_w}} | {_name}")
        lines.append("")
        if total > page_size:
            lines.append(f"… 仅显示前 {page_size} 条，完整列表已生成：{html_path}")
            lines.append("可回复「项目列表 第2页」查看下一页，或在浏览器中打开完整列表。")
        lines.append("提示：直接回复项目名称关键词或 ID 即可生成快报，如「车载终端 2026年8月」")
        return "\n".join(lines)

    candidates = intent.get("_candidates") or []
    if len(candidates) > 1 and not intent.get("project_id"):
        lines = ["匹配到多个项目，请回复要生成的项目 ID 或完整名称：", ""]
        for c in candidates[:15]:
            lines.append(f"- {c.get('name')}（ID={c.get('id')}）")
        return "\n".join(lines)

    project_id = intent.get("project_id")
    project_name = intent.get("project_name")
    month = intent.get("month")
    month_range = intent.get("month_range")
    page_ids = intent.get("page_ids")

    # 过滤把技能名本身当成项目名的情况（如「生成项目管理快报」→「管理」）
    _WEAK_NAMES = {
        "管理", "快报", "月报", "报告", "指标", "PMO", "pmo",
        "项目管理", "项目快报", "项目月报", "经营",
    }
    if project_name and str(project_name).strip() in _WEAK_NAMES and not project_id:
        project_name = None

    missing = []
    if not project_id and not project_name:
        missing.append("项目名称或项目 ID")
    if not month and not month_range:
        missing.append("结算月份（如 2026-07 / 2026年7月）")
    if missing:
        return (
            "生成项目管理快报还需要：\n- "
            + "\n- ".join(missing)
            + "\n\n示例：「生成示例项目 2025年1月项目管理快报」或「列出项目」。"
        )

    # ── 项目存在性验证 ──
    # resolve_intent 已匹配但 ID 未命中
    if intent.get("_project_not_found"):
        label = project_id or project_name or ""
        return (
            f"❗ 未找到项目（输入：{label}）。\n"
            f"请检查项目 ID 或名称是否正确，可回复「列出项目」查看可用项目列表。"
        )
    # 有 project_id 且 resolve_intent 成功匹配了 project_name → 通过
    # 有 project_id 但没有 project_name → ID 未在 API 列表中找到
    if project_id and not project_name:
        return (
            f"❗ 未找到 ID={project_id} 对应的项目。\n"
            f"请检查项目 ID 是否正确，可回复「列出项目」查看可用项目列表。"
        )
    # 只有 project_name（无 project_id），且 resolve_intent 未能匹配 → 验证名称
    if project_name and not project_id:
        try:
            _proj_list = list_projects()
            _all_names = [p.get("name", "") for p in _proj_list if not p.get("error")]
            # 精确匹配或子串匹配
            _matched = [n for n in _all_names
                        if project_name in n or n in project_name]
            if not _matched:
                return (
                    f"❗ 未找到名称包含「{project_name}」的项目。\n"
                    f"请检查项目名称是否正确，可回复「列出项目」查看可用项目列表。"
                )
        except Exception:
            pass  # API 异常时不阻止后续流程（fetch 内部会再尝试）

    formats = intent.get("formats") or ["html", "pdf", "docx", "metrics"]
    try:
        if month_range and len(month_range) > 1:
            result = run_pipeline_multi(
                project_id=str(project_id or ""),
                project_name=str(project_name or ""),
                month_range=list(month_range),
                output_dir=out_dir,
                formats=formats,
                page_ids=page_ids,
            )
        else:
            result = run_pipeline(
                project_id=str(project_id or ""),
                project_name=str(project_name or ""),
                settlement_month=str(month),
                output_dir=out_dir,
                formats=formats,
                page_ids=page_ids,
            )
        return _format_pipeline_result(result)
    except Exception as e:
        logger.exception("项目管理快报执行失败")
        return f"项目管理快报执行失败：{e}"


if HAS_LANGCHAIN:
    @tool
    def run_project_mgmt_skill(query: str) -> str:
        """用自然语言生成项目管理月度快报（PMO）。

        自动解析项目名/ID、结算月份与导出格式，拉取 example-platform 数据并生成
        HTML/PDF/Word。缺少项目或月份时会提示补充；说「列出项目」可先看清单。

        Args:
            query: 自然语言，如「生成示例项目 2025年1月项目管理快报」
        """
        return _run_project_mgmt_skill_impl(query)
else:
    def run_project_mgmt_skill(query: str) -> str:  # type: ignore
        return _run_project_mgmt_skill_impl(query)


# ──────────────────────────────────────────────
# 工具注册表（兼容 LangChain @tool 格式）
# ──────────────────────────────────────────────

SKILL_TOOLS = [
    {
        "name": "list_projects",
        "description": "列出系统中所有可查询的项目",
        "function": list_projects,
    },
    {
        "name": "fetch_project_data",
        "description": "拉取指定项目的统一数据快照（需要 project_id, project_name, settlement_month）",
        "function": fetch_project_data,
    },
    {
        "name": "generate_html_report",
        "description": "从数据快照生成交互式 HTML 快报",
        "function": generate_html_report,
    },
    {
        "name": "generate_pdf_report",
        "description": "从数据快照生成 PDF 打印版快报",
        "function": generate_pdf_report,
    },
    {
        "name": "generate_metrics",
        "description": "从数据快照计算 9 项 PMO 指标并输出 JSON",
        "function": generate_metrics,
    },
    {
        "name": "run_full_pipeline",
        "description": "完整流水线：拉取数据 + 生成多格式快报（支持 page_ids/focus/columns/output_format）",
        "function": run_full_pipeline,
    },
    {
        "name": "generate_page_report",
        "description": "按需切片导出：选择页面、过滤行列、多格式输出（html/pdf/docx/pptx/all）",
        "function": generate_page_report_tool,
    },
    {
        "name": "list_available_pages",
        "description": "列出所有可用页面模块（ID + 名称 + 序号）",
        "function": list_available_pages,
    },
    {
        "name": "run_project_mgmt_skill",
        "description": "自然语言入口：解析意图并生成项目管理快报",
        "function": run_project_mgmt_skill,
    },
]


def get_tool(name: str):
    """按名称查找工具函数。"""
    for tool_def in SKILL_TOOLS:
        if tool_def["name"] == name:
            return tool_def["function"]
    raise KeyError(f"未知工具: {name}")


# ──────────────────────────────────────────────
# LangChain Agent 创建（可选，需安装 langchain）
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个项目管理快报助手。你可以帮助用户：
1. 查看系统中有哪些项目
2. 为指定项目和月份生成项目管理月度快报
3. 输出 HTML 交互版、PDF 打印版和指标 JSON

工作流程：
- 如果用户没有提供项目 ID，先调用 list_projects 让用户选择
- 如果用户没有提供月份，要求用户补充（格式 YYYY-MM）
- 确认项目和月份后，调用 run_full_pipeline 一次性生成所有输出
- 返回生成的文件路径给用户

注意事项：
- 认证信息从环境变量读取，不要在回复中暴露任何 token 或 cookie
- 如果 API 返回 401，检查 PMO_API_USERNAME/PMO_API_PASSWORD 环境变量是否正确设置
- 数据缺失时在快报中标记"待补"，不编造数据
"""


def create_skill_agent(llm=None):
    """创建 LangChain ReAct Agent（需要安装 langchain）。

    Args:
        llm: LangChain LLM 实例。如果为 None，尝试从 agents/openai.yaml 创建。

    Returns:
        LangChain AgentExecutor
    """
    try:
        from langchain.agents import create_react_agent, AgentExecutor
        from langchain_core.tools import StructuredTool
    except ImportError:
        raise ImportError(
            "需要安装 langchain: pip install langchain langchain-openai"
        )

    lc_tools = []
    for tool_def in SKILL_TOOLS:
        func = tool_def["function"]
        if hasattr(func, "invoke"):
            lc_tools.append(func)
            continue
        lc_tools.append(
            StructuredTool.from_function(
                func=func,
                name=tool_def["name"],
                description=tool_def["description"],
            )
        )

    agent = create_react_agent(llm, lc_tools, prompt=SYSTEM_PROMPT)
    return AgentExecutor(agent=agent, tools=lc_tools, verbose=True)
