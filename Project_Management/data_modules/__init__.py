"""data_modules — 数据层
=====================
提供 API 数据适配和指标计算功能：
  - fetch_data: 7 个 API 适配函数 + 7 项校验 + snapshot 生成
  - metrics_engine: 9 项 PMO 指标计算
"""

from data_modules.fetch_data import fetch
from data_modules.metrics_engine import create_metrics, load_snapshot, MetricResult

__all__ = [
    "fetch",
    "create_metrics",
    "load_snapshot",
    "MetricResult",
]
