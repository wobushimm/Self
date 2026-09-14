"""页面模块基类。

所有页面模块继承 BasePageModule，实现统一的接口：
  - PAGE_ID: 页面唯一标识（如 'p01_fine_management'）
  - TITLE: 页面标题（如 '① 产研精细化管理'）
  - CORE_QUESTION: 核心问题（如 '交付节奏是否可控？'）
  - DOMAIN_INDEX: 在快报中的页码序号（1-6）
  - fetch_data(snap): 从 snapshot 中提取本领域所需数据
  - build_context(data): 构建 HTML/PDF 渲染上下文（图表数据、结论文案、建议动作）
"""

from __future__ import annotations

from typing import Any


class BasePageModule:
    """页面模块基类。"""

    PAGE_ID: str = ""
    TITLE: str = ""
    CORE_QUESTION: str = ""
    DOMAIN_INDEX: int = 0

    def fetch_data(self, snap: dict) -> dict[str, Any]:
        """从 snapshot 中提取本领域所需数据。

        Args:
            snap: 统一数据快照（snapshot.json 的内容）

        Returns:
            本领域所需的数据字典
        """
        raise NotImplementedError

    def build_context(self, data: dict[str, Any], metrics: dict) -> dict[str, Any]:
        """构建渲染上下文，供 HTML/PDF 生成器使用。

        Args:
            data: fetch_data() 返回的领域数据
            metrics: 由 metrics_engine.create_metrics() 计算的指标字典（code → MetricResult）

        Returns:
            包含以下键的字典：
            - charts: 图表数据列表 [{id, title, type, data}]
            - conclusion: 结论文案
            - action: 建议动作
            - decision_brief: 决策提示短语
        """
        raise NotImplementedError
