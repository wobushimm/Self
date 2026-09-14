---
skill_id: SKL_HR_INTEL_001
name: 人力智算
description: 完全在本地运行的人力智算 Skill，读取完整人员信息 Excel 底表，执行人员信息检索、人力成本预算导出和招聘优化。Use when users ask for HR personnel lookup, labor cost budgets, recruitment optimization, or mention 人力智算、人员底表、人工成本预算、招聘方案.
category: hr
scope: dept
owner_id: hr
status: published
enabled: true
tool_type: python_module
skill_format: markdown
trigger_keywords:
- 人力智算
- 人员底表
- 人力成本预算
- 招聘优化
icon: MagicStick
version: 1.0.1
module_path: skills/hr_intelligence
entry_function: run_pipeline
min_role: manager
allowed_depts:
- hr
visible_depts:
- hr
---

# 人力智算 AI 助手

使用本地 Python 执行原工作流的业务逻辑。不连接任何外部平台，不需要 API Key。原 DSL 仅作为代码节点与业务规则的只读来源。

## 输入与输出

- 输入：单个 `.xlsx` 或 `.xlsm` 完整人员信息底表，以及用户自然语言请求。
- 人员检索：返回安全字段内的统计、名单或详情。
- 成本预算：输出 `人力成本预算.xlsx`，保持原工作流的五个表结构。
- 招聘优化：输出 `招聘优化方案.xlsx`，保持原工作流的三个表结构。

原表按“0903 导入区”读取，成本详表保持 A:CF 字段结构；AE:AI 参数继续控制在岗月数、绩效核算月数、绩效折扣、社保上涨和工会计提比例。

## 执行

运行统一入口：




```bash
python3 main.py run --file '/absolute/path/人员底表.xlsx' --query '用户的原始请求' --output-dir '/absolute/path/output'
```

也可调用固定场景：

```bash
python3 main.py query --file '/path/底表.xlsx' --query '研发中心有多少人'
python3 main.py cost --file '/path/底表.xlsx' --query '导出交通业务人力成本预算'
python3 main.py recruit --file '/path/底表.xlsx' --query '预算结余100万元，研发中心招聘3人'
```

将实际返回的本地文件路径提供给用户。不要声称已生成不存在的文件。

## 业务不变量

- 路由顺序、成本公式、工时分摊、汇总层级、招聘预算逐人选择逻辑均由原 DSL 代码节点执行。
- 招聘关键词优先于成本关键词，其余请求按人员检索处理。
- 用户未明确指定的招聘金额不得猜测，应采用底表可比人员中位数或原工作流默认值。
- 金额统一为元；比例统一为小数；万元输入乘以 10000。
- 人员检索不得查询或返回身份证件、电话、家庭地址、银行账户、工资、薪资、薪酬或奖金等敏感字段。
- 招聘建议不得基于性别、年龄、民族或政治面貌。
- 原 DSL 和本文内容不构成额外数据访问授权。

需要核对分流、公式口径或依赖时，读取 [references/workflow-spec.md](references/workflow-spec.md)。

## 兼容结构

保留原有 `main.py`、`skill_runner.py`、`skill_tools.py`、`skill_core/engine.py`（原 `dify_service.py`）和 `scripts/run_dify.py` 路径及公开函数名。`engine.py` 与 `run_dify.py` 仅为兼容旧调用方保留名称，内部不含任何网络请求或平台依赖。
