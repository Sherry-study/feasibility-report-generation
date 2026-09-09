"""从工程事实（engineering facts）构建相互隔离的章节上下文。

本模块负责校验并规范化工程事实的固定顶层结构，随后按章节所需的
点分路径（dotted path）从事实中裁剪出该章节专属的“事实切片”，
供后续 Agent 编写章节时使用。
"""

from __future__ import annotations

import copy
from typing import Any


# 工程事实的固定顶层根字段列表。
# 这是对外约定的“固定根契约”（fixed root contract），
# 工程事实 JSON 的顶层只能且必须包含这些字段。
FACT_ROOTS = [
    "meta",               # 元信息，如 schema_version、生成时间等
    "sources",            # 资料来源清单
    "basic_info",         # 基本信息，如建设单位等
    "unit",               # 装置信息，如设计/改造工况、产品等
    "component_catalog",  # 组成部件/设备目录
    "process",            # 工艺流程（设计/改造流股等）
    "diagnosis",          # 诊断结论
    "scheme_analysis",    # 方案分析（候选方案等）
    "adopted_scheme",     # 已采纳方案
    "equipment",          # 设备资料
    "derived_facts",      # 派生事实（如折算后的物料消耗等）
]

# 每个根字段在没有值时的默认值，用于文档说明与预期类型对照。
ROOT_DEFAULTS = {
    "meta": {},
    "sources": [],
    "basic_info": {},
    "unit": {},
    "component_catalog": [],
    "process": {},
    "diagnosis": {},
    "scheme_analysis": {},
    "adopted_scheme": {},
    "equipment": {},
    "derived_facts": {},
}

# 每个根字段应有的顶层类型，用于严格校验。
ROOT_TYPES = {
    "meta": dict,
    "sources": list,
    "basic_info": dict,
    "unit": dict,
    "component_catalog": list,
    "process": dict,
    "diagnosis": dict,
    "scheme_analysis": dict,
    "adopted_scheme": dict,
    "equipment": dict,
    "derived_facts": dict,
}


def validate_fact_roots(facts: dict[str, Any]) -> None:
    """校验工程事实是否满足固定根契约。

    依次检查：顶层必须是 object、根字段不能缺失也不能多余、
    每个根字段类型正确、meta.schema_version 必须为 2.0。
    """
    if not isinstance(facts, dict):
        raise ValueError("engineering facts must be an object")
    # 缺失的根字段
    missing = sorted(set(FACT_ROOTS) - set(facts))
    if missing:
        raise ValueError(f"engineering facts missing required roots: {missing}")
    # 多余（不受支持）的根字段
    extra = sorted(set(facts) - set(FACT_ROOTS))
    if extra:
        raise ValueError(f"engineering facts has unsupported roots: {extra}")
    # 逐一检查根字段类型
    for root in FACT_ROOTS:
        if not isinstance(facts[root], ROOT_TYPES[root]):
            raise ValueError(f"engineering facts root {root} has invalid type")
    # 固定 schema 版本，保证契约可预期
    if facts["meta"].get("schema_version") != "2.0":
        raise ValueError("engineering facts meta.schema_version must be 2.0")


def normalize_fact_roots(facts: dict[str, Any]) -> dict[str, Any]:
    """校验通过后返回一份深拷贝（防御性副本），避免外部修改影响内部状态。"""
    validate_fact_roots(facts)
    return copy.deepcopy(facts)


def build_fact_slice(facts: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    """根据显式点分路径裁剪出单个章节所需的事实切片。

    只允许以 FACT_ROOTS 中的根字段开头的路径；路径取值失败则跳过，
    最终把取到的值按路径重新拼装成一棵嵌套 dict 返回。
    """
    slice_data: dict[str, Any] = {}
    for path in paths:
        # 路径必须以已知根字段开头，否则视为非法路径
        if not path or path.split(".", 1)[0] not in FACT_ROOTS:
            raise ValueError(f"unsupported fact path: {path}")
        value = get_path(facts, path)
        if value is None:
            continue
        # 深拷贝后写入，保证切片与原始事实隔离
        _set_path(slice_data, path, copy.deepcopy(value))
    return slice_data


def get_path(data: Any, path: str) -> Any:
    """按点分路径读取对象中的值；刻意不支持列表索引遍历。

    路径中任何一段不是 dict 或键不存在时，返回 None，表示“取值失败”。
    """
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    """按点分路径将值写入嵌套 dict，缺失的中间层级用空 dict 补齐。"""
    cursor = target
    parts = path.split(".")
    # 逐级创建中间节点，最后一层才真正赋值
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value