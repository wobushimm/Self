#!/usr/bin/env python3
"""按真实司库交易明细生成“收付明细”（本地离线版）。

财务确认的固定列映射：
    司库 E 银行账号.银行账号 -> 日报 F 银行账号
    司库 J *对方户名       -> 日报 G 往来单位
    司库 N 付款金额        -> 日报 K 支出
    司库 O 收款金额        -> 日报 J 收入
    司库 H *交易日期       -> 日报 B 日期，并生成日报 A 年-月

收付明细 C/D/E 与 L 列复制模板第 5 行公式。付款摘要中的人工标注“采购/费用/内部往来”直接分类；
未标注付款再查“特殊摘要规则”。规则未命中、规则冲突和摘要标注冲突会写入“H_I待配置”。
程序从不根据摘要自行猜测未确认的财务分类。
"""

from __future__ import annotations

import argparse
import shutil
from copy import copy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


SOURCE_SHEET = "司库交易明细"
# 正式工作簿中的明细页名称。为兼容前期 demo，首次生成时会把旧的“日报模板”改名为此名称。
REPORT_SHEET = "收付明细"
LEGACY_REPORT_SHEET = "日报模板"
SPECIAL_RULE_SHEET = "特殊摘要规则"
PENDING_SHEET = "H_I待配置"
HEADER_ROW = 4
REPORT_FIRST_DATA_ROW = 5

RULE_HEADERS = [
    "启用", "交易方向", "匹配字段", "匹配方式", "匹配值",
    "编码(H)", "收付项目(I)", "允许H为空", "允许I为空", "优先级", "规则说明",
]

NAVY = "17365D"
LIGHT_BLUE = "D9EAF7"
LIGHT_YELLOW = "FFF2CC"
LIGHT_RED = "FCE4D6"
THIN_LINE = Side(style="thin", color="D9E2F3")
PAYMENT_MARKERS = {
    "采购": ("2", "2、采购付款"),
    "费用": ("3", "3、费用支出"),
    "内部往来": ("5", "5、内部往来"),
}


class DailyReportError(ValueError):
    """输入文件或配置规则不满足生成条件。"""


@dataclass(frozen=True)
class SourceRow:
    detail_no: str
    transaction_date: Any
    account: str
    counterparty: str
    summary: str
    payment: Decimal
    receipt: Decimal

    @property
    def direction(self) -> str:
        if self.payment > 0 and self.receipt == 0:
            return "支出"
        if self.receipt > 0 and self.payment == 0:
            return "收入"
        return "异常"

    def value_for(self, field: str) -> str:
        values = {
            "对方户名": self.counterparty,
            "摘要": self.summary,
            "银行账号": self.account,
            "交易明细编号": self.detail_no,
        }
        return values.get(field, "")


@dataclass(frozen=True)
class MappingRule:
    enabled: bool
    direction: str
    field: str
    method: str
    target: str
    code: str
    project: str
    allow_empty_code: bool
    allow_empty_project: bool
    priority: int
    note: str


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def money(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise DailyReportError(f"金额字段含有无法识别的内容：{value!r}")


def normalize_header(value: Any) -> str:
    """兼容司库表头的必填星号，例如 *交易日期。"""
    return text(value).lstrip("*")


def header_index(sheet) -> dict[str, int]:
    return {normalize_header(cell.value): cell.column for cell in sheet[HEADER_ROW] if normalize_header(cell.value)}


def require_columns(index: dict[str, int], required: set[str], sheet_name: str) -> None:
    missing = sorted(required - set(index))
    if missing:
        raise DailyReportError(f"Sheet「{sheet_name}」缺少必需表头：{'、'.join(missing)}")


def cell_value(sheet, row: int, index: dict[str, int], header: str) -> Any:
    return sheet.cell(row=row, column=index[header]).value


def read_source_rows(sheet) -> list[SourceRow]:
    index = header_index(sheet)
    required = {"交易明细编号", "交易日期", "银行账号.银行账号", "对方户名", "摘要", "付款金额", "收款金额"}
    require_columns(index, required, sheet.title)
    records: list[SourceRow] = []
    for row in range(HEADER_ROW + 1, sheet.max_row + 1):
        detail_no = text(cell_value(sheet, row, index, "交易明细编号"))
        if not detail_no:
            continue
        records.append(SourceRow(
            detail_no=detail_no,
            transaction_date=cell_value(sheet, row, index, "交易日期"),
            account=text(cell_value(sheet, row, index, "银行账号.银行账号")),
            counterparty=text(cell_value(sheet, row, index, "对方户名")),
            summary=text(cell_value(sheet, row, index, "摘要")),
            payment=money(cell_value(sheet, row, index, "付款金额")),
            receipt=money(cell_value(sheet, row, index, "收款金额")),
        ))
    if not records:
        raise DailyReportError("司库交易明细没有可处理的数据行。")
    return records


def copy_style(source, target) -> None:
    if source.has_style:
        target._style = copy(source._style)
    if source.number_format:
        target.number_format = source.number_format
    target.font = copy(source.font)
    target.fill = copy(source.fill)
    target.border = copy(source.border)
    target.alignment = copy(source.alignment)
    target.protection = copy(source.protection)


def add_missing_rule_seeds(sheet, records: list[SourceRow]) -> int:
    """只为尚未被任何启用规则覆盖的付款补充规则草稿。

    例如已维护“包含：网银服务年费”时，不能再为“2026年网银服务年费”
    新增一条精确匹配草稿，否则同一摘要会命中两条规则而转人工审核。
    """
    headers = [text(cell.value) for cell in sheet[HEADER_ROW]]
    existing = set()
    for values in sheet.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
        row = {headers[i]: values[i] if i < len(values) else None for i in range(len(headers))}
        existing.add((text(row.get("交易方向")), text(row.get("匹配字段")), text(row.get("匹配值"))))
    existing_rules = read_rules(sheet)
    added = 0
    for record in records:
        if record.direction != "支出" or len(payment_marker_hits(record.summary)) == 1:
            continue
        # 已有“精确匹配”或“包含”等任意启用规则能够处理时，不产生重复草稿。
        if any(matches(rule, record) for rule in existing_rules):
            continue
        target = record.summary
        field = "摘要"
        key = (record.direction, field, target)
        if not target or key in existing:
            continue
        sheet.append(["是", record.direction, field, "精确匹配", target, "", "", "否", "否", 400, "待财务确认的特殊付款摘要规则"])
        existing.add(key)
        added += 1
    return added


def build_rule_seed_sheet(workbook, records: list[SourceRow]) -> int:
    """创建或同步特殊付款摘要规则，不对收入和未确认业务做猜测。"""
    if SPECIAL_RULE_SHEET in workbook.sheetnames:
        return add_missing_rule_seeds(workbook[SPECIAL_RULE_SHEET], records)
    sheet = workbook.create_sheet(SPECIAL_RULE_SHEET)
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells("A1:K1")
    sheet["A1"] = "特殊摘要规则（请财务维护）"
    sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
    sheet["A1"].font = Font(name="Microsoft YaHei", size=15, bold=True, color="FFFFFF")
    sheet["A1"].alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet.merge_cells("A2:K2")
    sheet["A2"] = "本表仅处理未带人工标注的付款摘要。付款摘要中恰好出现“采购/费用/内部往来”时，程序直接分类；无标注才查本表。建议固定摘要用“精确匹配”，可变摘要用“包含”。若多条规则同时命中，程序转人工审核。"
    sheet["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[2].height = 36
    sheet.append([])
    sheet.append(RULE_HEADERS)
    style_rule_header(sheet)

    added = add_missing_rule_seeds(sheet, records)
    set_rule_widths(sheet)
    return added


def style_rule_header(sheet) -> None:
    for cell in sheet[4]:
        cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        cell.font = Font(name="Microsoft YaHei", bold=True, color=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN_LINE)
    sheet.freeze_panes = "A5"


def set_rule_widths(sheet) -> None:
    widths = [10, 12, 16, 14, 32, 12, 22, 12, 12, 10, 28]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width


def read_rules(sheet) -> list[MappingRule]:
    actual_headers = [text(cell.value) for cell in sheet[HEADER_ROW]]
    missing = set(RULE_HEADERS) - set(actual_headers)
    if missing:
        raise DailyReportError(f"Sheet「{sheet.title}」缺少表头：{'、'.join(sorted(missing))}")
    records = []
    for values in sheet.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
        row = {actual_headers[i]: values[i] if i < len(values) else None for i in range(len(actual_headers))}
        if not any(text(value) for value in row.values()):
            continue
        try:
            priority = int(row.get("优先级") or 0)
        except (TypeError, ValueError):
            priority = 0
        records.append(MappingRule(
            enabled=text(row.get("启用")) not in {"否", "N", "n", "0", ""},
            direction=text(row.get("交易方向")),
            field=text(row.get("匹配字段")),
            method=text(row.get("匹配方式")) or "精确匹配",
            target=text(row.get("匹配值")),
            code=text(row.get("编码(H)")),
            project=text(row.get("收付项目(I)")),
            allow_empty_code=text(row.get("允许H为空")) in {"是", "Y", "y", "1"},
            allow_empty_project=text(row.get("允许I为空")) in {"是", "Y", "y", "1"},
            priority=priority,
            note=text(row.get("规则说明")),
        ))
    return sorted((rule for rule in records if rule.enabled), key=lambda rule: rule.priority, reverse=True)


def matches(rule: MappingRule, record: SourceRow) -> bool:
    if rule.direction and rule.direction != record.direction:
        return False
    if not rule.target:
        return False

    def compare(value: str, target: str) -> bool:
        if not value or not target:
            return False
        return target in value if rule.method == "包含" else target == value

    if rule.field == "对方户名+摘要":
        if "|" not in rule.target:
            return False
        counterparty_target, summary_target = rule.target.split("|", 1)
        return compare(record.counterparty, counterparty_target) and compare(record.summary, summary_target)
    if rule.field == "银行账号+对方户名+摘要":
        parts = rule.target.split("|", 2)
        return len(parts) == 3 and compare(record.account, parts[0]) and compare(record.counterparty, parts[1]) and compare(record.summary, parts[2])
    return compare(record.value_for(rule.field), rule.target)


def payment_marker_hits(summary: str) -> list[str]:
    """人工标注可出现在摘要任何位置，不依赖 +、# 等分隔符。"""
    return [marker for marker in PAYMENT_MARKERS if marker in summary]


def validate_rule(rule: MappingRule) -> tuple[str, str, str]:
    issues = []
    if not rule.code and not rule.allow_empty_code:
        issues.append("特殊摘要规则未填写编码(H)")
    if not rule.project and not rule.allow_empty_project:
        issues.append("特殊摘要规则未填写收付项目(I)")
    return rule.code, rule.project, "；".join(issues)


def classify(record: SourceRow, rules: list[MappingRule]) -> tuple[str, str, str]:
    """收款直接归销售收款；付款：人工标注 -> 特殊摘要规则 -> 人工审核。"""
    if record.direction == "异常":
        return "", "", "付款金额和收款金额应当只有一列大于 0"
    if record.direction == "收入":
        return "1", "1、销售收款", ""

    markers = payment_marker_hits(record.summary)
    if len(markers) == 1:
        code, project = PAYMENT_MARKERS[markers[0]]
        return code, project, ""
    if len(markers) > 1:
        return "", "", f"摘要同时出现多个人工标注：{'、'.join(markers)}"

    matched_rules = [rule for rule in rules if matches(rule, record)]
    if not matched_rules:
        return "", "", "未匹配特殊摘要规则"
    if len(matched_rules) > 1:
        return "", "", "同时匹配多条特殊摘要规则"
    return validate_rule(matched_rules[0])


def date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
    raise DailyReportError(f"交易日期无法识别：{value!r}")


def copy_template_row(sheet, source_row: int, target_row: int) -> None:
    for column in range(1, 14):
        source = sheet.cell(source_row, column)
        target = sheet.cell(target_row, column)
        copy_style(source, target)
        if source.data_type == "f" and isinstance(source.value, str):
            try:
                target.value = Translator(source.value, origin=source.coordinate).translate_formula(target.coordinate)
            except ValueError:
                target.value = source.value
        else:
            target.value = None
    sheet.row_dimensions[target_row].height = sheet.row_dimensions[source_row].height


def last_existing_report_row(sheet) -> int:
    """识别日报当前数据区末行，不误删下方的辅助账户清单。"""
    last = REPORT_FIRST_DATA_ROW - 1
    for row in range(REPORT_FIRST_DATA_ROW, sheet.max_row + 1):
        if sheet.cell(row, 2).value not in (None, "") or sheet.cell(row, 1).value not in (None, ""):
            last = row
        elif last >= REPORT_FIRST_DATA_ROW:
            break
    return last


def clear_report_data(sheet, last_row: int) -> None:
    for row in range(REPORT_FIRST_DATA_ROW, last_row + 1):
        for column in range(1, 14):
            sheet.cell(row, column).value = None


def fill_report(sheet, records: list[SourceRow], rules: list[MappingRule], opening_balance: Decimal) -> list[list[Any]]:
    original_last = last_existing_report_row(sheet)
    required_last = REPORT_FIRST_DATA_ROW + len(records) - 1
    sample_row = REPORT_FIRST_DATA_ROW
    # 保存样例单元格样式和 C/D/E 公式；清空后重新复制到目标行。
    template_cells = [copy(sheet.cell(sample_row, column)) for column in range(1, 14)]
    clear_report_data(sheet, max(original_last, required_last))
    pending: list[list[Any]] = []
    for offset, record in enumerate(records):
        row = REPORT_FIRST_DATA_ROW + offset
        for column, saved in enumerate(template_cells, start=1):
            target = sheet.cell(row, column)
            target._style = copy(saved._style)
            target.number_format = saved.number_format
            target.font = copy(saved.font)
            target.fill = copy(saved.fill)
            target.border = copy(saved.border)
            target.alignment = copy(saved.alignment)
            if column in (3, 4, 5) and isinstance(saved.value, str) and saved.value.startswith("="):
                try:
                    target.value = Translator(saved.value, origin=f"{chr(64 + column)}{sample_row}").translate_formula(target.coordinate)
                except ValueError:
                    target.value = saved.value
            else:
                target.value = None

        tx_date = date_value(record.transaction_date)
        code, project, issue = classify(record, rules)
        sheet.cell(row, 1).value = f"{tx_date.year}年-{tx_date.month}月"
        sheet.cell(row, 2).value = tx_date
        sheet.cell(row, 2).number_format = "yyyy年m月d日"
        sheet.cell(row, 6).value = record.account
        sheet.cell(row, 6).number_format = "@"
        sheet.cell(row, 7).value = record.counterparty
        sheet.cell(row, 8).value = code
        sheet.cell(row, 9).value = project
        sheet.cell(row, 10).value = float(record.receipt)
        sheet.cell(row, 11).value = float(record.payment)
        sheet.cell(row, 10).number_format = '#,##0.00;[Red](#,##0.00);-'
        sheet.cell(row, 11).number_format = '#,##0.00;[Red](#,##0.00);-'
        if row == REPORT_FIRST_DATA_ROW:
            sheet.cell(row, 12).value = f"={float(opening_balance)}+J{row}-K{row}"
        else:
            sheet.cell(row, 12).value = f"=L{row - 1}+J{row}-K{row}"
        sheet.cell(row, 12).number_format = '#,##0.00;[Red](#,##0.00);-'
        sheet.cell(row, 13).value = "自动生成"
        if issue:
            pending.append([
                record.detail_no, tx_date, record.direction, record.account, record.counterparty,
                record.summary, float(record.payment), float(record.receipt), issue,
                "付款请维护“特殊摘要规则”；收款及规则冲突请财务人工审核后处理",
            ])
            for cell in (sheet.cell(row, 8), sheet.cell(row, 9)):
                cell.fill = PatternFill("solid", fgColor=LIGHT_YELLOW)
    return pending


def write_pending_sheet(workbook, pending: list[list[Any]]) -> None:
    if PENDING_SHEET in workbook.sheetnames:
        del workbook[PENDING_SHEET]
    sheet = workbook.create_sheet(PENDING_SHEET)
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells("A1:J1")
    sheet["A1"] = "H/I 待配置清单"
    sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
    sheet["A1"].font = Font(name="Microsoft YaHei", size=15, bold=True, color="FFFFFF")
    sheet.merge_cells("A2:J2")
    sheet["A2"] = "此处列出未匹配、规则不完整或规则冲突的交易。付款可维护“特殊摘要规则”后重新生成；收款及不确定业务需人工审核。"
    sheet["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[2].height = 32
    sheet.append([])
    headers = ["交易明细编号", "交易日期", "收支方向", "银行账号", "对方户名", "摘要", "付款金额", "收款金额", "问题", "建议处理"]
    sheet.append(headers)
    for cell in sheet[4]:
        cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        cell.font = Font(name="Microsoft YaHei", bold=True, color=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN_LINE)
    if pending:
        for row in pending:
            sheet.append(row)
        for row in sheet.iter_rows(min_row=5, max_row=sheet.max_row, max_col=10):
            for cell in row:
                cell.fill = PatternFill("solid", fgColor=LIGHT_YELLOW)
    else:
        sheet.append(["-", "-", "-", "-", "-", "-", 0, 0, "无待配置项", "无需处理"])
    for row in range(5, sheet.max_row + 1):
        sheet.cell(row, 2).number_format = "yyyy-mm-dd"
        sheet.cell(row, 7).number_format = '#,##0.00;[Red](#,##0.00);-'
        sheet.cell(row, 8).number_format = '#,##0.00;[Red](#,##0.00);-'
    widths = [20, 14, 12, 20, 28, 38, 14, 14, 34, 42]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    sheet.freeze_panes = "A5"


def initialize_rules(input_path: Path) -> None:
    workbook = load_workbook(input_path, data_only=False)
    if SOURCE_SHEET not in workbook.sheetnames:
        raise DailyReportError(f"找不到 Sheet「{SOURCE_SHEET}」。")
    records = read_source_rows(workbook[SOURCE_SHEET])
    added = build_rule_seed_sheet(workbook, records)
    workbook.save(input_path)
    return added


def sync_rules_from_source(source_path: Path, template_path: Path) -> int:
    """将当天司库文件中的新对方户名/摘要补充到长期规则模板，保留既有规则。"""
    source_book = load_workbook(source_path, data_only=False, read_only=False)
    if SOURCE_SHEET not in source_book.sheetnames:
        raise DailyReportError(f"司库文件中找不到 Sheet「{SOURCE_SHEET}」。")
    records = read_source_rows(source_book[SOURCE_SHEET])
    template_book = load_workbook(template_path, data_only=False)
    added = build_rule_seed_sheet(template_book, records)
    template_book.save(template_path)
    return added


def replace_source_sheet(workbook, source_sheet) -> None:
    """将当天司库 Sheet 写入输出工作簿，便于日报结果追溯原始数据。"""
    if SOURCE_SHEET in workbook.sheetnames:
        position = workbook.sheetnames.index(SOURCE_SHEET)
        del workbook[SOURCE_SHEET]
    else:
        position = 0
    target = workbook.create_sheet(SOURCE_SHEET, position)
    target.sheet_view.showGridLines = source_sheet.sheet_view.showGridLines
    target.freeze_panes = source_sheet.freeze_panes
    for key, dimension in source_sheet.column_dimensions.items():
        target.column_dimensions[key].width = dimension.width
        target.column_dimensions[key].hidden = dimension.hidden
    for row_number, dimension in source_sheet.row_dimensions.items():
        target.row_dimensions[row_number].height = dimension.height
        target.row_dimensions[row_number].hidden = dimension.hidden
    for row in source_sheet.iter_rows():
        for source in row:
            target_cell = target.cell(source.row, source.column, source.value)
            copy_style(source, target_cell)
    for merged_range in source_sheet.merged_cells.ranges:
        target.merge_cells(str(merged_range))


def get_report_sheet(workbook):
    """取得收付明细页，并兼容早期 demo 的“日报模板”命名。"""
    if REPORT_SHEET in workbook.sheetnames:
        return workbook[REPORT_SHEET]
    if LEGACY_REPORT_SHEET in workbook.sheetnames:
        sheet = workbook[LEGACY_REPORT_SHEET]
        sheet.title = REPORT_SHEET
        return sheet
    raise DailyReportError(
        f"工作簿必须包含 Sheet「{REPORT_SHEET}」；"
        f"早期 demo 也可提供 Sheet「{LEGACY_REPORT_SHEET}」。"
    )


def generate_loaded_workbook(workbook, output_path: Path, opening_balance: Decimal) -> dict[str, Any]:
    if SOURCE_SHEET not in workbook.sheetnames:
        raise DailyReportError(f"工作簿必须包含 Sheet「{SOURCE_SHEET}」。")
    if SPECIAL_RULE_SHEET not in workbook.sheetnames:
        raise DailyReportError(f"找不到 Sheet「{SPECIAL_RULE_SHEET}」。请先同步特殊摘要规则，再由财务填写规则。")
    records = read_source_rows(workbook[SOURCE_SHEET])
    rules = read_rules(workbook[SPECIAL_RULE_SHEET])
    # 这一步会把 A–M 列按收付明细格式完整生成；透视表刷新留给下一阶段。
    pending = fill_report(get_report_sheet(workbook), records, rules, opening_balance)
    write_pending_sheet(workbook, pending)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return {
        "output_path": str(output_path),
        "transactions": len(records),
        "pending_h_i": len(pending),
        "report_sheet": REPORT_SHEET,
    }


def generate_report(input_path: Path, output_path: Path, opening_balance: Decimal) -> dict[str, Any]:
    if not input_path.exists():
        raise DailyReportError(f"找不到输入工作簿：{input_path}")
    shutil.copy2(input_path, output_path)
    workbook = load_workbook(output_path, data_only=False)
    return generate_loaded_workbook(workbook, output_path, opening_balance)


def generate_report_from_source(source_path: Path, template_path: Path, output_path: Path, opening_balance: Decimal) -> dict[str, Any]:
    """正式日常入口：当天司库文件 + 长期模板规则文件 -> 一份新日报。"""
    if not source_path.exists():
        raise DailyReportError(f"找不到司库文件：{source_path}")
    if not template_path.exists():
        raise DailyReportError(f"找不到模板与规则文件：{template_path}")
    source_book = load_workbook(source_path, data_only=False, read_only=False)
    if SOURCE_SHEET not in source_book.sheetnames:
        raise DailyReportError(f"司库文件中找不到 Sheet「{SOURCE_SHEET}」。")
    workbook = load_workbook(template_path, data_only=False)
    replace_source_sheet(workbook, source_book[SOURCE_SHEET])
    return generate_loaded_workbook(workbook, output_path, opening_balance)


def main() -> int:
    parser = argparse.ArgumentParser(description="将司库交易明细填充到收付明细。")
    parser.add_argument("--input", required=True, help="包含司库交易明细、收付明细模板的 Excel 文件。")
    parser.add_argument("--output", help="生成后的日报文件路径。执行 --init-rules 时无需此参数。")
    parser.add_argument("--template", help="长期保存日报模板和 H/I 规则的 Excel 文件；提供后 --input 仅需为当天司库文件。")
    parser.add_argument("--opening-balance", help="日报首笔交易前的期初余额，例如 10000.00。")
    parser.add_argument("--init-rules", action="store_true", help="在输入文件中创建或同步特殊摘要规则表，供财务填写。")
    args = parser.parse_args()
    input_path = Path(args.input).expanduser().resolve()
    try:
        if args.init_rules:
            added = initialize_rules(input_path)
            print(f"已同步 Sheet「{SPECIAL_RULE_SHEET}」，新增 {added} 条待填写规则。请由财务填写编码(H)和收付项目(I)后再生成日报。")
            return 0
        if not args.output:
            parser.error("生成日报时必须提供 --output。")
        if args.opening_balance is None:
            parser.error("生成日报时必须提供 --opening-balance，避免程序猜测期初余额。")
        if args.template:
            result = generate_report_from_source(
                input_path, Path(args.template).expanduser().resolve(),
                Path(args.output).expanduser().resolve(), money(args.opening_balance),
            )
        else:
            result = generate_report(input_path, Path(args.output).expanduser().resolve(), money(args.opening_balance))
    except DailyReportError as error:
        parser.error(str(error))
    print("资金日报生成完成")
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
