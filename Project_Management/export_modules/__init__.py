"""export_modules — 导出层
=======================
提供文档输出功能：
  - html_generator: 交互式 HTML 快报生成
  - pdf_generator: PDF 打印版快报生成
  - report_runner: 统一导出编排（HTML + PDF + 指标 JSON）
"""

from export_modules.report_runner import (
    generate_html,
    generate_pdf_reportlab,
    generate_metrics_json,
)

__all__ = [
    "generate_html",
    "generate_pdf_reportlab",
    "generate_metrics_json",
]
