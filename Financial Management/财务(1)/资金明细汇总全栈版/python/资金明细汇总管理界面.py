#!/usr/bin/env python3
"""资金明细汇总桌面界面：一次性导入、合并、汇总并导出 Excel。"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from tkinter import END, LEFT, RIGHT, X, Button, Entry, Frame, Label, StringVar, Text, Tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import CATEGORIES, init_db, insert_rows, parse_csv, parse_xlsx, records, summary  # noqa: E402


class FundDetailApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("资金明细汇总管理")
        self.root.geometry("1180x710")
        self.root.minsize(980, 610)
        self.source = StringVar()
        self.output = StringVar(value=str(ROOT / "output" / f"资金汇总大表_{date.today():%Y%m%d}.xlsx"))
        self.department = StringVar()
        self.happen_date = StringVar(value=date.today().isoformat())
        self.category = StringVar(value=CATEGORIES[0])
        self.direction = StringVar(value="收入")
        self.amount = StringVar()
        self.status = StringVar(value="第 1 步：上传各部门 Excel 小表，或手工新增一笔明细。")
        init_db()
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.root.configure(bg="#FFF9ED")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#FFF9ED", borderwidth=0)
        style.configure("TNotebook.Tab", background="#F8E5B1", foreground="#6A4614", padding=(18, 8), font=("Microsoft YaHei", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#E8AE43")], foreground=[("selected", "#4C2E08")])
        Label(self.root, text="资金明细汇总管理", font=("Microsoft YaHei", 20, "bold"), fg="#6A4614", bg="#FFF9ED").pack(anchor="w", padx=30, pady=(24, 2))
        Label(self.root, text="各部门 Excel 小表导入 → 自动合并明细大表 → 导出 Excel。数据仅在本次运行内存中，关闭程序即自动清空。", font=("Microsoft YaHei", 10), fg="#7B6550", bg="#FFF9ED").pack(anchor="w", padx=30, pady=(0, 16))
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=30, pady=(0, 10))
        self.import_tab = Frame(notebook, bg="#FFF9ED")
        self.detail_tab = Frame(notebook, bg="#FFF9ED")
        self.export_tab = Frame(notebook, bg="#FFF9ED")
        notebook.add(self.import_tab, text="  ① 小表导入与新增  ")
        notebook.add(self.detail_tab, text="  ② 合并明细大表  ")
        notebook.add(self.export_tab, text="  ③ 导出汇总大表  ")
        self._build_import()
        self._build_detail()
        self._build_export()
        Label(self.root, textvariable=self.status, font=("Microsoft YaHei", 10), fg="#6B5844", bg="#FFF9ED", wraplength=1080, justify="left").pack(anchor="w", padx=30, pady=(0, 16))

    def _path_row(self, parent, label: str, variable: StringVar, button_text: str, command) -> None:
        row = Frame(parent, bg="#FFF9ED"); row.pack(fill=X, padx=22, pady=8)
        Label(row, text=label, width=15, anchor="w", font=("Microsoft YaHei", 10, "bold"), fg="#6A4614", bg="#FFF9ED").pack(side=LEFT)
        Entry(row, textvariable=variable, font=("Microsoft YaHei", 10), relief="solid", bd=1).pack(side=LEFT, fill=X, expand=True, padx=(0, 8), ipady=6)
        Button(row, text=button_text, command=command, font=("Microsoft YaHei", 9), fg="#6A4614", bg="#F7D789", activebackground="#EFC361", relief="flat", padx=12, pady=7, cursor="hand2").pack(side=LEFT)

    def _build_import(self) -> None:
        Label(self.import_tab, text="导入部门资金小表", font=("Microsoft YaHei", 14, "bold"), fg="#6A4614", bg="#FFF9ED").pack(anchor="w", padx=22, pady=(20, 2))
        Label(self.import_tab, text="Excel 表头必须是：部门、预计日期、日报栏目、收支方向、金额；事项说明为可选列。", font=("Microsoft YaHei", 9), fg="#7B6550", bg="#FFF9ED").pack(anchor="w", padx=22, pady=(0, 8))
        self._path_row(self.import_tab, "部门 Excel 小表", self.source, "选择文件", self.choose_source)
        action = Frame(self.import_tab, bg="#FFF9ED"); action.pack(fill=X, padx=22, pady=(10, 18))
        Button(action, text="① 查看 Excel 表头要求", command=self.download_template, font=("Microsoft YaHei", 10, "bold"), fg="#6A4614", bg="#F7D789", activebackground="#EFC361", relief="flat", padx=15, pady=9, cursor="hand2").pack(side=LEFT)
        Button(action, text="② 导入并合并 Excel 小表", command=self.import_excel, font=("Microsoft YaHei", 10, "bold"), fg="white", bg="#C88716", activebackground="#A96D0A", relief="flat", padx=17, pady=9, cursor="hand2").pack(side=LEFT, padx=10)

        Label(self.import_tab, text="或手工新增一笔资金明细", font=("Microsoft YaHei", 14, "bold"), fg="#6A4614", bg="#FFF9ED").pack(anchor="w", padx=22, pady=(6, 3))
        box = Frame(self.import_tab, bg="#FFF9ED"); box.pack(fill=X, padx=22, pady=(0, 8))
        self._field(box, "部门 / 单位", self.department, 0, 0)
        self._field(box, "预计日期", self.happen_date, 0, 2)
        self._combo(box, "日报栏目", self.category, CATEGORIES, 1, 0)
        self._combo(box, "收支方向", self.direction, ["收入", "支出"], 1, 2)
        self._field(box, "金额（元）", self.amount, 2, 0)
        Label(box, text="事项说明", font=("Microsoft YaHei", 10, "bold"), fg="#6A4614", bg="#FFF9ED").grid(row=2, column=2, sticky="w", padx=(22, 6), pady=8)
        self.description = Text(box, height=2, width=40, font=("Microsoft YaHei", 10), relief="solid", bd=1)
        self.description.grid(row=2, column=3, sticky="ew", padx=(0, 22), pady=8)
        box.grid_columnconfigure(1, weight=1); box.grid_columnconfigure(3, weight=1)
        Button(self.import_tab, text="合并到资金明细大表", command=self.add_record, font=("Microsoft YaHei", 10, "bold"), fg="white", bg="#C88716", activebackground="#A96D0A", relief="flat", padx=18, pady=9, cursor="hand2").pack(anchor="e", padx=22, pady=(2, 18))

    def _field(self, parent, label, var, row, col) -> None:
        Label(parent, text=label, font=("Microsoft YaHei", 10, "bold"), fg="#6A4614", bg="#FFF9ED").grid(row=row, column=col, sticky="w", padx=(22, 6), pady=8)
        Entry(parent, textvariable=var, font=("Microsoft YaHei", 10), relief="solid", bd=1).grid(row=row, column=col + 1, sticky="ew", padx=(0, 22), pady=8, ipady=5)

    def _combo(self, parent, label, var, values, row, col) -> None:
        Label(parent, text=label, font=("Microsoft YaHei", 10, "bold"), fg="#6A4614", bg="#FFF9ED").grid(row=row, column=col, sticky="w", padx=(22, 6), pady=8)
        ttk.Combobox(parent, textvariable=var, values=values, state="readonly", font=("Microsoft YaHei", 10)).grid(row=row, column=col + 1, sticky="ew", padx=(0, 22), pady=8, ipady=4)

    def _build_detail(self) -> None:
        Label(self.detail_tab, text="已合并资金明细大表", font=("Microsoft YaHei", 14, "bold"), fg="#6A4614", bg="#FFF9ED").pack(anchor="w", padx=22, pady=(20, 3))
        Label(self.detail_tab, text="每行是一笔来自部门小表或手工新增的原始明细；按预计日期排序。", font=("Microsoft YaHei", 9), fg="#7B6550", bg="#FFF9ED").pack(anchor="w", padx=22, pady=(0, 10))
        cols = ("id", "department", "date", "category", "direction", "amount", "description", "source")
        self.detail_tree = ttk.Treeview(self.detail_tab, columns=cols, show="headings", height=19)
        heads = [("id", "序号", 55), ("department", "部门", 120), ("date", "预计日期", 110), ("category", "日报栏目", 130), ("direction", "收支方向", 85), ("amount", "金额（元）", 115), ("description", "事项说明", 270), ("source", "来源小表", 160)]
        for col, title, width in heads: self.detail_tree.heading(col, text=title); self.detail_tree.column(col, width=width, anchor="w")
        self.detail_tree.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        Button(self.detail_tab, text="刷新明细表", command=self.refresh, font=("Microsoft YaHei", 10), fg="#6A4614", bg="#F7D789", activebackground="#EFC361", relief="flat", padx=15, pady=8).pack(anchor="w", padx=22, pady=(0, 18))

    def _build_export(self) -> None:
        Label(self.export_tab, text="导出资金汇总大表（Excel）", font=("Microsoft YaHei", 14, "bold"), fg="#6A4614", bg="#FFF9ED").pack(anchor="w", padx=22, pady=(20, 3))
        Label(self.export_tab, text="导出一个 Excel：Sheet 1 为“资金明细大表”，Sheet 2 为“日报栏目汇总”。", font=("Microsoft YaHei", 9), fg="#7B6550", bg="#FFF9ED").pack(anchor="w", padx=22, pady=(0, 10))
        self._path_row(self.export_tab, "输出文件", self.output, "选择位置", self.choose_output)
        Button(self.export_tab, text="导出汇总大表 Excel", command=self.export, font=("Microsoft YaHei", 10, "bold"), fg="white", bg="#C88716", activebackground="#A96D0A", relief="flat", padx=18, pady=9, cursor="hand2").pack(anchor="w", padx=22, pady=14)

    def choose_source(self) -> None:
        path = filedialog.askopenfilename(title="选择部门 Excel 资金小表", filetypes=[("Excel 工作簿", "*.xlsx"), ("CSV 文件（兼容）", "*.csv"), ("所有文件", "*.*")])
        if path: self.source.set(path)

    def choose_output(self) -> None:
        path = filedialog.asksaveasfilename(title="保存资金汇总大表", initialfile=Path(self.output.get()).name, defaultextension=".xlsx", filetypes=[("Excel 工作簿", "*.xlsx")])
        if path: self.output.set(path)

    def download_template(self) -> None:
        messagebox.showinfo("Excel 表头要求", "请使用第一张工作表，并保留以下表头：\n\n部门｜预计日期｜日报栏目｜收支方向｜金额｜事项说明（可选）\n\n日报栏目须使用系统预置的 10 个栏目；金额单位为元。")

    def import_excel(self) -> None:
        path = Path(self.source.get().strip())
        if not path.exists():
            messagebox.showerror("请选择文件", "请先选择存在的部门 Excel 小表。"); return
        try:
            rows = parse_xlsx(path) if path.suffix.lower() == ".xlsx" else parse_csv(path.read_text(encoding="utf-8-sig"), path.name)
            count = insert_rows(rows)
        except (ValueError, UnicodeDecodeError) as error:
            messagebox.showerror("导入失败", str(error)); return
        self.status.set(f"导入完成：{path.name} 的 {count} 条明细已合并进资金明细大表。")
        self.refresh(); messagebox.showinfo("导入完成", f"已合并 {count} 条资金明细。")

    def add_record(self) -> None:
        try:
            count = insert_rows([{"department": self.department.get(), "date": self.happen_date.get(), "category": self.category.get(), "direction": self.direction.get(), "amount": self.amount.get(), "description": self.description.get("1.0", END).strip(), "source_file": "手工新增"}])
        except ValueError as error:
            messagebox.showerror("无法新增", str(error)); return
        self.department.set(""); self.amount.set(""); self.description.delete("1.0", END)
        self.status.set(f"已新增 {count} 条明细，并自动合并到资金明细大表。")
        self.refresh()

    def refresh(self) -> None:
        if not hasattr(self, "detail_tree"): return
        self.detail_tree.delete(*self.detail_tree.get_children())
        for row in records():
            self.detail_tree.insert("", "end", values=(row["id"], row["department"], row["happen_date"], row["category"], row["direction"], f'{row["amount"]:,.2f}', row["description"] or "—", row["source_file"]))

    def export(self) -> None:
        path = Path(self.output.get().strip())
        if not path.name:
            messagebox.showerror("未设置输出位置", "请选择导出文件的保存位置。"); return
        rows = records()
        if not rows:
            messagebox.showerror("无法导出", "当前没有资金明细。"); return
        path.parent.mkdir(parents=True, exist_ok=True)
        node = (ROOT / "node_modules").resolve().parent / "bin" / "node"
        if not node.exists():
            messagebox.showerror("无法导出", "未找到 Excel 导出运行环境，请联系工具维护人员。"); return
        result = summary()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as temp:
            json.dump({"records": rows, "summary": result}, temp, ensure_ascii=False)
            temp_path = Path(temp.name)
        try:
            subprocess.run([str(node), str(ROOT / "export_xlsx.mjs"), str(temp_path), str(path)], cwd=ROOT, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            messagebox.showerror("导出失败", error.stderr or error.stdout or "Excel 文件生成失败。"); return
        finally:
            temp_path.unlink(missing_ok=True)
        self.status.set(f"导出完成：共 {len(rows)} 条合并明细；Excel 包含“资金明细大表”和“日报栏目汇总”两个 Sheet。")
        messagebox.showinfo("导出完成", f"已导出 {len(rows)} 条合并明细。\n\nSheet 1：资金明细大表\nSheet 2：日报栏目汇总\n\n文件：\n{path}")


def main() -> int:
    try:
        root = Tk(); FundDetailApp(root); root.mainloop()
    except Exception as error:
        print("界面无法打开：", error); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
