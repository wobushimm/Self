"""DSL 代码节点 — 从原始工作流 DSL 提取的 Python 代码节点。

每个 .py 文件包含一个 main() 函数，签名与原 DSL 一致。
通过 node_main(node_id) 按 ID 调用对应节点。
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, Callable

_NODE_IDS = {
    "route_intent",
    "1784166032256",
    "1784186520243",
    "1783912721113",
    "1778747174285",
    "cost_query",
    "hire_build",
}

_cache: dict[str, Callable] = {}


def node_main(node_id: str, **kwargs: Any) -> dict[str, Any]:
    """调用指定 DSL 代码节点的 main() 函数。

    Args:
        node_id: 节点 ID（如 "route_intent", "1784166032256"）
        **kwargs: 传给 main() 的参数

    Returns:
        main() 函数的返回值 dict
    """
    if node_id not in _NODE_IDS:
        raise RuntimeError(f"未知节点 ID: {node_id}")
    if node_id not in _cache:
        mod = import_module(f"{__package__}.{node_id}")
        _cache[node_id] = mod.main
    return _cache[node_id](**kwargs)
