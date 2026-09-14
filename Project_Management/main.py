"""项目管理快报 Skill 入口
========================
支持多种调用方式：

1. Skill 模式（推荐，基于 page_modules 独立模块）:
   python main.py run --project-id 10001 --project-name "示例项目" --month 2025-01
   python main.py run --project-id 10001 --project-name "示例项目" --month 2025-01 --format html pdf

2. 交互模式（选择项目 + 输入月份）:
   python main.py interactive
   python main.py interactive --list

3. 从 snapshot 生成:
   python main.py from-snapshot --snapshot output/snapshot.json --format all
"""

import sys
import json
import logging
import webbrowser
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 确保 Skill 根目录在 sys.path
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


def run_pipeline_mode(args):
    """流水线模式：指定项目 + 月份，一键生成。支持跨月范围。"""
    from skill_runner import run_pipeline, run_pipeline_multi

    month_str = args.month

    # 解析页面选择
    page_ids = getattr(args, 'pages', None)
    focus = getattr(args, 'focus', None)
    columns = getattr(args, 'columns', None)
    output_format = getattr(args, 'output_format', None)

    # 检测月份范围：支持 2026-06:2026-07 / 2026-06~2026-07 / 2026-06到2026-07
    import re
    range_match = re.search(r'(\d{4}-\d{2})\s*[到至:~\-]+\s*(\d{4}-\d{2})', month_str)
    if range_match:
        # 跨月模式
        from skill_runner import _expand_month_range
        start_m, end_m = range_match.group(1), range_match.group(2)
        month_range = _expand_month_range(start_m, end_m)
        print(f"[INFO] 跨月模式: {month_range[0]} ~ {month_range[-1]} ({len(month_range)} 个月)")
        result = run_pipeline_multi(
            project_id=args.project_id,
            project_name=args.project_name,
            month_range=month_range,
            output_dir=args.output_dir,
            formats=args.format,
        )
    else:
        result = run_pipeline(
            project_id=args.project_id,
            project_name=args.project_name,
            settlement_month=month_str,
            output_dir=args.output_dir,
            formats=args.format,
            page_ids=page_ids,
            focus=focus,
            columns=columns,
            output_format=output_format,
        )

    print(f"\n{'='*60}")
    print(f"  状态: {'✓ 成功' if result.success else '✗ 失败'}")
    print(f"  项目: {result.project_name} (ID={result.project_id})")
    print(f"  月份: {result.month}")
    print(f"  数据质量: missing={len(result.data_quality.get('missing_fields', []))}, "
          f"warnings={len(result.data_quality.get('warnings', []))}")
    print(f"  页面模块: {len(result.page_contexts)} 个")
    for key, path in result.outputs.items():
        print(f"  {key:>8}: {path}")
    if result.errors:
        print(f"  错误:")
        for err in result.errors:
            print(f"    - {err}")

    # 自动打开 HTML
    html_path = result.outputs.get("html")
    if html_path:
        webbrowser.open(Path(html_path).resolve().as_uri())
        print(f"\n  快报已在浏览器中打开。")

    return result


def interactive_mode(args):
    """交互模式：选择项目 → 输入月份 → 生成报告 → 浏览器打开。"""
    from data_modules.fetch_data import fetch, _get

    # 拉取项目列表
    print("[INFO] 正在从 API 获取项目列表...")
    try:
        data = _get("project", "project_info", params={"id": "", "page": 1, "size": 200})
        projects = data.get("data", {}).get("records", []) if isinstance(data, dict) else []
    except Exception as e:
        print(f"[ERROR] 拉取项目列表失败: {e}", file=sys.stderr)
        return

    if not projects:
        print("[ERROR] 未获取到任何项目，请检查 API 凭证。", file=sys.stderr)
        return

    print(f"\n{'='*70}")
    print(f"  系统中共有 {len(projects)} 个项目")
    print(f"{'='*70}")
    print(f"  {'序号':>4}  {'ID':<8} {'编码':<16} {'名称'}")
    print(f"  {'-'*4}  {'-'*8} {'-'*16} {'-'*30}")
    for i, p in enumerate(projects, 1):
        print(f"  {i:>4}  {str(p.get('id','?')):<8} {p.get('code','?'):<16} {p.get('name','?')}")
    print()

    if args.list:
        return

    # 选择项目
    print("  提示: 输入序号/项目ID/名称关键字，q 退出")
    while True:
        raw = input("请选择项目: ").strip()
        if raw.lower() == "q":
            return
        if not raw:
            continue

        # 按序号
        try:
            idx = int(raw)
            if 1 <= idx <= len(projects):
                chosen = projects[idx - 1]
                break
        except ValueError:
            pass

        # 按 ID
        matched = [p for p in projects if str(p.get("id", "")) == raw]
        if matched:
            chosen = matched[0]
            break

        # 按名称
        matches = [p for p in projects if raw in p.get("name", "")]
        if len(matches) == 1:
            chosen = matches[0]
            break
        elif len(matches) > 1:
            print(f"  找到 {len(matches)} 个匹配，请缩小范围:")
            for p in matches:
                print(f"    ID={p.get('id')}  {p.get('name')}")
        else:
            print("  未找到，请重试。")

    project_id = str(chosen.get("id", ""))
    project_name = str(chosen.get("name", ""))
    print(f"  已选择: ID={project_id}  名称={project_name}")

    # 选择月份
    from datetime import datetime
    default_month = datetime.now().strftime("%Y-%m")
    raw_month = input(f"请输入结算月份 YYYY-MM（默认 {default_month}）: ").strip()
    month = raw_month if raw_month else default_month

    # 执行流水线
    print(f"\n[INFO] 项目: {project_name} (ID={project_id}), 月份: {month}")
    print("[1/3] 拉取 API 数据...")
    snap = fetch(project_id=project_id, project_name=project_name, settlement_month=month)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    from export_modules.report_runner import safe_filename
    snap_path = out / f"{safe_filename(project_name)}_{month}_snapshot.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[2/3] 生成快报...")
    from export_modules.report_runner import generate_html, generate_pdf_reportlab

    try:
        generate_pdf_reportlab(snap, out)
    except Exception:
        pass
    html_path = generate_html(snap, out)

    print("[3/3] 打开浏览器...")
    webbrowser.open(html_path.resolve().as_uri())

    print(f"\n{'='*70}")
    print(f"  快报已在浏览器中打开")
    print(f"  HTML: {html_path}")
    print(f"{'='*70}")


def from_snapshot_mode(args):
    """从已有 snapshot 生成快报。"""
    from export_modules.report_runner import generate_html, generate_pdf_reportlab, generate_metrics_json

    snap_path = Path(args.snapshot)
    if not snap_path.exists():
        print(f"错误：找不到 {snap_path}", file=sys.stderr)
        return

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    proj = snap.get("project", {})
    print(f"项目：{proj.get('project_name', '?')} ({proj.get('project_id', '?')})")
    print(f"月份：{proj.get('stat_month', '?')}")

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    fmt = args.format or ["all"]
    if "all" in fmt or "html" in fmt:
        print(f"[OK] HTML: {generate_html(snap, out)}")
    if "all" in fmt or "pdf" in fmt:
        try:
            print(f"[OK] PDF:  {generate_pdf_reportlab(snap, out)}")
        except Exception as e:
            print(f"[WARN] PDF 生成失败: {e}", file=sys.stderr)
    if "all" in fmt or "metrics" in fmt:
        print(f"[OK] JSON: {generate_metrics_json(snap, out)}")


def query_mode(args):
    """自然语言查询模式：用自然语言生成快报。"""
    from skill_runner import resolve_intent, run_pipeline

    query_text = " ".join(args.query)
    print(f"[INFO] 查询: {query_text}")
    print("[1/3] 解析意图...")

    intent = resolve_intent(query_text)
    action = intent.get("action", "pipeline")

    print(f"  动作: {action}")
    print(f"  项目: {intent.get('project_name', '?')} (ID={intent.get('project_id', '?')})")
    print(f"  月份: {intent.get('month', '?')}")
    print(f"  格式: {intent.get('formats', [])}")

    # 多候选提示
    candidates = intent.get("_candidates")
    if candidates and len(candidates) > 1:
        print(f"\n  [WARN] 找到 {len(candidates)} 个匹配项目:")
        for c in candidates:
            print(f"    ID={c['id']}  {c['name']}")
        print(f"  已选择第一个，如需其他请用项目 ID 精确指定。\n")

    if action == "list":
        from data_modules.fetch_data import _get
        data = _get("project", "project_info", params={"id": "", "page": 1, "size": 200})
        projects = data.get("data", {}).get("records", []) if isinstance(data, dict) else []
        print(f"\n共 {len(projects)} 个项目:")
        for p in projects:
            print(f"  {str(p.get('id', '?')):>8}  {p.get('code', '?'):<16}  {p.get('name', '?')}")
        return

    # 检查必要参数
    pid = intent.get("project_id")
    pname = intent.get("project_name")
    month = intent.get("month")
    if not pid or not pname:
        print("\n[ERROR] 未能识别项目，请提供更完整的项目名称或 ID。")
        print("  示例: '帮我生成智联车载IoT 2026-07 的快报'")
        print("  示例: '查看119项目7月的月报'")
        return
    if not month:
        from datetime import datetime
        month = datetime.now().strftime("%Y-%m")
        print(f"  [INFO] 未指定月份，默认使用 {month}")

    print(f"[2/3] 执行流水线...")

    # 检查是否为跨月范围
    month_range = intent.get("month_range")
    if month_range and len(month_range) > 1:
        from skill_runner import run_pipeline_multi
        print(f"  [INFO] 跨月合并模式: {month_range[0]} ~ {month_range[-1]}")
        result = run_pipeline_multi(
            project_id=pid,
            project_name=pname,
            month_range=month_range,
            output_dir=args.output_dir,
            formats=intent.get("formats"),
        )
    else:
        result = run_pipeline(
            project_id=pid,
            project_name=pname,
            settlement_month=month,
            output_dir=args.output_dir,
            formats=intent.get("formats"),
        )

    print(f"\n[3/3] 结果:")
    print(f"  状态: {'✓ 成功' if result.success else '✗ 失败'}")
    for fmt, path in result.outputs.items():
        print(f"  {fmt.upper():>8}: {path}")
    if result.errors:
        for err in result.errors:
            print(f"  ERROR: {err}")

    # 自动打开 HTML
    html_path = result.outputs.get("html")
    if html_path:
        webbrowser.open(Path(html_path).resolve().as_uri())
        print(f"\n  快报已在浏览器中打开。")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="项目管理快报 Skill")
    sub = parser.add_subparsers(dest="command")

    # query（自然语言查询模式）
    p_query = sub.add_parser("query", help="自然语言查询（如：帮我生成智联车载IoT 7月的快报）")
    p_query.add_argument("query", nargs="+", help="自然语言查询文本")
    p_query.add_argument("--output-dir", default="output", help="输出目录")

    # run（流水线模式）
    p_run = sub.add_parser("run", help="执行完整流水线（拉取数据 + 生成快报）")
    p_run.add_argument("--project-id", required=True, help="项目 ID")
    p_run.add_argument("--project-name", required=True, help="项目名称")
    p_run.add_argument("--month", required=True, help="结算月份 YYYY-MM，或范围如 2026-06:2026-07")
    p_run.add_argument("--output-dir", default="output", help="输出目录")
    p_run.add_argument("--format", nargs="+", default=None,
                       help="输出格式: html pdf docx pptx metrics（默认全部）")
    p_run.add_argument("--pages", nargs="+", default=None,
                       help="选择性导出页面: p01 p02 p03 p04 p05 p06（支持简写）")
    p_run.add_argument("--focus", default=None,
                       help="行过滤关键词（如：工时、成本）")
    p_run.add_argument("--columns", nargs="+", default=None,
                       help="列过滤（如：实际值 预算值）")
    p_run.add_argument("--output-format", default=None,
                       help="输出格式字符串: html/pdf/docx/pptx/all")

    # interactive（交互模式）
    p_interactive = sub.add_parser("interactive", help="交互模式：选择项目 → 输入月份 → 生成")
    p_interactive.add_argument("--list", action="store_true", help="仅列出项目")
    p_interactive.add_argument("--output-dir", default="output", help="输出目录")

    # from-snapshot（从 snapshot 生成）
    p_snap = sub.add_parser("from-snapshot", help="从已有 snapshot.json 生成快报")
    p_snap.add_argument("--snapshot", required=True, help="snapshot.json 路径")
    p_snap.add_argument("--output-dir", default="output", help="输出目录")
    p_snap.add_argument("--format", nargs="+", default=None,
                        help="输出格式: html pdf metrics all（默认 all）")

    # list（列出项目）
    sub.add_parser("list", help="列出所有项目")

    # modules（列出页面模块）
    sub.add_parser("modules", help="列出所有页面模块")

    args = parser.parse_args()

    if args.command == "query":
        query_mode(args)
    elif args.command == "run":
        run_pipeline_mode(args)
    elif args.command == "interactive":
        interactive_mode(args)
    elif args.command == "from-snapshot":
        from_snapshot_mode(args)
    elif args.command == "list":
        from skill_runner import discover_projects
        projects = discover_projects()
        print(f"共 {len(projects)} 个项目：")
        for p in projects:
            print(f"  {str(p.get('id', '?')):>8}  {p.get('code', '?'):<16}  {p.get('name', '?')}")
    elif args.command == "modules":
        from page_modules import PAGE_REGISTRY
        print(f"共 {len(PAGE_REGISTRY)} 个页面模块：")
        for page_id, (mod_path, cls_name, title, idx) in sorted(PAGE_REGISTRY.items(), key=lambda x: x[1][3]):
            print(f"  {idx}. {title:<20} ({page_id}) → {mod_path}.{cls_name}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
