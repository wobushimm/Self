#!/usr/bin/env python3
"""资金日报本地上传界面。

本文件仅负责选文件和展示结果；司库列映射、付款人工标注和特殊摘要规则匹配均由
generate_treasury_daily_template.py 处理，后续公司 UI 壳可直接替换本界面。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from tkinter import LEFT, NORMAL, DISABLED, X, Button, Entry, Frame, Label, StringVar, Tk
from tkinter import filedialog, messagebox

from generate_treasury_daily_template import DailyReportError, generate_report_from_source, money, sync_rules_from_source


DEMO_DIR = Path(__file__).resolve().parent.parent
# 当前已整理为：当天司库文件与长期规则文件分别保存，避免把历史 demo 数据带入日常使用。
DEFAULT_SOURCE = DEMO_DIR.parent / "测试表" / "司库交易明细.xlsx"
DEFAULT_TEMPLATE = DEMO_DIR.parent / "测试表" / "规则配置.xlsx"
DEFAULT_OUTPUT = DEMO_DIR / "output" / f"资金日报_{date.today().strftime('%Y%m%d')}.xlsx"


class DailyReportUploadUI:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("收付明细自动生成")
        self.root.geometry("820x390")
        self.root.minsize(720, 350)
        self.source_path = StringVar(value=str(DEFAULT_SOURCE))
        self.template_path = StringVar(value=str(DEFAULT_TEMPLATE))
        self.output_path = StringVar(value=str(DEFAULT_OUTPUT))
        self.opening_balance = StringVar()
        self.status = StringVar(value="第 1 步：选择当天司库文件。")
        self._build()

    def _build(self) -> None:
        self.root.configure(bg="#F7FAFC")
        Label(self.root, text="收付明细自动生成", font=("Microsoft YaHei", 20, "bold"), fg="#17365D", bg="#F7FAFC").pack(anchor="w", padx=30, pady=(25, 2))
        Label(
            self.root,
            text="每天上传司库文件，先生成“收付明细”；透视表刷新将在下一阶段接入。",
            font=("Microsoft YaHei", 10), fg="#5B6573", bg="#F7FAFC",
        ).pack(anchor="w", padx=30, pady=(0, 18))
        self._path_row("当天司库文件", self.source_path, "选择文件", self.choose_source)
        self._path_row("模板与规则", self.template_path, "选择文件", self.choose_template)
        self._path_row("输出收付明细", self.output_path, "选择位置", self.choose_output)

        balance = Frame(self.root, bg="#F7FAFC")
        balance.pack(fill=X, padx=30, pady=7)
        Label(balance, text="期初余额", width=10, anchor="w", font=("Microsoft YaHei", 10, "bold"), fg="#17365D", bg="#F7FAFC").pack(side=LEFT)
        Entry(balance, textvariable=self.opening_balance, font=("Microsoft YaHei", 10), relief="solid", bd=1).pack(side=LEFT, fill=X, expand=True, padx=(0, 8), ipady=6)
        Label(balance, text="日报首笔交易发生前的真实余额", font=("Microsoft YaHei", 9), fg="#6B7280", bg="#F7FAFC").pack(side=LEFT)

        action = Frame(self.root, bg="#F7FAFC")
        action.pack(fill=X, padx=30, pady=(20, 8))
        self.sync_button = Button(
            action, text="① 同步特殊规则", command=self.sync_rules, font=("Microsoft YaHei", 10, "bold"),
            fg="#17365D", bg="#D9EAF7", activebackground="#BDD7EE", relief="flat", padx=16, pady=9, cursor="hand2",
        )
        self.sync_button.pack(side=LEFT)
        self.generate_button = Button(
            action, text="② 生成资金日报", command=self.generate, font=("Microsoft YaHei", 10, "bold"),
            fg="white", bg="#17365D", activebackground="#295A8A", activeforeground="white", relief="flat", padx=18, pady=9, cursor="hand2",
        )
        self.generate_button.pack(side=LEFT, padx=10)

        Label(self.root, textvariable=self.status, font=("Microsoft YaHei", 10), fg="#4B5563", bg="#F7FAFC", wraplength=740, justify="left").pack(anchor="w", padx=30, pady=(4, 8))
        Label(
            self.root,
            text="操作顺序：收款自动归销售收款；付款摘要含“采购/费用/内部往来”时自动分类；无标注付款才查特殊摘要规则。",
            font=("Microsoft YaHei", 9), fg="#6B7280", bg="#F7FAFC", justify="left",
        ).pack(anchor="w", padx=30)

    def _path_row(self, label: str, variable: StringVar, button_text: str, callback) -> None:
        frame = Frame(self.root, bg="#F7FAFC")
        frame.pack(fill=X, padx=30, pady=7)
        Label(frame, text=label, width=10, anchor="w", font=("Microsoft YaHei", 10, "bold"), fg="#17365D", bg="#F7FAFC").pack(side=LEFT)
        Entry(frame, textvariable=variable, font=("Microsoft YaHei", 10), relief="solid", bd=1).pack(side=LEFT, fill=X, expand=True, padx=(0, 8), ipady=6)
        Button(
            frame, text=button_text, command=callback, font=("Microsoft YaHei", 9),
            fg="#17365D", bg="#D9EAF7", activebackground="#BDD7EE", relief="flat", padx=12, pady=7, cursor="hand2",
        ).pack(side=LEFT)

    def choose_source(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择包含司库交易明细与日报模板的 Excel 文件",
            initialdir=str(DEFAULT_SOURCE.parent), filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")],
        )
        if selected:
            self.source_path.set(selected)
            source = Path(selected)
            self.output_path.set(str(source.parent / f"资金日报_{date.today().strftime('%Y%m%d')}.xlsx"))
            self.status.set("已选择当天司库文件。若出现未标注付款，请先点击“同步特殊规则”。")

    def choose_template(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择长期保存的日报模板与特殊摘要规则文件",
            initialdir=str(DEFAULT_TEMPLATE.parent), filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")],
        )
        if selected:
            self.template_path.set(selected)

    def choose_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="选择生成日报的保存位置", initialdir=str(Path(self.output_path.get()).parent),
            initialfile=Path(self.output_path.get()).name, defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
        )
        if selected:
            self.output_path.set(selected)

    def _selected_file(self, value: StringVar, label: str) -> Path | None:
        path = Path(value.get().strip())
        if not path.exists():
            messagebox.showerror("找不到文件", f"请选择存在的 {label}。")
            return None
        return path

    def sync_rules(self) -> None:
        source_path = self._selected_file(self.source_path, "当天司库文件")
        template_path = self._selected_file(self.template_path, "模板与规则文件")
        if source_path is None or template_path is None:
            return
        self.sync_button.config(state=DISABLED, text="正在同步…")
        self.root.update_idletasks()
        try:
            added = sync_rules_from_source(source_path, template_path)
        except DailyReportError as error:
            messagebox.showerror("无法同步规则", str(error))
            self.status.set("同步失败：请检查司库交易明细的表头。")
        else:
            self.status.set(f"已同步规则：新增 {added} 条待填写特殊摘要规则。请打开“模板与规则”文件填写规则。")
            messagebox.showinfo(
                "规则已同步",
                f"已新增 {added} 条待填写规则。\n\n请打开“模板与规则”文件的 特殊摘要规则 Sheet：\n填写 F 列“编码(H)”和 G 列“收付项目(I)”，保存后再点击“生成资金日报”。",
            )
        finally:
            self.sync_button.config(state=NORMAL, text="① 同步特殊规则")

    def generate(self) -> None:
        source_path = self._selected_file(self.source_path, "当天司库文件")
        template_path = self._selected_file(self.template_path, "模板与规则文件")
        if source_path is None or template_path is None:
            return
        output_text = self.output_path.get().strip()
        if not output_text:
            messagebox.showerror("未设置输出位置", "请选择生成后的日报保存位置。")
            return
        try:
            opening_balance = money(self.opening_balance.get())
            if opening_balance < 0:
                raise DailyReportError("期初余额不能为负数。")
        except DailyReportError as error:
            messagebox.showerror("期初余额有误", str(error))
            return
        self.generate_button.config(state=DISABLED, text="正在生成…")
        self.status.set("正在读取司库明细、识别付款人工标注与特殊摘要规则、生成收付明细…")
        self.root.update_idletasks()
        try:
            result = generate_report_from_source(source_path, template_path, Path(output_text), opening_balance)
        except DailyReportError as error:
            messagebox.showerror("生成失败", str(error))
            self.status.set("生成失败：请检查输入表和特殊摘要规则。")
        else:
            pending = result["pending_h_i"]
            self.status.set(f"生成完成：共处理 {result['transactions']} 笔，H/I 待配置 {pending} 笔。")
            messagebox.showinfo(
                "收付明细已生成",
                f"已处理 {result['transactions']} 笔交易。\nH/I 待配置：{pending} 笔。\n\n输出文件：\n{result['output_path']}\n\n请查看“收付明细”Sheet 和 H_I待配置 Sheet。",
            )
        finally:
            self.generate_button.config(state=NORMAL, text="② 生成资金日报")


def main() -> int:
    print("正在打开“收付明细自动生成”界面…")
    try:
        root = Tk()
        DailyReportUploadUI(root)
        root.mainloop()
    except Exception as error:
        print("界面无法打开：", error)
        print("请确认已安装 Python 的 tkinter 组件，并执行：python -m pip install -r requirements.txt")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
