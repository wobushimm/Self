# 人力智算AI助手 Skill

版本 1.0.0 已完全脱离原工作流平台，人员底表只在本机读取和处理。

## 安装

```bash
python3 -m pip install -r requirements.txt
```

依赖只有 `openpyxl`，不需要任何外部平台、Ollama、API Key 或网络连接。

## 使用

```bash
python3 main.py check
python3 main.py run --file '/path/人员底表.xlsx' --query '研发中心有多少人' --output-dir output
python3 main.py cost --file '/path/人员底表.xlsx' --query '导出交通业务人力成本预算' --output-dir output
python3 main.py recruit --file '/path/人员底表.xlsx' --query '预算结余100万元，研发中心招聘3人' --output-dir output
```

## 目录结构

目录和旧版保持一致：

```text
人力智算skill/
├── SKILL.md
├── README.md
├── main.py
├── skill_runner.py
├── skill_tools.py
├── requirements.txt
├── _meta.json
├── agents/openai.yaml
├── assets/人力智算AI助手.yml
├── references/workflow-spec.md
├── scripts/run_dify.py
└── skill_core/
    ├── __init__.py
    ├── engine.py
    └── dsl_nodes/
```

`assets` 中的原始工作流 DSL 作为业务逻辑资产保留；本地引擎动态加载 `dsl_nodes/` 中的 Python 代码节点。原两个模型节点已改写为确定性 Python 参数解析，不调用任何模型服务。

## 输出

- 人员查询：终端和 JSON 返回查询结果。
- 成本预算：`人力成本预算.xlsx`，五个 Sheet。
- 招聘优化：`招聘优化方案.xlsx`，三个 Sheet。

底表格式或业务规则详情见 `references/workflow-spec.md`。
