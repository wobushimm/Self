#!/usr/bin/env python3
"""Unified runner: load snapshot.json → generate HTML + PDF reports.

Usage:
    python run_report.py                          # default: ../snapshot.json
    python run_report.py --snapshot path/to/snap  # custom snapshot path
    python run_report.py --html-only              # only generate HTML
    python run_report.py --pdf-only              # only generate PDF (reportlab)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure the Skill root directory is on sys.path.
_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR))

from data_modules.metrics_engine import load_snapshot, create_metrics  # noqa: E402


def safe_filename(name: str) -> str:
    """将名称中的路径特殊字符替换为安全字符，用于文件名。

    处理 / \\ : * ? " < > | 等文件系统不允许的字符。
    原始名称不做修改，仅在构建文件路径时调用。
    """
    import re as _re
    return _re.sub(r'[/\\:*?"<>|]+', '_', name).strip('_')


def generate_html(snap: dict, output_dir: Path, pdf_filename: str = "",
                  page_ids=None, focus=None, columns=None,
                  export_files=None, custom_templates=None) -> Path:
    from export_modules.html_generator import build_html_report_from_snapshot

    proj = snap.get("project", {})
    project_name = proj.get("project_name", "unknown")
    month = proj.get("stat_month", "")
    html_path = output_dir / f"{safe_filename(project_name)}_{month}_交互快报.html"
    build_html_report_from_snapshot(snap, html_path, pdf_filename=pdf_filename,
                                    page_ids=page_ids, focus=focus, columns=columns,
                                    export_files=export_files,
                                    custom_templates=custom_templates)
    return html_path


def generate_pdf_reportlab(snap: dict, output_dir: Path, page_ids=None) -> Path:
    from export_modules.pdf_generator import export_pdf

    proj = snap.get("project", {})
    project_name = proj.get("project_name", "unknown")
    month = proj.get("stat_month", "")
    pdf_path = output_dir / f"{safe_filename(project_name)}_{month}_交互快报.pdf"
    export_pdf(snap, pdf_path, page_ids=page_ids)
    return pdf_path


def generate_docx(snap: dict, output_dir: Path, page_ids=None) -> Path:
    from export_modules.docx_generator import export_docx

    proj = snap.get("project", {})
    project_name = proj.get("project_name", "unknown")
    month = proj.get("stat_month", "")
    docx_path = output_dir / f"{safe_filename(project_name)}_{month}_项目管理月度快报.docx"
    export_docx(snap, docx_path, page_ids=page_ids)
    return docx_path


def generate_pptx(snap: dict, output_dir: Path, page_ids=None) -> Path:
    from export_modules.ppt_generator import export_pptx

    proj = snap.get("project", {})
    project_name = proj.get("project_name", "unknown")
    month = proj.get("stat_month", "")
    pptx_path = output_dir / f"{safe_filename(project_name)}_{month}_项目管理月度快报.pptx"
    export_pptx(snap, pptx_path, page_ids=page_ids)
    return pptx_path


def generate_validation(snap: dict, output_dir: Path) -> Path:
    """生成数据校验报告（每次执行自动调用）。"""
    from export_modules.validation_report import generate_validation_report
    return generate_validation_report(snap, output_dir)


def generate_page_report(
    snap: dict,
    output_dir: Path,
    page_ids: list = None,
    focus: str = None,
    columns: list = None,
    output_format: str = "html",
    pdf_filename: str = "",
) -> dict:
    """统一导出入口，返回 {format: filepath}。

    Args:
        snap: 数据快照
        output_dir: 输出目录
        page_ids: 页面 ID 列表，None=全部，["p01","p03"]=选择性
        focus: 行过滤关键词
        columns: 列过滤
        output_format: html/pdf/docx/pptx/all
        pdf_filename: PDF 文件名（供 HTML 下载链接使用）

    Returns:
        {format: filepath} 字典
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    proj = snap.get("project", {})
    pn = proj.get("project_name", "unknown")
    sm = proj.get("stat_month", "")

    formats = _resolve_output_format(output_format)

    if "html" in formats:
        try:
            pdf_name = pdf_filename or f"{safe_filename(pn)}_{sm}_交互快报.pdf"
            path = generate_html(snap, output_dir, pdf_filename=pdf_name,
                                 page_ids=page_ids, focus=focus, columns=columns)
            results["html"] = str(path)
        except Exception as e:
            results["html"] = f"ERROR: {e}"

    if "pdf" in formats:
        try:
            path = generate_pdf_reportlab(snap, output_dir, page_ids=page_ids)
            results["pdf"] = str(path)
        except Exception as e:
            results["pdf"] = f"ERROR: {e}"

    if "docx" in formats:
        try:
            path = generate_docx(snap, output_dir, page_ids=page_ids)
            results["docx"] = str(path)
        except Exception as e:
            results["docx"] = f"ERROR: {e}"

    if "pptx" in formats:
        try:
            path = generate_pptx(snap, output_dir, page_ids=page_ids)
            results["pptx"] = str(path)
        except Exception as e:
            results["pptx"] = f"ERROR: {e}"

    # 始终生成数据校验报告
    try:
        path = generate_validation(snap, output_dir)
        results["validation"] = str(path)
    except Exception as e:
        results["validation"] = f"ERROR: {e}"

    return results


def _resolve_output_format(output_format: str) -> list:
    """解析输出格式字符串。"""
    fmt = output_format.lower().strip()
    if fmt == "all":
        return ["html", "pdf", "docx", "pptx"]
    # 支持逗号分隔或空格分隔
    parts = fmt.replace(",", " ").split()
    valid = {"html", "pdf", "docx", "pptx"}
    return [p for p in parts if p in valid] or ["html"]


def generate_metrics_json(snap: dict, output_dir: Path) -> Path:
    """Generate the brief JSON + Markdown using create_metrics()."""
    from dataclasses import asdict
    from datetime import datetime, timezone

    proj = snap.get("project", {})
    metrics, conflicts = create_metrics(snap)
    report = {
        "schema_version": "0.2.0",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "project": {
            "project_id": proj.get("project_id", ""),
            "project_name": proj.get("project_name", ""),
            "settlement_month": proj.get("stat_month", ""),
        },
        "metrics": [asdict(item) for item in metrics],
        "data_conflicts": conflicts,
        "source_policy": "统一 snapshot.json（API 数据）作为唯一数据源。",
    }
    stem = f"{proj.get('project_id', 'unknown')}_{proj.get('stat_month', '')}_项目快报"
    json_path = output_dir / f"{stem}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


def generate_merged_pdf(snapshots: list[dict], output_dir: Path, quarterly_data: dict = None,
                        quarter_label: str = "", aggregated_data: dict = None,
                        page_ids: list[str] = None) -> Path:
    """生成跨月合并 PDF 报告。"""
    from export_modules.merged_pdf_generator import export_merged_pdf

    if not snapshots:
        raise ValueError("snapshots 列表不能为空")

    first_proj = snapshots[0].get("project", {})
    last_proj = snapshots[-1].get("project", {})
    project_name = first_proj.get("project_name", "unknown")
    first_month = first_proj.get("stat_month", "")
    last_month = last_proj.get("stat_month", "")
    month_label = first_month if len(snapshots) == 1 else f"{first_month}_{last_month}"

    pdf_path = output_dir / f"{safe_filename(project_name)}_{month_label}_跨月合并快报.pdf"
    export_merged_pdf(snapshots, pdf_path, quarterly_data=quarterly_data or {}, quarter_label=quarter_label,
                      aggregated_data=aggregated_data or {}, page_ids=page_ids)
    return pdf_path


def generate_merged_docx(snapshots: list[dict], output_dir: Path, quarterly_data: dict = None,
                         quarter_label: str = "", aggregated_data: dict = None,
                         page_ids: list[str] = None) -> Path:
    """生成跨月合并 Word 报告。"""
    from export_modules.merged_docx_generator import export_merged_docx

    if not snapshots:
        raise ValueError("snapshots 列表不能为空")

    first_proj = snapshots[0].get("project", {})
    last_proj = snapshots[-1].get("project", {})
    project_name = first_proj.get("project_name", "unknown")
    first_month = first_proj.get("stat_month", "")
    last_month = last_proj.get("stat_month", "")
    month_label = first_month if len(snapshots) == 1 else f"{first_month}_{last_month}"

    docx_path = output_dir / f"{safe_filename(project_name)}_{month_label}_跨月合并快报.docx"
    export_merged_docx(snapshots, docx_path, quarterly_data=quarterly_data or {}, quarter_label=quarter_label,
                       aggregated_data=aggregated_data or {}, page_ids=page_ids)
    return docx_path


def generate_merged_html(snapshots: list[dict], output_dir: Path, pdf_filename: str = "",
                         quarterly_data: dict = None, quarter_label: str = "",
                         export_files=None, aggregated_data: dict = None,
                         page_ids: list[str] = None,
                         focus: str = None) -> Path:
    """生成跨月合并 HTML 报告。"""
    from export_modules.merged_html_generator import build_merged_report

    if not snapshots:
        raise ValueError("snapshots 列表不能为空")

    first_proj = snapshots[0].get("project", {})
    last_proj = snapshots[-1].get("project", {})
    project_name = first_proj.get("project_name", "unknown")
    first_month = first_proj.get("stat_month", "")
    last_month = last_proj.get("stat_month", "")
    month_label = first_month if len(snapshots) == 1 else f"{first_month}_{last_month}"

    html_path = output_dir / f"{safe_filename(project_name)}_{month_label}_跨月合并快报.html"
    build_merged_report(snapshots, html_path, pdf_filename=pdf_filename,
                        quarterly_data=quarterly_data or {}, quarter_label=quarter_label,
                        export_files=export_files, aggregated_data=aggregated_data or {},
                        page_ids=page_ids, focus=focus)
    return html_path


def main():
    parser = argparse.ArgumentParser(description="从 snapshot.json 生成 HTML/PDF 月度快报")
    parser.add_argument("--snapshot", type=Path, default=Path("../snapshot.json"),
                        help="snapshot.json 文件路径（默认 ../snapshot.json）")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"),
                        help="输出目录（默认 outputs/）")
    parser.add_argument("--html-only", action="store_true", help="仅生成 HTML")
    parser.add_argument("--pdf-only", action="store_true", help="仅生成 PDF（reportlab）")
    parser.add_argument("--docx-only", action="store_true", help="仅生成 DOCX（Word）")
    args = parser.parse_args()

    if not args.snapshot.exists():
        print(f"错误：找不到 {args.snapshot}", file=sys.stderr)
        print("请先运行 fetch_data 脚本生成 snapshot.json", file=sys.stderr)
        sys.exit(1)

    snap = load_snapshot(args.snapshot)
    proj = snap.get("project", {})
    print(f"项目：{proj.get('project_name', '?')} ({proj.get('project_id', '?')})")
    print(f"月份：{proj.get('stat_month', '?')}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    do_html = not args.pdf_only and not args.docx_only
    do_pdf = not args.html_only and not args.docx_only
    do_docx = not args.html_only and not args.pdf_only

    if do_html:
        # 计算 PDF 文件名，传给 HTML 生成器以便下载链接可用
        pdf_name = f"{safe_filename(proj.get('project_name', 'unknown'))}_{proj.get('stat_month', '')}_交互快报.pdf"
        html_path = generate_html(snap, args.output_dir, pdf_filename=pdf_name)
        print(f"[OK] HTML: {html_path}")

    if do_pdf:
        try:
            pdf_path = generate_pdf_reportlab(snap, args.output_dir)
            print(f"[OK] PDF: {pdf_path}")
        except ImportError as e:
            print(f"[WARN] PDF 生成失败（缺少 reportlab）：{e}", file=sys.stderr)

    if do_docx:
        try:
            docx_path = generate_docx(snap, args.output_dir)
            print(f"[OK] DOCX: {docx_path}")
        except ImportError as e:
            print(f"[WARN] DOCX 生成失败（缺少 python-docx）：{e}", file=sys.stderr)

    # Always generate metrics JSON
    json_path = generate_metrics_json(snap, args.output_dir)
    print(f"[OK] 指标 JSON: {json_path}")

    print("\n完成！")


if __name__ == "__main__":
    main()
