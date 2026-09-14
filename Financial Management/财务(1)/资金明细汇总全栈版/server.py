#!/usr/bin/env python3
"""本地资金明细汇总工具：一次性内存会话，不保存历史数据。"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
CATEGORIES = ["销售收款", "采购付款", "费用支出", "工资、奖金", "内部往来", "社保及公积金", "税金", "其他收入", "其他支出", "投标保证金"]
_session_records: list[dict] = []
_next_id = 1


def init_db() -> None:
    """初始化一次性会话：程序重启后不保留任何历史数据。"""
    global _next_id
    _session_records.clear()
    _next_id = 1


def validate(item: dict) -> dict:
    department = str(item.get("department", "")).strip()
    happen_date = str(item.get("date", item.get("happen_date", ""))).strip()
    category = str(item.get("category", "")).strip()
    direction = str(item.get("direction", "")).strip()
    description = str(item.get("description", "")).strip()
    source_file = str(item.get("source_file", "手工新增")).strip() or "手工新增"
    if not department or not happen_date or not category or not direction:
        raise ValueError("部门、预计日期、日报栏目和收支方向均不能为空。")
    if category not in CATEGORIES:
        raise ValueError(f"日报栏目必须是预置栏目之一：{category}")
    if direction not in {"收入", "支出"}:
        raise ValueError("收支方向只能填写“收入”或“支出”。")
    try:
        datetime.strptime(happen_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("预计日期格式应为 YYYY-MM-DD，例如 2026-09-15。")
    try:
        amount = float(str(item.get("amount", "")).replace(",", ""))
    except ValueError:
        raise ValueError("金额必须为有效数字。")
    if amount <= 0:
        raise ValueError("金额必须大于 0。")
    return {"department": department, "date": happen_date, "category": category, "direction": direction, "amount": amount, "description": description, "source_file": source_file}


def insert_rows(rows: list[dict]) -> int:
    global _next_id
    clean = [validate(row) for row in rows]
    for row in clean:
        _session_records.append({"id": _next_id, "department": row["department"], "happen_date": row["date"], "category": row["category"], "direction": row["direction"], "amount": row["amount"], "description": row["description"], "source_file": row["source_file"], "created_at": datetime.now().isoformat(timespec="seconds")})
        _next_id += 1
    return len(clean)


def records() -> list[dict]:
    return [row.copy() for row in sorted(_session_records, key=lambda row: (row["happen_date"], row["id"]))]


def summary() -> dict:
    rows = records()
    grouped = {category: {"income": 0, "expense": 0, "count": 0} for category in CATEGORIES}
    departments: dict[str, int] = {}
    for row in rows:
        cell = grouped[row["category"]]
        cell["income" if row["direction"] == "收入" else "expense"] += row["amount"]
        cell["count"] += 1
        departments[row["department"]] = departments.get(row["department"], 0) + 1
    detail = [{"category": k, **v, "net": v["income"] - v["expense"]} for k, v in grouped.items() if v["count"]]
    income = sum(r["amount"] for r in rows if r["direction"] == "收入")
    expense = sum(r["amount"] for r in rows if r["direction"] == "支出")
    return {"rows": detail, "income": income, "expense": expense, "net": income - expense, "record_count": len(rows), "department_count": len(departments)}


def parse_csv(text: str, filename: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    required = {"部门", "预计日期", "日报栏目", "收支方向", "金额"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("CSV 表头必须包含：部门、预计日期、日报栏目、收支方向、金额；事项说明可选。")
    data = []
    for line, row in enumerate(reader, 2):
        if not any((value or "").strip() for value in row.values()):
            continue
        try:
            data.append({"department": row["部门"], "date": row["预计日期"], "category": row["日报栏目"], "direction": row["收支方向"], "amount": row["金额"], "description": row.get("事项说明", ""), "source_file": filename})
        except Exception as exc:
            raise ValueError(f"第 {line} 行无法读取：{exc}")
    if not data:
        raise ValueError("CSV 中没有可导入的数据行。")
    return data


def _excel_column(reference: str) -> int:
    """将 Excel 坐标（如 B4）转换为从 0 开始的列序号。"""
    letters = "".join(char for char in reference if char.isalpha())
    value = 0
    for char in letters: value = value * 26 + ord(char.upper()) - 64
    return value - 1


def _excel_date(value: str) -> str:
    """支持 YYYY-MM-DD 文本日期和 Excel 序列号日期。"""
    text = str(value).strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass
    try:
        # Excel 的 1 对应 1899-12-31；此公式同时修正 Excel 1900 闰年兼容偏差。
        from datetime import timedelta
        return (datetime(1899, 12, 30) + timedelta(days=float(text))).strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError("预计日期应为 YYYY-MM-DD 或有效的 Excel 日期。")


def parse_xlsx(source: Path) -> list[dict]:
    """读取第一张工作表的资金明细；支持常规 Excel 内联字符串与共享字符串。"""
    if not source.exists() or source.suffix.lower() != ".xlsx":
        raise ValueError("请选择存在的 .xlsx Excel 小表。")
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.iter(f"{namespace}t")) for item in root.findall(f"{namespace}si")]
            sheet_name = next((name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")), None)
            if not sheet_name: raise ValueError("Excel 中未找到可读取的工作表。")
            root = ET.fromstring(archive.read(sheet_name))
    except zipfile.BadZipFile:
        raise ValueError("该文件不是有效的 .xlsx Excel 文件。")
    except ET.ParseError:
        raise ValueError("Excel 工作表内容无法解析。")

    rows: list[list[str]] = []
    for row in root.findall(f".//{namespace}sheetData/{namespace}row"):
        cells: dict[int, str] = {}
        for cell in row.findall(f"{namespace}c"):
            column = _excel_column(cell.get("r", "A1"))
            cell_type = cell.get("t")
            if cell_type == "inlineStr": value = "".join(node.text or "" for node in cell.iter(f"{namespace}t"))
            else:
                value = cell.findtext(f"{namespace}v", default="")
                if cell_type == "s" and value != "": value = shared[int(value)]
            cells[column] = value
        if cells: rows.append([cells.get(index, "") for index in range(max(cells) + 1)])

    required = ["部门", "预计日期", "日报栏目", "收支方向", "金额"]
    header_index = next((index for index, row in enumerate(rows) if set(required).issubset(set(row))), None)
    if header_index is None:
        raise ValueError("Excel 表头必须包含：部门、预计日期、日报栏目、收支方向、金额；事项说明可选。")
    headers = {value.strip(): index for index, value in enumerate(rows[header_index]) if value.strip()}
    data = []
    for line, row in enumerate(rows[header_index + 1:], header_index + 2):
        def value(key: str) -> str: return row[headers[key]] if headers[key] < len(row) else ""
        if not any(str(item).strip() for item in row): continue
        try:
            data.append({"department": value("部门"), "date": _excel_date(value("预计日期")), "category": value("日报栏目"), "direction": value("收支方向"), "amount": value("金额"), "description": value("事项说明") if "事项说明" in headers else "", "source_file": source.name})
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Excel 第 {line} 行无法读取：{exc}")
    if not data: raise ValueError("Excel 中没有可导入的资金明细。")
    return data


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, value: object, code: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/records": return self.send_json({"records": records(), "categories": CATEGORIES})
        if path == "/api/summary": return self.send_json(summary())
        if path == "/api/export.csv":
            buffer = io.StringIO(); writer = csv.writer(buffer); writer.writerow(["序号", "部门", "预计日期", "日报栏目", "收支方向", "金额（元）", "事项说明", "来源文件"])
            for row in records(): writer.writerow([row["id"], row["department"], row["happen_date"], row["category"], row["direction"], f'{row["amount"]:.2f}', row["description"], row["source_file"]])
            payload = ("\ufeff" + buffer.getvalue()).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/csv; charset=utf-8"); self.send_header("Content-Disposition", "attachment; filename=fund_detail_summary.csv"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        return super().do_GET()

    def do_POST(self) -> None:
        try:
            path, data = urlparse(self.path).path, self.body()
            if path == "/api/records": count = insert_rows([data]); return self.send_json({"ok": True, "count": count})
            if path == "/api/import":
                rows = parse_csv(str(data.get("content", "")), str(data.get("filename", "导入明细.csv"))); count = insert_rows(rows); return self.send_json({"ok": True, "count": count})
            if path == "/api/reset":
                init_db()
                return self.send_json({"ok": True})
            self.send_json({"error": "接口不存在"}, 404)
        except (ValueError, json.JSONDecodeError) as exc: self.send_json({"error": str(exc)}, 400)
        except Exception as exc: self.send_json({"error": f"服务处理失败：{exc}"}, 500)


if __name__ == "__main__":
    init_db()
    port = 8765
    print(f"资金明细汇总工具已启动：http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
