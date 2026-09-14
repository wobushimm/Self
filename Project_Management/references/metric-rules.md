# 项目月度快报指标规则

## 必要数据块

`project`：`project_id`、`project_name`、`project_code`、`manager_name`、`as_of_date`。

`quality_current` 与 `quality_history`：研发/人工实际与预算成本、成本执行率、实际/预估工时、工时执行率、需求验证率、Bug 关闭率、修复时长、缺陷逃逸率。

`requirement_detail`：`total_requirement_count`、`verified_requirement_count`、`req_verify_rate`。

`bug_detail`：`bug_total_count`、`bug_closed_count`、`bug_resolve_duration_seconds`、`bug_close_rate`、`bug_leak_rate`。

`workhour_summary`：`actual_allocated_hour`、`estimated_hour`、`reported_hour`、`member_count`。

`staff_daily_workhours`：`work_date`、`employee_id`、`project_id`、`allocated_hour`、`source_unit`。

## 公式

| 指标 | 公式 |
|---|---|
| 需求吞吐量 | `verified_requirement_count` |
| 需求验证通过率 | `verified_requirement_count / total_requirement_count` |
| 需求成本（人时） | `actual_allocated_hour / verified_requirement_count` |
| 需求成本（人天） | `actual_allocated_hour / 9 / verified_requirement_count` |
| 工时执行率 | `actual_workhour_month / estimated_workhour_month` |
| 工时偏差率 | `工时执行率 - 1` |
| 研发成本执行率 | `rd_actual_cost_month / rd_budget_cost_month` |
| 人工成本执行率 | `staff_actual_cost_month / staff_budget_cost_month` |
| 总费用执行率 | `(研发实际 + 人工实际) / (研发预算 + 人工预算)` |
| 费用执行偏差 | `总费用执行率 - 1` |
| 研发成本偏差 | `研发成本执行率 - 1` |
| 人工成本偏差 | `人工成本执行率 - 1` |
| Bug 关闭率 | `bug_closed_count / bug_total_count` |
| MTTR（小时） | `bug_resolve_duration_seconds / bug_closed_count / 3600` |
| 缺陷逃逸率 | 使用平台来源值；可获得线上 Bug 数时为 `online_bug_count / bug_total_count` |
| 人均每日项目投入时长 | `SUM(allocated_hour) / 去重的员工-日期数量` |

## 预警

- 工时偏差率：绝对值不超过 15% 为正常，15%–25% 为关注，超过 25% 为干预。
- 预算/费用偏差率：绝对值不超过 10% 为正常，10%–25% 为关注，超过 25% 为干预。
- Sprint/任务完成率：不低于 85% 为正常。
- 缺陷逃逸率：不超过 10% 为正常，10%–25% 为关注，超过 25% 为干预。
- 未配置目标的指标标记为“参考”，不推断其预警等级。

## 当前测试基准

| 字段 | 值 |
|---|---:|
| 总需求数 | 56 |
| 验证通过需求数 | 40 |
| Bug 总数 | 67 |
| 已关闭 Bug 数 | 66 |
| 修复时长累计 | 86400 秒（示例） |
| 项目分摊工时 | 1648 小时 |
