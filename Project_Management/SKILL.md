---
skill_id: SKL_PROJECT_MGMT_001
name: 项目管理快报
description: 按项目 ID/名称 + 结算月份生成 PMO 月度快报（6 大领域、9 项核心指标），输出交互 HTML / PDF / Word
category: engineering
scope: dept
owner_id: rd,coop_dev
status: published
enabled: true
tool_type: python_module
skill_format: markdown
trigger_keywords:
- 项目管理快报
- 项目月报
- 项目快报
- PMO快报
- 项目指标
- 项目管理报告
compound_keywords:
- [项目, 快报]
- [项目, 月报]
- [PMO, 报告]
- [项目, 指标]
tags:
- 项目管理
- PMO
- 研发
- 快报
icon: DataAnalysis
version: 0.3.0
author: system
author_name: 系统预置
module_path: skills/Project_Management
entry_function: run_project_mgmt_skill
visible_depts:
- rd
- coop_dev
allowed_depts:
- rd
- coop_dev
min_role: manager
config:
  always_available: true
---

# 项目管理快报

按“项目名称 + 项目 ID + 月度结算周期”生成公司级 PMO 项目月度快报。输出交互 HTML，并提供 PDF/Word 导出；不得用对话文本替代快报文件。

## 权限说明

- 研发中心、合作发展部：**经理及以上**
- 公司领导：可使用（不受部门白名单限制）
- 其他部门 / 员工级：不可执行

## 输入与输出

输入：项目名称、项目 ID（若用户已知）、结算月份 `YYYY-MM`、可选导出格式。

输出：

- `ProjectMonthlySnapshot.json`：已校验的统一数据快照；
- 交互 HTML 快报；
- PDF 或 Word 文件（按用户选择）。

若缺少项目 ID，先调用项目主数据接口按名称查询；若返回多条项目，要求用户选择，不能猜测。

## 强制规则

- 月报按月结算；实时指标注明 `as_of_date`，表示上月初至调用时点的累计数据。
- 所有来源均以 `project_id + project_name` 校验一致；不一致时停止生成。
- 质量度量/质量平台是核心指标优先来源。
- 工时统一为小时；人天按 `1 人天 = 9 小时` 换算，并保留原始值和单位。
- 不编造、补零或以任务数替代需求数。缺失数据返回 `null` 并在快报标记“待补数据”。
- 认证信息只从环境变量或公司智能体运行环境读取，禁止写入代码、JSON、HTML、PDF 或日志。
- 先生成并校验 JSON，再生成快报；接口或指标校验失败时不得输出“数据正常”的快报。

## 工作流

### Phase 1：解析请求

1. 解析项目名称、项目 ID 和结算月份。
2. 若月份未提供，要求用户补充；本 Skill 不把实时数据当作周报或日报。
3. 计算目标月 `date_from`、`date_to`，并记录调用日 `as_of_date`。

### Phase 2：拉取统一数据快照

运行 `data_modules/fetch_data.py`（数据适配层），按以下逻辑拉取：

1. 项目主数据：项目编码、负责人、产品线、部门。
2. 质量度量当前值与近 4 月历史：工时、成本、需求验证、Bug 指标。
3. 需求汇总：总需求数、验证通过需求数。
4. Bug 汇总：总数、关闭数、修复时长、逃逸率。
5. 工时汇总与人员日分摊工时。

输出固定结构 `ProjectMonthlySnapshot.json`。字段详见 [references/metric-rules.md](references/metric-rules.md)。

### Phase 3：数据校验

执行以下校验并写入 `data_quality`：

1. 项目 ID、名称、编码跨来源一致。
2. 验证通过需求数不大于总需求数，且二者与需求验证通过率一致。
3. 已关闭 Bug 数不大于 Bug 总数，且二者与关闭率一致。
4. 项目汇总分摊工时与人员日分摊工时之和一致，容差 0.01 小时。
5. 研发/人工成本执行率与“实际/预算”一致；预算为 0 时标为缺失。
6. 必填字段、来源、拉取时间、数据状态齐全。

若有影响核心指标的 `missing_fields` 或 `warnings`，在快报中明确披露；对于项目或数据源不一致，停止生成。

### Phase 4：计算指标与洞察

按 [references/metric-rules.md](references/metric-rules.md) 计算，不得改变公式。快报按以下六个领域组织，每个领域必须包含适配图表、结论和建议动作：

1. 产研精细化管理：交付节奏是否可控？
2. 项目投入偏差：钱和时间花在哪了？偏了多少？
3. 人员投入健康度：团队是否在透支？
4. 产品质量：交付物靠不靠谱？
5. Bug 修复效率：问题解决得快不快？
6. 需求价值交付：做的东西有没有用？

图表选择遵循数据语义：趋势用折线图，实际/预算用分组柱状图，结构占比用实心饼图或环形图，成员投入用横向条形图。没有历史或明细数据时，不绘制假趋势图。

### Phase 5：生成与导出

1. 通过 `page_modules/` 中 6 个领域模块收集渲染上下文，由 `export_modules/html_generator.py` / `export_modules/pdf_generator.py` 统一生成。
2. 首屏展示项目、报告期、负责人、指标状态汇总及核心指标卡。
3. 后续按六个领域分页；末页输出 PMO 预警清单与数据质量说明。
4. PDF 为横向 A4，必须由已校验数据生成；Word 导出保留核心文字与表格。
5. 返回 HTML、PDF/Word 的文件路径及 `data_quality` 摘要。

## 示例话术

- 「列出可查询的项目」
- 「生成示例项目 2025年1月项目管理快报」
- 「项目 10001 2025-01 的项目月报，导出 PDF」
