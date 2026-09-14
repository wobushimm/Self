# 项目管理快报 Skill

> 面向公司项目管理的月度结算快报系统。按项目 ID + 结算月份自动生成 6 大管理领域、9 项核心指标的交互式快报，支持 HTML / PDF / Word 三种格式导出，以及跨月合并季度报告。

---

## 目录

- [功能概览](#功能概览)
- [核心指标](#核心指标)
- [系统架构](#系统架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [智能体集成](#智能体集成)
- [数据源与 API](#数据源与-api)
- [数据准确性验证](#数据准确性验证)
- [凭证管理](#凭证管理)
- [已知限制](#已知限制)

---

## 功能概览

| 功能 | 说明 | 状态 |
|------|------|:---:|
| 9 项 PMO 核心指标计算 | Sprint完成率、工时偏差率、预算偏差率、缺陷逃逸率、Bug关闭率、需求验证通过率、MTTR、需求成本、研发资源投入占比 | ✅ |
| 6 大领域分析页面 | 产研精细化管理、项目投入偏差、人员投入健康度、产品质量、Bug修复效率、需求价值交付 | ✅ |
| 团队健康度指标 | 成员投入明细、加班工时、总可用工时、跨项目投入占比 | ✅ |
| 单月快报生成 | HTML 交互快报（含 ECharts 图表） | ✅ |
| 单月 PDF 导出 | reportlab 横向 A4 打印版 | ✅ |
| 单月 Word 导出 | python-docx 表格+文字版 | ✅ |
| 跨月合并报告 | 多月份数据合并展示 + 季度汇总页 | ✅ |
| 跨月合并 PDF/Word | 跨月报告同样支持三种格式导出 | ✅ |
| 自然语言查询 | 支持"示例项目 1月快报"、"Q1季度报告"等自然语言输入 | ✅ |
| 季度自动匹配 | 支持 Q1-Q4、一二三四季度、第一季度等多种格式 | ✅ |
| 项目编号查询 | 支持"2026RDP006"格式精确匹配 | ✅ |
| 数据校验 | 7 项自动校验 + 双源交叉比对 | ✅ |
| 智能体工具集成 | 6 个 @tool 函数，支持 ReAct Agent 调用 | ✅ |

---

## 核心指标

9 项 PMO 核心指标口径（经 PMO 指标体系文档确认）：

| # | 指标名称 | 计算公式 | 数据来源 | 单位 |
|---|---------|---------|---------|------|
| 1 | Sprint完成率 | task_finish_count / task_count | 产品线看板 | % |
| 2 | 工时偏差率 | (actual_total_man_hour - plan_total_man_hour) / plan_total_man_hour | 产品线看板 | % |
| 3 | 预算偏差率 | ((actual_dev_cost + actual_staff_cost) - (plan_dev_cost + plan_staff_cost)) / (plan_dev_cost + plan_staff_cost) | 产品线看板 | % |
| 4 | 缺陷逃逸率 | fieldBugCount / totalBugCount (API 返回 bugLeakRate) | 质量度量 page / 看板 | ‰ |
| 5 | Bug关闭率 | closedBugCount / totalBugCount | 质量度量 page / 看板 | % |
| 6 | 需求验证通过率 | passedReqCount / totalReqCount | 质量度量 page | % |
| 7 | MTTR | bugResolveDurationExcludeRejectThrid / bugTotalCount / 3600 | 质量度量 page | 小时 |
| 8 | 需求成本 | actual_rd_man_hour / 完成需求数（按 9h/人天 折算） | 看板 + 质量度量 | 人天/项 |
| 9 | 研发资源投入占比 | 申报本项目工时人员数 / 公司总人数(229) | 人员工时 API | % |

**关键约定：**
- 公司总人数 = 229（硬编码常量 `_COMPANY_HEADCOUNT`）
- 需求成本分子用 `actual_rd_man_hour`（研发工时），不是 `actual_total_man_hour`（总工时）
- MTTR 分母是 Bug 总数（含未关闭），排除拒绝/第三方后的修复时长
- 需求吞吐量优先用 `passedReqCount`，fallback 到 `task_finish_count`

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  用户 / 智能体                                               │
│  "帮我生成示例项目 1月快报" / "Q1季度报告"                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  main.py — 统一 CLI 入口                                     │
│  run / query / list / modules                                │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  skill_runner.py — 调度引擎                                  │
│  意图解析(parse_intent) → 季度匹配 → 流水线编排 → 文档生成    │
└──────┬──────────────┬──────────────┬────────────────────────┘
       │              │              │
       ▼              ▼              ▼
┌──────────────┐ ┌──────────┐ ┌──────────────────────────┐
│ data_modules/│ │ metrics  │ │ page_modules/            │
│ 数据层       │ │ 指标计算  │ │ 6 大领域页面模块          │
│ fetch_data   │ │ 9 项指标  │ │ 独立获取数据 + 构建上下文 │
│ 10 API + 校验│ │          │ │                          │
└──────┬───────┘ └────┬─────┘ └──────────┬───────────────┘
       │              │                  │
       ▼              ▼                  ▼
┌──────────────────────────────────────────────────────────┐
│  snapshot.json — 统一数据快照（唯一数据源）                 │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  export_modules/ — 导出层                                 │
│  ┌─────────────┐ ┌─────────────┐ ┌────────────────────┐ │
│  │ 单月生成器   │ │ 跨月合并    │ │ report_runner.py   │ │
│  │ HTML/PDF/   │ │ HTML/PDF/   │ │ 统一编排           │ │
│  │ DOCX        │ │ DOCX        │ │                    │ │
│  └─────────────┘ └─────────────┘ └────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
项目管理快报skill/
├── SKILL.md                  # Skill 契约（YAML 元数据 + 强制规则 + 工作流）
├── _meta.json                # 元数据（创建/更新时间）
├── README.md                 # 本文档
├── requirements.txt          # Python 依赖
├── .gitignore                # 防止凭证/输出提交
│
├── main.py                   # 统一 CLI 入口（run / query / list / modules）
├── skill_tools.py            # 智能体工具入口（6 个 @tool 函数）
├── skill_runner.py           # 调度引擎（意图解析 + 季度匹配 + 流水线编排）
│
├── config/                   # 配置

文件
│   └── data_sources.yaml     # 数据源配置（10 个 API 端点说明）
│
├── data_modules/             # 数据层
│   ├── __init__.py
│   ├── fetch_data.py         # API 适配（10 个接口 + 7 项校验）
│   └── metrics_engine.py     # 指标计算（9 项 PMO 指标）
│
├── export_modules/           # 导出层
│   ├── __init__.py
│   ├── html_generator.py     # 单月 HTML 交互快报（ECharts 图表）
│   ├── pdf_generator.py      # 单月 PDF 打印版（reportlab 横向 A4）
│   ├── docx_generator.py     # 单月 Word 版（python-docx）
│   ├── merged_html_generator.py  # 跨月合并 HTML（含季度汇总页）
│   ├── merged_pdf_generator.py   # 跨月合并 PDF
│   ├── merged_docx_generator.py  # 跨月合并 Word
│   └── report_runner.py      # 导出统一编排
│
├── page_modules/             # 6 大领域页面模块
│   ├── __init__.py           # PAGE_REGISTRY 注册表 + 动态加载
│   ├── base_module.py        # BasePageModule 基类
│   ├── p01_fine_management.py    # 产研精细化管理
│   ├── p02_investment_deviation.py # 项目投入偏差
│   ├── p03_staff_health.py       # 人员投入健康度
│   ├── p04_product_quality.py    # 产品质量
│   ├── p05_bug_efficiency.py     # Bug 修复效率
│   └── p06_value_delivery.py     # 需求价值交付
│
├── report_modules/           # 数据校验
│   ├── __init__.py
│   ├── data_validator.py     # 数据校验逻辑
│   └── db_source.py          # 数据库数据源
│
├── skill_core/               # 核心组件
│   ├── __init__.py
│   ├── derived_fields.py     # 派生字段自动计算
│   ├── manual_input_store.py # 手工填报数据持久化
│   ├── pending_job.py        # 待续跑任务存储
│   └── llm_service.py        # LLM 服务（可选）
│
├── references/
│   └── metric-rules.md       # 指标公式与预警规则
│
├── docs/
│   └── 数据接口清单.md        # API 接口详细文档
│
├── tests/
│   └── test_multi_project.py # 端到端多项目验证
│
└── output/                   # 生成的快报输出（不入版本控制）
```

---

## 快速开始

### CLI 使用

```powershell
# 进入 Skill 目录
cd Project_Management-public

# 生成单月快报（HTML + PDF + Word）
py main.py run --project-id 10001 --project-name "示例项目" --month 2025-01 --format html pdf docx

# 生成跨月合并报告（季度报告）
py main.py run --project-id 10001 --project-name "示例项目" --month "2025-01:2025-03" --format html pdf docx

# 自然语言查询
py main.py query "示例项目 2025年1月快报"
py main.py query "Q1季度报告 示例项目"
py main.py query "2025年一季度 示例项目 合并快报"

# 列出所有项目
py main.py list

# 列出页面模块
py main.py modules
```

### 自然语言查询格式

支持以下季度查询格式：

| 格式 | 示例 |
|------|------|
| Q + 数字 | Q1、Q2、Q3、Q4 |
| 中文数字 | 一季度、二季度、三季度、四季度 |
| 第X季度 | 第一季度、第二季度、第三季度、第四季度 |
| 带年份 | 2026年Q2、2026年二季度、2026年第二季度 |
| 月份范围 | 2026-04 到 2026-06 |

---

## 智能体集成

### 工具函数

| 工具名 | 说明 | 参数 |
|--------|------|------|
| `list_projects` | 列出系统中所有项目 | 无 |
| `fetch_project_data` | 拉取统一数据快照 | project_id, project_name, month |
| `generate_html_report` | 生成 HTML 快报 | snapshot, output_dir |
| `generate_pdf_report` | 生成 PDF 快报 | snapshot, output_dir |
| `generate_metrics` | 计算指标输出 JSON | snapshot, output_dir |
| `run_full_pipeline` | 完整流水线 | project_id, project_name, month, formats |

### 智能体调用示例

```python
from skill_tools import SKILL_TOOLS, create_skill_agent

# 方式 1：直接使用工具
from skill_tools import run_full_pipeline
result = run_full_pipeline(
    project_id="10001",
    project_name="示例项目",
    month="2025-01",
    formats=["html", "pdf", "docx"]
)

# 方式 2：创建 ReAct Agent
agent = create_skill_agent(llm)
agent.invoke("帮我生成示例项目 1 月的快报，要 HTML 和 PDF 格式")
```

---

## 数据源与 API

系统通过环境变量连接项目管理平台。公开配置默认使用示例域名 `https://api.example.com`。

| # | API 名称 | 端点 | 用途 |
|---|---------|------|------|
| 1 | 项目主数据 | `GET /project` | 项目基本信息 |
| 2 | 项目看板 | `POST /reports/project` | 工时/成本/任务/质量指标 |
| 3 | 月度看板 | `POST /reports/monthly` | 原始口径回退 |
| 4 | 质量趋势 | `GET /quality/trends` | 近12月趋势图 |
| 5 | 质量指标 | `POST /quality/metrics` | Bug/需求原始计数 |
| 6 | 人员工时 | `GET /workhours/staff` | 部门人员工时 |
| 7 | 工时明细 | `GET /workhours/entries` | 加班/跨项目计算 |
| 8 | 考勤明细 | `GET /attendance/entries` | 总可用工时 |
| 9 | 季度看板 | `POST /reports/project`（跨月） | 季度聚合数据 |
| 10 | 预警阈值 | `GET /quality/targets` | 指标预警等级 |

详细接口文档见 [docs/数据接口清单.md](docs/数据接口清单.md)。

---

## 数据准确性验证

公开版不包含生产验证结果或真实项目指标。接入自己的测试数据后，应执行 API、指标引擎和生成器三层比对，并验证跨数据源的一致性。

---

## 凭证管理

API 凭证通过环境变量注入，代码中零凭证：

```powershell
# 方式 1：直接设置环境变量
$env:PMO_API_USERNAME = "your_username"
$env:PMO_API_PASSWORD = "your_password"

# 方式 2：使用 credentials.bat（不入版本控制）
# 编辑 credentials.bat 写入凭证后执行
```

认证流程：
1. 启动时自动调用 `/api/auth/login` 获取 access token
2. 后续请求使用 `Authorization: Bearer {token}` + Session Cookie
3. 如返回 401，自动重新登录

---

## 已知限制

| 限制 | 说明 | 影响 |
|------|------|------|
| 季度 API 不返回 bug_total_count | 平台 API 限制 | 季度汇总中 Bug 总数显示 "—" |
| 未来月份无质量度量数据 | 2026-08/09 的 quality_current 数据缺失 | 跨月报告中部分月份质量指标为空 |
| 工时汇总口径差异 | actual_allocated_hour 与 staff_daily 之和可能不一致（容差 ~112.5h） | 属平台数据源口径差异，非计算错误 |
| 需求验证通过率 | 部分月份质量平台 API 未返回 req_verify_rate 字段 | 标记为"待补"状态 |

---

## 依赖

| 依赖 | 用途 |
|------|------|
| Python 3.12+ | 运行环境 |
| requests | HTTP API 调用 |
| reportlab | PDF 生成 |
| python-docx | Word 文档生成 |
| lxml 5.3.1 | HTML 解析（注意：6.1.2 有 DLL 加载问题） |

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.3.0 | 2026-08 | 跨月合并报告、季度支持、自然语言查询、数据准确性验证 |
| 0.2.0 | 2026-07 | 9 项指标、6 大领域页面、HTML/PDF/Word 导出 |
| 0.1.0 | 2026-07 | 初始版本，基础指标查询 |
