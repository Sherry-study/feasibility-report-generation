"""报告编写 Tool 的核心模块。

本模块刻意设计为“确定性”（deterministic）流程：
prepare 阶段为 Agent 的章节撰写准备相互隔离的工作包（work package），
finalize 阶段把 Agent 产出的工作结果（work results）合并导出为 Markdown / DOCX。
整个过程不调用 LLM、不访问网络、不使用缓存、不维护确认状态，也不做语义修复。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .context_builder import FACT_ROOTS, build_fact_slice, normalize_fact_roots
from .deterministic_builders import build_blocks
from .exporters import export_docx, export_markdown
from .template_loader import load_template

# 默认产物输出目录：仓库根目录下的 report_output_dir
DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "report_output_dir"
# 工作包/工作结果的媒体类型（JSON）
MEDIA_TYPE = "application/json"
# Agent 章节撰写允许使用的块类型（白名单）
ALLOWED_AGENT_BLOCKS = {"paragraph", "bullet_list", "numbered_list"}
# 报告正文不得暴露上游内部布尔字段名。
RAW_INTERNAL_MARKERS = (
    "optimized=true",
    "optimized = true",
    "optimizated=true",
    "optimizated = true",
)
IMPLEMENTATION_SCHEDULE_SECTION_ID = "18.2"
IMPLEMENTATION_SCHEDULE_TABLE_ID = "表18.1"
IMPLEMENTATION_SCHEDULE_CAPTION = "项目实施进度计划表"
IMPLEMENTATION_SCHEDULE_HEADERS = ["阶段", "主要内容", "预计时长", "前置条件"]
IMPLEMENTATION_SCHEDULE_STAGES = [
    "项目前期（各报告编制及审批）",
    "基础设计",
    "施工图设计",
    "设备采购",
    "土建施工",
    "安装工程",
    "试生产",
]
# 章节级摘要允许包含的字段，用于合成章节与结果校验
SUMMARY_FIELDS = [
    "conclusions",
    "key_facts",
    "conditions",
    "risks",
    "recommendations",
    "unresolved_items",
]


def execute(request: dict[str, Any], artifact_dir: str | Path | None = None) -> dict[str, Any]:
    """Tool 的外部入口，根据 operation 分发到 prepare 或 finalize。

    所有异常在此统一捕获并转换为 status=failed 的结果，而不是抛出堆栈，
    以符合 Tool 边界对外的返回约定。
    """
    try:
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        operation = request.get("operation")
        # 解析并规整产物目录
        output_dir = (Path(artifact_dir) if artifact_dir is not None else DEFAULT_ARTIFACT_DIR).resolve()
        if operation == "prepare":
            # 拒绝未声明的字段，避免多余输入造成歧义
            _reject_undeclared_fields(
                request,
                {"operation", "engineering_facts_uri", "report_context"},
                "prepare request",
            )
            return _prepare(request, output_dir)
        if operation == "finalize":
            _reject_undeclared_fields(
                request,
                {"operation", "work_package_uri", "work_results_uri", "work_results"},
                "finalize request",
            )
            return _finalize(request, output_dir)
        raise ValueError("operation must be prepare or finalize")
    except Exception as exc:  # noqa: BLE001 - Tool boundary returns failed, not traceback.
        return {
            "status": "failed",
            "artifact": None,
            "artifacts": None,
            "summary": {},
            "diagnostics": [_diagnostic("fatal", "REPORT_TOOL_FAILED", str(exc))],
        }


def _prepare(request: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    """prepare 阶段：读取工程事实 + 模板，产出 work_package.json。

    核心产物是一个“工作包”，其中包含：
    - sections：全部章节的结构信息（含确定性块与 Agent 槽位）
    - research_tasks / writing_tasks / synthesis_tasks：分发给后续 Agent 的任务
    - deterministic_summaries：无需 Agent、直接由确定性内容生成的章节摘要
    """
    facts_uri = _required_string(request, "engineering_facts_uri")
    report_context = request.get("report_context") or {}
    if not isinstance(report_context, dict):
        raise ValueError("report_context must be an object")
    _reject_undeclared_fields(report_context, {"project_name"}, "report_context")

    # 读取并规范化工程事实（规范化会做固定根契约校验）
    facts_path = _uri_to_path(facts_uri)
    raw_facts = _load_json(facts_path)
    facts = normalize_fact_roots(raw_facts)
    template = load_template()
    project_name = _project_name(facts, report_context)
    # 叶子章节：只有叶子章节才允许没有任何确定性块和 Agent 槽位时使用 fallback
    leaf_section_ids = _leaf_section_ids(template["sections"])

    sections = []
    research_task_groups: dict[str, dict[str, Any]] = {}
    writing_tasks = []
    synthesis_tasks = []
    sections_by_id: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, str]] = []
    artifact_dir.mkdir(parents=True, exist_ok=True)
    # 每个需 Agent 编写的章节，其事实切片会落盘到该目录
    context_dir = artifact_dir / "chapter_contexts"
    context_dir.mkdir(parents=True, exist_ok=True)

    for section in template["sections"]:
        section_contract = section.get("contract", {})
        fact_paths = section_contract.get("fact_paths", [])
        # 按契约声明的事实路径裁剪该章节的事实切片
        fact_slice = build_fact_slice(facts, fact_paths)
        # 仅由工程事实确定性构建的块（不依赖 Agent）
        deterministic_blocks = build_blocks(section, facts, diagnostics)
        # Agent 槽位：synthesis（合成章节）或 writing（撰写章节）或 None
        agent_slot = _agent_slot(section)
        # 叶子章节若既无确定性块也无 Agent 槽位，则用 fallback 兜底
        if not deterministic_blocks and agent_slot is None and section["id"] in leaf_section_ids:
            deterministic_blocks.append({"type": "paragraph", "text": section["fallback"]})
        prepared = {
            "id": section["id"],
            "title": section["title"],
            "level": section["level"],
            "order": section["order"],
            "table_id": section["table_id"],
            "caption": section["caption"],
            "fallback": section["fallback"],
            "deterministic_blocks": deterministic_blocks,
            "agent_slot": agent_slot,
        }
        sections.append(prepared)
        sections_by_id[section["id"]] = prepared

        # 章节含 llm_section 块 => 需要 Agent 撰写
        if _has_block(section, "llm_section"):
            context_path = _write_chapter_context(context_dir, section["id"], fact_slice)
            research_task_id = _register_research_task(research_task_groups, section["id"], section_contract)
            writing_tasks.append(
                {
                    "task_id": f"writing:{section['id']}",
                    "section_id": section["id"],
                    "section_title": section["title"],
                    "contract": section_contract,
                    "fact_paths": fact_paths,
                    "context_uri": context_path.as_uri(),
                    "research_task_id": research_task_id,
                    "output_contract": _output_contract(section["id"]),
                }
            )
        # 章节含 synthesis_section 块 => 需要 Agent 汇总多个来源章节
        if _has_block(section, "synthesis_section"):
            synthesis_tasks.append(
                {
                    "task_id": f"synthesis:{section['id']}",
                    "section_id": section["id"],
                    "section_title": section["title"],
                    "contract": section_contract,
                    "context": {
                        "source_section_ids": section_contract.get("source_section_ids", []),
                        "summary_fields": SUMMARY_FIELDS,
                    },
                    "output_contract": _output_contract(section["id"]),
                }
            )

    # 那些既不需要 Agent 撰写、又被合成章节引用来源的章节，需要确定性摘要
    deterministic_summaries = _build_deterministic_summaries(
        sections_by_id,
        writing_tasks,
        synthesis_tasks,
    )
    work_package = {
        "schema_version": "1.0",
        "created_at": _deterministic_created_at(facts),
        "status": "prepared",
        "project_name": project_name,
        "engineering_facts": {
            "uri": facts_path.as_uri(),
            "root_keys": FACT_ROOTS,
        },
        "template": {
            "id": template["template_id"],
            "version": template["version"],
        },
        "sections": sections,
        "research_tasks": list(research_task_groups.values()),
        "writing_tasks": writing_tasks,
        "deterministic_summaries": deterministic_summaries,
        "synthesis_tasks": synthesis_tasks,
    }
    package_path = artifact_dir / "work_package.json"
    _write_json(package_path, work_package)
    return {
        "status": "prepared",
        "artifact": {
            "uri": package_path.as_uri(),
            "schema_version": "1.0",
            "media_type": MEDIA_TYPE,
        },
        "summary": {
            "research_task_count": len(research_task_groups),
            "writing_task_count": len(writing_tasks),
            "deterministic_summary_count": len(deterministic_summaries),
            "synthesis_task_count": len(synthesis_tasks),
        },
        "diagnostics": diagnostics,
    }


def _finalize(request: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    """finalize 阶段：用 Agent 结果回填章节，导出 Markdown 与 DOCX。

    Agent 工作结果有三种互斥输入路径（见 _agent_work_results）：inline
    ``work_results`` 对象、``work_results_uri`` 指向的 JSON 文件，以及两者
    均未提供时的空结果兜底。空结果兜底不视为失败：Agent 章节走现有
    fallback 机制，生成不完整初稿并写入 warning 诊断。

    组装逻辑：每章先取确定性块，若存在 Agent 槽位则从 writing_results /
    synthesis_results 中取对应结果拼入；结果缺失或非法时回退到 fallback。
    """
    package_path = _uri_to_path(_required_string(request, "work_package_uri"))
    package = _load_json(package_path)
    _validate_package(package)
    results, agent_results_provided = _agent_work_results(request)
    _validate_results_envelope(results)

    diagnostics: list[dict[str, str]] = []
    if not agent_results_provided:
        # 没有 Agent 工作结果时仍继续生成报告：Agent 章节走 fallback 兜底，
        # 并在返回中明确提示本次报告为不完整初稿。
        diagnostics.append(
            _diagnostic(
                "warning",
                "WORK_RESULTS_NOT_PROVIDED",
                "未提供 Agent 工作结果（work_results 与 work_results_uri 均未传入），"
                "本次报告为不完整初稿，Agent 章节均使用模板 fallback 兜底。",
            )
        )
    # 将结果列表转为 {section_id: result} 的映射，并校验 section_id 合法且无重复
    writing_results = _result_map(
        results["writing_results"],
        {task["section_id"] for task in package.get("writing_tasks", [])},
        "writing_results",
    )
    synthesis_results = _result_map(
        results["synthesis_results"],
        {task["section_id"] for task in package.get("synthesis_tasks", [])},
        "synthesis_results",
    )
    report_sections: list[dict[str, Any]] = []
    fallback_count = 0

    for section in package["sections"]:
        blocks = list(section.get("deterministic_blocks", []))
        slot = section.get("agent_slot")
        if slot:
            # 根据槽位类型挑选对应的 Agent 结果
            result = (
                synthesis_results.get(section["id"])
                if slot == "synthesis"
                else writing_results.get(section["id"])
            )
            agent_blocks = _valid_agent_blocks(result, section["id"])
            if agent_blocks is None:
                # Agent 结果缺失或非法 => 使用 fallback 并记录告警
                fallback_count += 1
                diagnostics.append(
                    _diagnostic(
                        "warning",
                        "SECTION_FALLBACK",
                        f"section {section['id']} missing or invalid; fallback used.",
                    )
                )
                blocks.append({"type": "paragraph", "text": section["fallback"]})
            else:
                blocks.extend(agent_blocks)
        report_sections.append(
            {
                "id": section["id"],
                "title": section["title"],
                "level": section["level"],
                "order": section["order"],
                "blocks": blocks,
            }
        )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = artifact_dir / "可行性研究报告_初稿.md"
    docx_path = artifact_dir / "可行性研究报告_初稿.docx"
    tmp_markdown_path = markdown_path.with_name(f"{markdown_path.name}.tmp")
    tmp_docx_path = docx_path.with_name(f"{docx_path.name}.tmp")
    report = {
        "project_name": package["project_name"],
        "cover": _build_cover(package),
        "properties": {
            "title": f"{package['project_name']}可行性研究报告（初稿）",
            "subject": "流程工业改造项目可行性研究报告",
        },
        "sections": report_sections,
    }
    export_markdown(report, tmp_markdown_path)
    export_docx(report, tmp_docx_path)
    os.replace(tmp_markdown_path, markdown_path)
    os.replace(tmp_docx_path, docx_path)
    markdown_content = markdown_path.read_text(encoding="utf-8")
    return {
        "status": "completed",
        "artifacts": {"docx": docx_path.as_uri(), "markdown": markdown_path.as_uri()},
        "markdown_content": markdown_content,
        "summary": {
            "section_count": len(report_sections),
            "fallback_section_count": fallback_count,
        },
        "diagnostics": diagnostics,
    }


def _agent_work_results(request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """解析 finalize 的 Agent 工作结果输入，返回 (results, provided)。

    ``work_results``（inline 对象）与 ``work_results_uri``（JSON 文件）表示
    同一类数据，只是传入方式不同，因此只能二选一；同时提供时直接报参数
    冲突错误，避免两份结果不一致。两者都未提供时返回空结果
    （provided=False），由调用方生成不完整初稿并写入 warning 诊断，而不是
    直接失败。
    """
    inline_results = request.get("work_results")
    results_uri = request.get("work_results_uri")
    if inline_results is not None and results_uri is not None:
        raise ValueError("work_results and work_results_uri are mutually exclusive; provide only one of them")
    if inline_results is not None:
        if not isinstance(inline_results, dict):
            raise ValueError("work_results must be an object")
        return inline_results, True
    if results_uri is not None:
        # 提供了 URI 却非法（非字符串/空串）时直接失败，不静默降级为空结果
        if not isinstance(results_uri, str) or not results_uri.strip():
            raise ValueError("work_results_uri must be a non-empty string when provided")
        return _load_json(_uri_to_path(results_uri)), True
    return {"schema_version": "1.0", "writing_results": [], "synthesis_results": []}, False


def _build_cover(package: dict[str, Any]) -> dict[str, Any]:
    """从工作包回读工程事实，构造封面所需的四要素。

    建设单位取自 basic_info.construction_unit；事实文件缺失或读取失败时
    退化为空字符串，不阻塞导出。
    """
    company_name = ""
    facts_uri = (package.get("engineering_facts") or {}).get("uri")
    if facts_uri:
        try:
            facts = _load_json(_uri_to_path(facts_uri))
            company_name = str(get_path_or_empty(facts, "basic_info.construction_unit") or "").strip()
        except Exception:  # noqa: BLE001 - 封面信息非关键，读取失败不阻塞导出。
            company_name = ""
    return {
        "company_name": company_name,
        "project_name": package.get("project_name", ""),
        "report_title": "可行性研究报告（初稿）",
        "project_code": "xxxxxx",
    }


def _project_name(facts: dict[str, Any], report_context: dict[str, Any]) -> str:
    """确定项目名称。

    优先级：显式传入的 project_name > “建设单位 + 装置名称 + 技术改造项目” >
    “装置名称 + 技术改造项目” > 默认“工业装置技术改造项目”。
    """
    explicit = str(report_context.get("project_name") or "").strip()
    if explicit:
        return explicit
    construction_unit = str(get_path_or_empty(facts, "basic_info.construction_unit")).strip()
    unit_name = str(get_path_or_empty(facts, "unit.name")).strip()
    if construction_unit and unit_name:
        return f"{construction_unit}{unit_name}技术改造项目"
    if unit_name:
        return f"{unit_name}技术改造项目"
    return "工业装置技术改造项目"


def _register_research_task(
    research_task_groups: dict[str, dict[str, Any]],
    section_id: str,
    section_contract: dict[str, Any],
) -> str | None:
    """把某章节登记到其所属的“研究任务组”。

    按 research_group 聚合：同一组的章节合并为一个研究任务，
    目标章节列表与目标（objectives）去重追加。无分组或无目标时返回 None。
    """
    group = str(section_contract.get("research_group") or "").strip()
    objectives = section_contract.get("research_objectives", [])
    if not group or not objectives:
        return None
    # 首次出现该分组时初始化任务骨架
    task = research_task_groups.setdefault(
        group,
        {
            "task_id": f"research:{group}",
            "research_group": group,
            "target_section_ids": [],
            "objectives": [],
        },
    )
    task["target_section_ids"].append(section_id)
    for objective in objectives:
        if objective not in task["objectives"]:
            task["objectives"].append(objective)
    return task["task_id"]


def _write_chapter_context(context_dir: Path, section_id: str, fact_slice: dict[str, Any]) -> Path:
    """把单个章节的事实切片落盘成一个 JSON 文件，便于传递给 Agent 使用。"""
    context_path = context_dir / f"{_safe_section_id(section_id)}.json"
    _write_json(context_path, {"section_id": section_id, "fact_slice": fact_slice})
    return context_path


def _build_deterministic_summaries(
    sections_by_id: dict[str, dict[str, Any]],
    writing_tasks: list[dict[str, Any]],
    synthesis_tasks: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    """为“被合成章节引用来源、但自身不需要 Agent 撰写”的章节生成确定性摘要。

    合成章节会引用若干来源章节；来源章节若本身是确定性构建的，则无需 Agent，
    直接从其确定性块提炼出结构化摘要，供合成 Agent 使用。
    """
    writing_section_ids = {task["section_id"] for task in writing_tasks}
    synthesis_section_ids = {task["section_id"] for task in synthesis_tasks}
    source_section_ids: set[str] = set()
    for task in synthesis_tasks:
        source_section_ids.update(task["context"].get("source_section_ids", []))

    summaries: dict[str, dict[str, list[str]]] = {}
    for section_id, section in sections_by_id.items():
        # 只处理被当作合成来源的章节
        if section_id not in source_section_ids:
            continue
        # 该章节本身需要 Agent 编写或合成，则交给 Agent，不在此生成确定性摘要
        if section_id in writing_section_ids or section_id in synthesis_section_ids:
            continue
        summaries[section_id] = _deterministic_section_summary(section)
    return summaries


def _deterministic_section_summary(section: dict[str, Any]) -> dict[str, list[str]]:
    """把章节的确定性块提炼成结构化摘要（覆盖 SUMMARY_FIELDS 各字段）。"""
    summary = {field: [] for field in SUMMARY_FIELDS}
    fallback = str(section.get("fallback", "")).strip()
    for block in section.get("deterministic_blocks", []):
        block_type = block.get("type")
        if block_type == "paragraph":
            text = str(block.get("text", "")).strip()
            if text:
                # 段落内容等于 fallback 说明是兜底占位，归入 unresolved_items；
                # 否则视为关键事实。
                target = "unresolved_items" if text == fallback else "key_facts"
                summary[target].append(text)
        elif block_type in {"bullet_list", "numbered_list"}:
            # 列表项归入关键事实，最多取前 12 条
            summary["key_facts"].extend(
                str(item).strip()
                for item in block.get("items", [])[:12]
                if str(item).strip()
            )
        elif block_type == "table":
            # 表格先取“表号 + 表题”作为标题，再取表格摘要行
            heading = " ".join(
                str(value).strip()
                for value in (block.get("table_id"), block.get("caption"))
                if str(value or "").strip()
            )
            if heading:
                summary["key_facts"].append(heading)
            summary["key_facts"].extend(_table_summary_rows(block))
    # 若所有字段都为空，且存在 fallback，则归入 unresolved_items
    if not any(summary.values()):
        if fallback:
            summary["unresolved_items"].append(fallback)
    return summary


def _table_summary_rows(block: dict[str, Any]) -> list[str]:
    """把表格行转换为“表头：单元格”的摘要文本列表（最多 8 行）。"""
    headers = [str(item).strip() for item in block.get("headers", []) if str(item).strip()]
    rows = []
    for row in block.get("rows", [])[:8]:
        cells = [str(item).strip() for item in row if str(item).strip()]
        if not cells:
            continue
        if headers and len(headers) == len(cells):
            # 表头与单元格数量一致时，用“表头：单元格”拼接，便于阅读
            rows.append("；".join(f"{header}：{cell}" for header, cell in zip(headers, cells, strict=True)))
        else:
            rows.append("；".join(cells))
    return rows


def _safe_section_id(section_id: str) -> str:
    """把章节 id 清洗为合法的文件名字符（去除非字母数字，用下划线替换）。"""
    safe = re.sub(r"[^A-Za-z0-9]+", "_", section_id).strip("_")
    return safe or "section"


def _deterministic_created_at(facts: dict[str, Any]) -> str:
    """仅使用来源元数据，绝不依赖时钟，保证工作包完全可复现。"""
    generated_at = get_path_or_empty(facts, "meta.generated_at")
    if isinstance(generated_at, str) and generated_at.strip():
        return generated_at.strip()
    # 无来源时间时使用固定的零时间，避免每次运行结果不一致
    return "1970-01-01T00:00:00+00:00"


def _leaf_section_ids(sections: list[dict[str, Any]]) -> set[str]:
    """计算叶子章节 id 集合。

    若某章节 id 是其他章节 id 的前缀（即存在形如“父 id.子 id”的章节），
    则该章节是父章节；否则为叶子章节。
    """
    section_ids = [section["id"] for section in sections]
    parents = {
        section_id
        for section_id in section_ids
        for candidate in section_ids
        if candidate != section_id and candidate.startswith(f"{section_id}.")
    }
    return set(section_ids) - parents


def get_path_or_empty(data: dict[str, Any], path: str) -> Any:
    """按点分路径读取值，路径中任何一层不是 dict 时返回空字符串（而非报错）。"""
    value = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part, "")
    return value


def _agent_slot(section: dict[str, Any]) -> str | None:
    """判断章节的 Agent 槽位类型：synthesis 优先于 writing。"""
    if _has_block(section, "synthesis_section"):
        return "synthesis"
    if _has_block(section, "llm_section"):
        return "writing"
    return None


def _has_block(section: dict[str, Any], block_type: str) -> bool:
    """章节 blocks 中是否存在指定类型的块。"""
    return any(block.get("type") == block_type for block in section.get("blocks", []))


def _output_contract(section_id: str) -> dict[str, Any]:
    """返回 Agent 输出的“契约”：允许的块类型、摘要字段，以及不允许控制的内容。"""
    contract = {
        "allowed_blocks": sorted(ALLOWED_AGENT_BLOCKS),
        "summary_fields": SUMMARY_FIELDS,
        "agent_must_not_control": ["title", "order", "table_id", "caption"],
    }
    if section_id == IMPLEMENTATION_SCHEDULE_SECTION_ID:
        contract["implementation_schedule"] = {
            "required": True,
            "table_id": IMPLEMENTATION_SCHEDULE_TABLE_ID,
            "caption": IMPLEMENTATION_SCHEDULE_CAPTION,
            "headers": IMPLEMENTATION_SCHEDULE_HEADERS,
            "stages": IMPLEMENTATION_SCHEDULE_STAGES,
            "row_fields": ["stage", "main_content", "duration_range", "prerequisites"],
            "duration_range_pattern": "X～Y个月",
        }
    return contract


def _validate_package(package: dict[str, Any]) -> None:
    """校验工作包的基本结构与状态。"""
    if not isinstance(package, dict) or package.get("status") != "prepared":
        raise ValueError("work_package must have status=prepared")
    for key in (
        "schema_version",
        "project_name",
        "engineering_facts",
        "template",
        "sections",
        "deterministic_summaries",
    ):
        if key not in package:
            raise ValueError(f"work_package missing {key}")
    if package["engineering_facts"].get("root_keys") != FACT_ROOTS:
        raise ValueError("work_package fact roots do not match the new contract")


def _validate_results_envelope(results: dict[str, Any]) -> None:
    """校验工作结果外包（envelope）的字段完整性。"""
    if not isinstance(results, dict):
        raise ValueError("work_results must be an object")
    required = {
        "schema_version",
        "writing_results",
        "synthesis_results",
    }
    missing = sorted(required - set(results))
    if missing:
        raise ValueError(f"work_results missing required fields: {missing}")
    extra = sorted(set(results) - required)
    if extra:
        raise ValueError(f"work_results has unsupported fields: {extra}")
    if results.get("schema_version") != "1.0":
        raise ValueError("work_results schema_version must be 1.0")


def _result_map(
    results: Any,
    expected_section_ids: set[str],
    label: str,
) -> dict[str, dict[str, Any]]:
    """把结果列表转换为 {section_id: item} 映射，并校验 section_id 合法且无重复。"""
    if not isinstance(results, list):
        raise ValueError(f"{label} must be a list")
    mapped: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("section_id"), str):
            raise ValueError(f"{label} item missing section_id")
        section_id = item["section_id"]
        if section_id not in expected_section_ids:
            raise ValueError(f"{label} contains unknown section_id: {section_id}")
        if item.get("implementation_schedule") is not None and section_id != IMPLEMENTATION_SCHEDULE_SECTION_ID:
            raise ValueError("implementation_schedule is only supported for section 18.2")
        if section_id in mapped:
            raise ValueError(f"{label} contains duplicate section_id: {section_id}")
        mapped[section_id] = item
    return mapped


def _valid_agent_blocks(result: dict[str, Any] | None, section_id: str) -> list[dict[str, Any]] | None:
    """校验并清洗 Agent 产出的章节块。

    返回干净合法的块列表；若结果缺失/非法则返回 None，由调用方回退到 fallback。
    """
    if not isinstance(result, dict):
        return None
    blocks = result.get("blocks")
    summary = result.get("section_summary")
    # 必须有非空 blocks 列表和合法的 section_summary
    if not isinstance(blocks, list) or not blocks or not _valid_summary(summary):
        return None
    clean_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in ALLOWED_AGENT_BLOCKS:
            return None
        if block["type"] == "paragraph":
            # 段落必须有非空文本
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            if _contains_raw_internal_marker(text):
                return None
            clean_blocks.append({"type": "paragraph", "text": text})
        else:
            # 列表必须有非空字符串项
            items = block.get("items")
            if (
                not isinstance(items, list)
                or not items
                or not all(isinstance(item, str) and item.strip() for item in items)
            ):
                return None
            if any(_contains_raw_internal_marker(item) for item in items):
                return None
            clean_blocks.append({"type": block["type"], "items": items})
    if section_id == IMPLEMENTATION_SCHEDULE_SECTION_ID:
        schedule_block = _valid_implementation_schedule(result.get("implementation_schedule"))
        if schedule_block is None:
            return None
        clean_blocks.append(schedule_block)
    elif result.get("implementation_schedule") is not None:
        return None
    return clean_blocks


def _valid_implementation_schedule(schedule: Any) -> dict[str, Any] | None:
    if not isinstance(schedule, dict):
        return None
    rows = schedule.get("rows")
    if not isinstance(rows, list) or len(rows) != len(IMPLEMENTATION_SCHEDULE_STAGES):
        return None
    rendered_rows = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"stage", "main_content", "duration_range", "prerequisites"}:
            return None
        stage = row.get("stage")
        main_content = row.get("main_content")
        duration_range = row.get("duration_range")
        prerequisites = row.get("prerequisites")
        if stage != IMPLEMENTATION_SCHEDULE_STAGES[index] or stage in seen:
            return None
        seen.add(stage)
        if not all(isinstance(value, str) and value.strip() for value in (main_content, duration_range, prerequisites)):
            return None
        if not re.fullmatch(r"\d+(?:\.\d+)?～\d+(?:\.\d+)?个月", duration_range.strip()):
            return None
        rendered_rows.append([stage, main_content.strip(), duration_range.strip(), prerequisites.strip()])
    return {
        "type": "table",
        "table_id": IMPLEMENTATION_SCHEDULE_TABLE_ID,
        "caption": IMPLEMENTATION_SCHEDULE_CAPTION,
        "headers": IMPLEMENTATION_SCHEDULE_HEADERS,
        "rows": rendered_rows,
    }


def _contains_raw_internal_marker(text: str) -> bool:
    normalized = text.lower().replace(" ", "")
    return any(marker.replace(" ", "") in normalized for marker in RAW_INTERNAL_MARKERS)


def _valid_summary(summary: Any) -> bool:
    """校验章节摘要：必须是 dict，且 SUMMARY_FIELDS 各字段均为字符串列表。"""
    if not isinstance(summary, dict):
        return False
    for field in SUMMARY_FIELDS:
        value = summary.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return False
    return True


def _required_string(request: dict[str, Any], key: str) -> str:
    """从请求中取必填非空字符串字段，缺失或为空则报错。"""
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _reject_undeclared_fields(payload: dict[str, Any], allowed: set[str], label: str) -> None:
    """拒绝 payload 中出现未声明的字段，保证输入严格符合约定。"""
    extra = sorted(set(payload) - allowed)
    if extra:
        raise ValueError(f"{label} has unsupported fields: {extra}")


def _uri_to_path(value: str) -> Path:
    """把 file URI 或本地路径统一解析为绝对 Path。

    支持 Windows 风格路径（解析时去掉多余的 leading slash）。
    """
    if value.startswith("file:"):
        parsed = urlparse(value)
        if parsed.scheme != "file":
            raise ValueError("only file URI is supported")
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        # 处理 file:///C:/... 这种 Windows 盘的 URI（去掉多余的 "/"）
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path).resolve()
    return Path(value).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件，并要求其顶层为 object。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """以 UTF-8、缩进格式写出 JSON 文件（保证可读性）。"""
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _diagnostic(level: str, code: str, message: str) -> dict[str, str]:
    """构造一条诊断信息。"""
    return {"level": level, "code": code, "message": message}
