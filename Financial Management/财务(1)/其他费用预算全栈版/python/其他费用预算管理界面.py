#!/usr/bin/env python3
"""本地预算管理界面：导入、校验、提交、审核和汇总导出。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from tkinter import END, LEFT, RIGHT, X, Button, Entry, Frame, Label, StringVar, Tk
from tkinter import filedialog, messagebox, ttk

from budget_service import BudgetError, ROOT, TEMPLATE_PATH, create_template, export_summary, list_budgets, parse_budget_excel, review_budget, submit_budget_excel


class BudgetApp:
    def __init__(self, root: Tk) -> None:
        self.root = root; self.root.title("其他费用预算管理"); self.root.geometry("1080x680"); self.root.minsize(920, 580)
        self.bg, self.panel, self.text, self.muted = "#FFF8FB", "#FFF0F5", "#7C3E59", "#8E6878"
        self.primary, self.primary_active, self.secondary = "#D9779A", "#B95278", "#F6D7E3"
        self.source = StringVar(); self.rule_template = StringVar(value=str(TEMPLATE_PATH)); self.attachment = StringVar(); self.year = StringVar(value="2026"); self.output = StringVar(value=str(ROOT / "output" / f"其他费用预算汇总_{date.today():%Y%m%d}.xlsx")); self.status = StringVar(value="第 1 步：选择财务规则模板，再上传部门线下发送的预算明细。")
        self._build(); self.refresh()

    def _build(self) -> None:
        self.root.configure(bg=self.bg)
        style = ttk.Style(self.root); style.theme_use("clam")
        style.configure("TNotebook", background=self.bg, borderwidth=0); style.configure("TNotebook.Tab", background="#FCE6EE", foreground=self.text, padding=(18, 9), font=("Microsoft YaHei", 10, "bold")); style.map("TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", self.primary_active)])
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground="#5D4650", rowheight=30, font=("Microsoft YaHei", 10)); style.configure("Treeview.Heading", background="#F6D7E3", foreground=self.text, font=("Microsoft YaHei", 10, "bold"), relief="flat"); style.map("Treeview", background=[("selected", "#FBE1EB")], foreground=[("selected", self.text)])
        Label(self.root, text="其他费用预算管理", font=("Microsoft YaHei", 20, "bold"), fg=self.text, bg=self.bg).pack(anchor="w", padx=30, pady=(24,2))
        Label(self.root, text="导入 Excel → 自动校验 → 提交审核 → 汇总导出。所有数据仅在本次运行内有效，关闭程序后自动清空。", font=("Microsoft YaHei",10), fg=self.muted, bg=self.bg).pack(anchor="w", padx=30, pady=(0,16))
        notebook = ttk.Notebook(self.root); notebook.pack(fill="both", expand=True, padx=30, pady=(0,10))
        self.entry_tab = Frame(notebook, bg=self.bg); self.review_tab = Frame(notebook, bg=self.bg); self.report_tab = Frame(notebook, bg=self.bg)
        notebook.add(self.entry_tab, text="  预算填报与提交  "); notebook.add(self.review_tab, text="  财务审核  "); notebook.add(self.report_tab, text="  汇总导出  ")
        self._build_entry(); self._build_review(); self._build_report()
        Label(self.root, textvariable=self.status, font=("Microsoft YaHei",10), fg=self.muted, bg=self.bg, wraplength=980, justify="left").pack(anchor="w", padx=30, pady=(0,16))

    def _row(self, parent, label, variable, button_text, command) -> None:
        f=Frame(parent,bg=self.bg);f.pack(fill=X,padx=22,pady=8);Label(f,text=label,width=17,anchor="w",font=("Microsoft YaHei",10,"bold"),fg=self.text,bg=self.bg).pack(side=LEFT);Entry(f,textvariable=variable,font=("Microsoft YaHei",10),relief="solid",bd=1).pack(side=LEFT,fill=X,expand=True,padx=(0,8),ipady=6);Button(f,text=button_text,command=command,font=("Microsoft YaHei",9,"bold"),fg=self.text,bg=self.secondary,activebackground="#F0C1D3",relief="flat",padx=12,pady=7,cursor="hand2").pack(side=LEFT)

    def _build_entry(self) -> None:
        Label(self.entry_tab,text="导入部门线下发送的预算明细",font=("Microsoft YaHei",14,"bold"),fg=self.text,bg=self.bg).pack(anchor="w",padx=22,pady=(20,3))
        Label(self.entry_tab,text="先选择财务规则模板；系统以模板第一行字段为准，校验部门文件是否缺少必填列，再执行金额与同比校验。",font=("Microsoft YaHei",9),fg=self.muted,bg=self.bg).pack(anchor="w",padx=22,pady=(0,10))
        self._row(self.entry_tab,"财务规则模板",self.rule_template,"选择模板",self.choose_rule_template);self._row(self.entry_tab,"部门预算 Excel",self.source,"选择文件",self.choose_source);self._row(self.entry_tab,"证明附件（可选）",self.attachment,"选择附件",self.choose_attachment)
        f=Frame(self.entry_tab,bg=self.bg);f.pack(fill=X,padx=22,pady=8);Label(f,text="预算年度（报表标签）",width=17,anchor="w",font=("Microsoft YaHei",10,"bold"),fg=self.text,bg=self.bg).pack(side=LEFT);Entry(f,textvariable=self.year,width=16,font=("Microsoft YaHei",10),relief="solid",bd=1).pack(side=LEFT,ipady=6);Label(f,text="例如：2026。仅写入本次导出汇总表，不保存历史数据。",font=("Microsoft YaHei",9),fg=self.muted,bg=self.bg).pack(side=LEFT,padx=10)
        action=Frame(self.entry_tab,bg=self.bg);action.pack(fill=X,padx=22,pady=(18,8));Button(action,text="① 导入并校验",command=self.preview,font=("Microsoft YaHei",10,"bold"),fg=self.text,bg=self.secondary,activebackground="#F0C1D3",relief="flat",padx=15,pady=9,cursor="hand2").pack(side=LEFT);Button(action,text="② 提交财务审核",command=self.submit,font=("Microsoft YaHei",10,"bold"),fg="white",bg=self.primary,activebackground=self.primary_active,relief="flat",padx=17,pady=9,cursor="hand2").pack(side=LEFT,padx=10)
        self.preview_text=ttk.Treeview(self.entry_tab,columns=("department","item","last","budget","risk","message"),show="headings",height=10);heads=[("department","部门",130),("item","费用项目",160),("last","上年实际",115),("budget","本年预算",115),("risk","风险",90),("message","系统校验",430)];
        for col,title,width in heads:self.preview_text.heading(col,text=title);self.preview_text.column(col,width=width,anchor="w")
        self.preview_text.pack(fill="both",expand=True,padx=22,pady=(6,18))

    def _build_review(self) -> None:
        Label(self.review_tab,text="财务审核队列",font=("Microsoft YaHei",14,"bold"),fg=self.text,bg=self.bg).pack(anchor="w",padx=22,pady=(20,10))
        self.review_tree=ttk.Treeview(self.review_tab,columns=("id","department","item","budget","growth","risk","status","suggestion"),show="headings",height=16)
        for col,title,width in [("id","ID",48),("department","部门",100),("item","费用项目",135),("budget","本年预算",90),("growth","同比",80),("risk","风险",75),("status","状态",80),("suggestion","修改建议",445)]:self.review_tree.heading(col,text=title);self.review_tree.column(col,width=width,anchor="w")
        self.review_tree.pack(fill="both",expand=True,padx=22,pady=(0,12));a=Frame(self.review_tab,bg=self.bg);a.pack(fill=X,padx=22,pady=(0,18));Button(a,text="通过所选预算",command=lambda:self.review("已通过"),font=("Microsoft YaHei",10,"bold"),fg="white",bg="#78AE91",activebackground="#5E9277",relief="flat",padx=15,pady=8).pack(side=LEFT);Button(a,text="退回所选预算",command=lambda:self.review("已退回"),font=("Microsoft YaHei",10,"bold"),fg="#A64D6A",bg="#FBE0E9",activebackground="#F5C3D4",relief="flat",padx=15,pady=8).pack(side=LEFT,padx=10);Button(a,text="刷新队列",command=self.refresh,font=("Microsoft YaHei",10,"bold"),fg=self.text,bg=self.secondary,activebackground="#F0C1D3",relief="flat",padx=15,pady=8).pack(side=LEFT)

    def _build_report(self) -> None:
        Label(self.report_tab,text="预算审核结果与部门汇总导出",font=("Microsoft YaHei",14,"bold"),fg=self.text,bg=self.bg).pack(anchor="w",padx=22,pady=(20,3));Label(self.report_tab,text="Sheet1 展示全部当次明细（含已退回）；Sheet2 按部门分列已通过、已退回和待审核金额。",font=("Microsoft YaHei",9),fg=self.muted,bg=self.bg).pack(anchor="w",padx=22,pady=(0,10));self._row(self.report_tab,"导出汇总表",self.output,"选择位置",self.choose_output);Button(self.report_tab,text="导出审核结果 Excel",command=self.export,font=("Microsoft YaHei",10,"bold"),fg="white",bg=self.primary,activebackground=self.primary_active,relief="flat",padx=17,pady=9,cursor="hand2").pack(anchor="w",padx=22,pady=12)

    def choose_source(self):
        p=filedialog.askopenfilename(title="选择其他费用预算明细",filetypes=[("Excel 工作簿","*.xlsx")]);
        if p:self.source.set(p)
    def choose_rule_template(self):
        p=filedialog.askopenfilename(title="选择财务规则模板",filetypes=[("Excel 工作簿","*.xlsx")]);
        if p:self.rule_template.set(p)
    def choose_attachment(self):
        p=filedialog.askopenfilename(title="选择测算依据或报价附件",filetypes=[("支持文件","*.pdf *.doc *.docx *.xls *.xlsx *.png *.jpg"),("所有文件","*.*")]);
        if p:self.attachment.set(p)
    def choose_output(self):
        p=filedialog.asksaveasfilename(title="保存正式汇总表",initialfile=Path(self.output.get()).name,defaultextension=".xlsx",filetypes=[("Excel 工作簿","*.xlsx")]);
        if p:self.output.set(p)
    def preview(self):
        try: rows,errors=parse_budget_excel(Path(self.source.get().strip()),Path(self.rule_template.get().strip()))
        except BudgetError as e:messagebox.showerror("无法校验",str(e));return
        self.preview_text.delete(*self.preview_text.get_children())
        for r in rows:self.preview_text.insert("","end",values=(r['department'],r['cost_item'],f"{r['last_amount']:.2f}",f"{r['current_amount']:.2f}",r['risk_level'],r['validation_message']))
        if errors:messagebox.showerror("校验未通过","\n".join(errors));self.status.set(f"发现 {len(errors)} 项错误，已阻止提交。")
        else:self.status.set(f"校验通过：共 {len(rows)} 条预算，其中 {sum(r['risk_level']=='高风险' for r in rows)} 条高风险，允许提交财务审核。")
    def submit(self):
        try:
            count,_=submit_budget_excel(Path(self.source.get().strip()),self.year.get().strip(),Path(self.attachment.get().strip()) if self.attachment.get().strip() else None,Path(self.rule_template.get().strip()))
        except BudgetError as e:messagebox.showerror("提交失败",str(e));return
        self.status.set(f"提交成功：{count} 条预算已进入本次运行的财务审核队列；关闭程序后会自动清空。");messagebox.showinfo("已提交审核",f"已提交 {count} 条预算。") ;self.refresh()
    def refresh(self):
        if not hasattr(self,"review_tree"):return
        self.review_tree.delete(*self.review_tree.get_children())
        for r in list_budgets("待审核"):self.review_tree.insert("","end",values=(r['id'],r['department'],r['cost_item'],f"{r['current_amount']:.2f}","新增" if r['growth_rate'] is None else f"{r['growth_rate']:.1f}%",r['risk_level'],r['status'],r['suggestion']))
    def review(self,status):
        selected=self.review_tree.selection()
        if not selected:messagebox.showwarning("请选择记录","请先选择一条待审核预算。");return
        values=self.review_tree.item(selected[0],"values")
        if values[6] != "待审核":messagebox.showwarning("无法处理","仅可处理待审核预算。");return
        note="审核通过" if status=="已通过" else "请补充预算依据后重新提交"
        try:review_budget(int(values[0]),status,note)
        except BudgetError as e:messagebox.showerror("审核失败",str(e));return
        self.status.set(f"预算 ID {values[0]} 已{status}。");self.refresh()
    def export(self):
        try:count,total=export_summary(Path(self.output.get().strip()))
        except BudgetError as e:messagebox.showerror("无法导出",str(e));return
        self.status.set(f"已导出 {count} 条当次预算明细；已通过金额合计 {total:.2f} 万元。");messagebox.showinfo("导出完成",f"已导出 {count} 条当次预算。\nSheet1：预算审核明细（含退回记录）\nSheet2：按部门汇总。\n\n文件：\n{self.output.get()}")


def main() -> int:
    try:
        root=Tk();BudgetApp(root);root.mainloop()
    except Exception as exc:
        print("界面无法启动：",exc);return 1
    return 0

if __name__ == "__main__":sys.exit(main())
