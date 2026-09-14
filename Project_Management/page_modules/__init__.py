"""页面模块注册表与动态加载。

PAGE_REGISTRY: page_id → (module_path, class_name, title, domain_index)
get_all_modules(): 按 domain_index 排序返回所有已注册模块实例
get_module(page_id): 按 page_id 返回单个模块实例
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from page_modules.base_module import BasePageModule

PAGE_REGISTRY: dict[str, tuple[str, str, str, int]] = {
    "p01_fine_management": (
        "page_modules.p01_fine_management", "P01FineManagement",
        "① 产研精细化管理", 1,
    ),
    "p02_investment_deviation": (
        "page_modules.p02_investment_deviation", "P02InvestmentDeviation",
        "② 项目投入偏差", 2,
    ),
    "p03_staff_health": (
        "page_modules.p03_staff_health", "P03StaffHealth",
        "③ 人员投入健康度", 3,
    ),
    "p04_product_quality": (
        "page_modules.p04_product_quality", "P04ProductQuality",
        "④ 产品质量", 4,
    ),
    "p05_bug_efficiency": (
        "page_modules.p05_bug_efficiency", "P05BugEfficiency",
        "⑤ Bug 修复效率", 5,
    ),
    "p06_value_delivery": (
        "page_modules.p06_value_delivery", "P06ValueDelivery",
        "⑥ 需求价值交付", 6,
    ),
}


def _load_module(page_id: str):
    """动态加载指定 page_id 的模块类并返回实例。"""
    if page_id not in PAGE_REGISTRY:
        raise KeyError(f"未知的页面模块 ID: {page_id}")
    module_path, class_name, title, domain_index = PAGE_REGISTRY[page_id]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def get_all_modules() -> list:
    """返回所有已注册页面模块实例，按 domain_index 排序。"""
    modules = []
    for page_id in sorted(PAGE_REGISTRY, key=lambda k: PAGE_REGISTRY[k][3]):
        modules.append(_load_module(page_id))
    return modules


def get_module(page_id: str):
    """返回指定 page_id 的页面模块实例。"""
    return _load_module(page_id)
