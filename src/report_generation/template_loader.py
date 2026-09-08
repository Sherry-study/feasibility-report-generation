"""加载并校验固定报告模板。

本模块负责读取报告模板（report_template.json）以及外部规则文件
（chapter_rules.json、standards_library.json），进行严格校验后，
把模板中声明的引用（标准组、章节契约）解析展开，最终返回一份
可直接使用的模板字典。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .context_builder import FACT_ROOTS
from .deterministic_builders import BUILDER_NAMES

# 各资源文件的默认路径（相对于本模块所在目录）
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "report_template.json"
RULES_DIR = Path(__file__).resolve().parent / "rules"
CHAPTER_RULES_PATH = RULES_DIR / "chapter_rules.json"
STANDARDS_LIBRARY_PATH = RULES_DIR / "standards_library.json"

# 模板章节 blocks 中允许的块类型（白名单）
ALLOWED_BLOCK_TYPES = {
    "static_text",        # 静态文本
    "standards_reference",  # 标准引用（需从标准库展开）
    "conditional_text",     # 条件文本（事实值非空时输出）
    "fact_value",           # 事实值输出
    "deterministic_table",  # 确定性表格
    "llm_section",          # 需 Agent 撰写的章节块
    "synthesis_section",    # 需 Agent 合成的章节块
}


def load_template(
    path: Path | None = None,
    chapter_rules_path: Path | None = None,
    standards_library_path: Path | None = None,
) -> dict[str, Any]:
    """读取、校验并解析报告模板（结合外部规则文件）。

    流程：读取三个 JSON -> 校验模板 -> 校验章节规则 -> 校验标准库
    -> 解析模板中的引用（标准组展开、契约填充）-> 规范化章节顺序。
    """
    template_path = path or TEMPLATE_PATH
    payload = _load_json(template_path)
    chapter_rules = _load_json(chapter_rules_path or CHAPTER_RULES_PATH)
    standards_library = _load_json(standards_library_path or STANDARDS_LIBRARY_PATH)
    _validate_template(payload)
    _validate_chapter_rules(chapter_rules, payload)
    _validate_standards_library(standards_library)
    _resolve_template_references(payload, chapter_rules, standards_library)
    _normalize_section_order(payload)
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件，并要求其顶层为 object。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _normalize_section_order(template: dict[str, Any]) -> None:
    """把 sections 列表顺序作为章节顺序的唯一权威来源（顺序号从 1 递增）。"""
    sections = template.get("sections")
    for index, section in enumerate(sections, 1):
        section["order"] = index


def _validate_template(template: dict[str, Any]) -> None:
    """校验报告模板的顶层结构与每个章节、每个块的字段合法性。"""
    if not isinstance(template, dict):
        raise ValueError("template must be an object")
    sections = template.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("template.sections must be a non-empty list")

    seen_ids: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("template section must be an object")
        # 章节允许的字段集合（超出则报错）
        allowed_section_keys = {"id", "title", "level", "table_id", "caption", "fallback", "blocks"}
        unsupported = sorted(set(section) - allowed_section_keys)
        if unsupported:
            raise ValueError(f"template section has unsupported fields: {unsupported}")
        for key in ("id", "title", "level", "table_id", "caption", "fallback", "blocks"):
            if key not in section:
                raise ValueError(f"template section missing {key}")
        section_id = section["id"]
        if not isinstance(section_id, str) or not section_id:
            raise ValueError("template section id must be a non-empty string")
        # 章节 id 不允许重复
        if section_id in seen_ids:
            raise ValueError(f"duplicate section id: {section_id}")
        seen_ids.add(section_id)
        if not isinstance(section["title"], str) or not section["title"]:
            raise ValueError(f"section {section_id} title must be a non-empty string")
        if not isinstance(section["level"], int) or section["level"] < 1:
            raise ValueError(f"section {section_id} level must be a positive integer")
        if section["table_id"] is not None and not isinstance(section["table_id"], str):
            raise ValueError(f"section {section_id} table_id must be a string or null")
        if section["caption"] is not None and not isinstance(section["caption"], str):
            raise ValueError(f"section {section_id} caption must be a string or null")
        if not isinstance(section["fallback"], str) or not section["fallback"]:
            raise ValueError(f"section {section_id} fallback must be a non-empty string")
        blocks = section.get("blocks", [])
        if not isinstance(blocks, list):
            raise ValueError(f"section {section_id} blocks must be a list")
        for block in blocks:
            _validate_block(section_id, block)


def _validate_block(section_id: str, block: Any) -> None:
    """校验单个块：类型合法、字段无多余、必填字段齐全、字符串字段非空。"""
    if not isinstance(block, dict) or block.get("type") not in ALLOWED_BLOCK_TYPES:
        raise ValueError(f"section {section_id} has invalid block type")
    block_type = block["type"]
    # 每种块类型允许的字段集合
    allowed_fields = {
        "static_text": {"type", "text"},
        "standards_reference": {"type", "group"},
        "conditional_text": {"type", "path", "text"},
        "fact_value": {"type", "path", "label"},
        "deterministic_table": {"type", "builder", "table_id", "caption"},
        "llm_section": {"type", "contract_ref"},
        "synthesis_section": {"type", "contract_ref"},
    }[block_type]
    # deterministic_table 的 table_id/caption 为可选覆盖项，其余字段必填
    optional_fields = {"table_id", "caption"} if block_type == "deterministic_table" else set()
    unsupported = sorted(set(block) - allowed_fields)
    if unsupported:
        raise ValueError(f"section {section_id} {block_type} block has unsupported fields: {unsupported}")
    # 除 type 外的必填字段都必须存在
    for key in allowed_fields - {"type"} - optional_fields:
        if key not in block:
            raise ValueError(f"section {section_id} {block_type} block missing {key}")
    # deterministic_table 的 builder 必须在已知构建器集合内
    if block_type == "deterministic_table" and block["builder"] not in BUILDER_NAMES:
        raise ValueError(f"section {section_id} uses unknown deterministic builder: {block['builder']}")
    # 必填字段必须是非空字符串
    for key in allowed_fields - {"type"} - optional_fields:
        if not isinstance(block[key], str) or not block[key].strip():
            raise ValueError(f"section {section_id} {block_type} block field {key} must be a non-empty string")
    # 可选字段若出现，也必须是非空字符串
    for key in optional_fields:
        if key in block and (not isinstance(block[key], str) or not block[key].strip()):
            raise ValueError(f"section {section_id} {block_type} block field {key} must be a non-empty string")


def _validate_chapter_rules(rules: dict[str, Any], template: dict[str, Any]) -> None:
    """校验章节规则文件，并确保它与模板中的 llm_section/synthesis_section 引用一致。"""
    if rules.get("schema_version") != "1.0":
        raise ValueError("chapter_rules.schema_version must be 1.0")
    writing_rules = rules.get("writing")
    synthesis_rules = rules.get("synthesis")
    if not isinstance(writing_rules, dict) or not isinstance(synthesis_rules, dict):
        raise ValueError("chapter_rules must contain writing and synthesis objects")

    # 规则中的章节 id 必须存在于模板 sections 中
    section_ids = {section["id"] for section in template["sections"]}
    for section_id in writing_rules:
        if section_id not in section_ids:
            raise ValueError(f"chapter writing rule references missing template section: {section_id}")
    for section_id in synthesis_rules:
        if section_id not in section_ids:
            raise ValueError(f"chapter synthesis rule references missing template section: {section_id}")

    # 模板中动态块的 contract_ref 必须与规则的键一一对应
    writing_refs = _dynamic_contract_refs(template, "llm_section")
    synthesis_refs = _dynamic_contract_refs(template, "synthesis_section")
    if set(writing_refs.values()) != set(writing_rules):
        raise ValueError("chapter writing rules must match template llm_section contract_ref values")
    if set(synthesis_refs.values()) != set(synthesis_rules):
        raise ValueError("chapter synthesis rules must match template synthesis_section contract_ref values")

    # 约定每个动态块的 contract_ref 与它所在章节 id 相同
    for section_id, contract_ref in writing_refs.items():
        if section_id != contract_ref:
            raise ValueError(f"section {section_id} llm_section contract_ref must match section id")
    for section_id, contract_ref in synthesis_refs.items():
        if section_id != contract_ref:
            raise ValueError(f"section {section_id} synthesis_section contract_ref must match section id")

    for section_id, contract in writing_rules.items():
        _validate_writing_contract(section_id, contract)
    for section_id, contract in synthesis_rules.items():
        _validate_synthesis_contract(section_id, contract, section_ids, set(synthesis_rules))


def _validate_writing_contract(section_id: str, contract: Any) -> None:
    """校验单个撰写（writing）章节契约的字段与约束。"""
    if not isinstance(contract, dict):
        raise ValueError(f"chapter writing rule {section_id} must be an object")
    required = {"purpose", "fact_paths", "research_group", "research_objectives", "prohibited_content"}
    if set(contract) != required:
        raise ValueError(f"chapter writing rule {section_id} has invalid fields")
    if not isinstance(contract["purpose"], str) or not contract["purpose"].strip():
        raise ValueError(f"chapter writing rule {section_id} purpose must be a non-empty string")
    if not isinstance(contract["research_group"], str):
        raise ValueError(f"chapter writing rule {section_id} research_group must be a string")
    _validate_string_list(section_id, contract["fact_paths"], "fact_paths")
    _validate_string_list(section_id, contract["research_objectives"], "research_objectives")
    _validate_string_list(section_id, contract["prohibited_content"], "prohibited_content")
    # research_group 与 research_objectives 必须同时存在或同时为空
    has_group = bool(contract["research_group"].strip())
    has_objectives = bool(contract["research_objectives"])
    if has_group != has_objectives:
        raise ValueError(f"chapter writing rule {section_id} research_group and research_objectives must be both present or both empty")
    # fact_paths 中每条路径必须从已知根字段开始
    for path in contract["fact_paths"]:
        if path.split(".", 1)[0] not in FACT_ROOTS:
            raise ValueError(f"chapter writing rule {section_id} has unsupported fact path: {path}")


def _validate_synthesis_contract(
    section_id: str,
    contract: Any,
    section_ids: set[str],
    synthesis_section_ids: set[str],
) -> None:
    """校验单个合成（synthesis）章节契约的字段与约束。"""
    if not isinstance(contract, dict):
        raise ValueError(f"chapter synthesis rule {section_id} must be an object")
    required = {"purpose", "source_section_ids", "prohibited_content"}
    if set(contract) != required:
        raise ValueError(f"chapter synthesis rule {section_id} has invalid fields")
    if not isinstance(contract["purpose"], str) or not contract["purpose"].strip():
        raise ValueError(f"chapter synthesis rule {section_id} purpose must be a non-empty string")
    _validate_string_list(section_id, contract["source_section_ids"], "source_section_ids")
    _validate_string_list(section_id, contract["prohibited_content"], "prohibited_content")
    # 来源章节必须真实存在于模板中
    missing_sources = sorted(set(contract["source_section_ids"]) - section_ids)
    if missing_sources:
        raise ValueError(f"chapter synthesis rule {section_id} references missing source sections: {missing_sources}")
    # 合成章节不能引用其他合成章节作为来源（避免产生层级依赖）
    synthesis_sources = sorted(set(contract["source_section_ids"]) & synthesis_section_ids)
    if synthesis_sources:
        raise ValueError(f"chapter synthesis rule {section_id} references synthesis sections: {synthesis_sources}")


def _validate_standards_library(library: dict[str, Any]) -> None:
    """校验标准库（standards_library）的结构：分组为 object，且各项为非空字符串。"""
    if library.get("schema_version") != "1.0":
        raise ValueError("standards_library.schema_version must be 1.0")
    groups = library.get("groups")
    if not isinstance(groups, dict) or not groups:
        raise ValueError("standards_library.groups must be a non-empty object")
    for group, items in groups.items():
        if not isinstance(group, str) or not group.strip():
            raise ValueError("standards_library group id must be a non-empty string")
        _validate_string_list(group, items, "items")


def _resolve_template_references(
    template: dict[str, Any],
    chapter_rules: dict[str, Any],
    standards_library: dict[str, Any],
) -> None:
    """解析模板中的引用：标准组展开为 items、动态章节点位填充契约。

    对 standards_reference 块，把 group 引用的标准列表写入 block["items"]；
    对 llm_section / synthesis_section 块，把对应契约深拷贝到 section["contract"]。
    """
    standards_groups = standards_library["groups"]
    for section in template["sections"]:
        section.pop("contract", None)
        for block in section["blocks"]:
            block_type = block["type"]
            if block_type == "standards_reference":
                group = block["group"]
                if group not in standards_groups:
                    raise ValueError(f"section {section['id']} references unknown standards group: {group}")
                block["items"] = list(standards_groups[group])
            elif block_type == "llm_section":
                section["contract"] = copy.deepcopy(chapter_rules["writing"][block["contract_ref"]])
            elif block_type == "synthesis_section":
                section["contract"] = copy.deepcopy(chapter_rules["synthesis"][block["contract_ref"]])


def _dynamic_contract_refs(template: dict[str, Any], block_type: str) -> dict[str, str]:
    """收集模板中某类动态块（llm_section/synthesis_section）的 {section_id: contract_ref} 映射。

    同时校验同一章节不能出现重复的同类动态块。
    """
    refs: dict[str, str] = {}
    for section in template["sections"]:
        for block in section["blocks"]:
            if block["type"] != block_type:
                continue
            section_id = section["id"]
            if section_id in refs:
                raise ValueError(f"section {section_id} has duplicate {block_type} blocks")
            refs[section_id] = block["contract_ref"]
    return refs


def _validate_string_list(section_id: str, value: Any, field: str) -> None:
    """校验某字段必须是非空字符串的列表。"""
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{section_id} {field} must be a list of non-empty strings")