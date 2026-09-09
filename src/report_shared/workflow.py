"""Shared deterministic report workflow helpers.

This module intentionally contains no public MCP Tool router.  Public Tool
cores live in ``src.report_prepare`` and ``src.report_finalize``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

import jsonschema

from duck.host_client import HostClient
from src.errors import BusinessValidationError, HostStorageError, HostStorageIntegrityError

from .context_builder import FACT_ROOTS, build_fact_slice, normalize_fact_roots
from .deterministic_builders import build_blocks
from .exporters import export_docx, export_markdown
from .template_loader import load_template


TOOL_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SCHEMA_PATH = TOOL_ROOT / "schemas" / "report_work_package.schema.json"
RESULTS_SCHEMA_PATH = TOOL_ROOT / "schemas" / "report_work_results.schema.json"
MANIFEST_SCHEMA_PATH = TOOL_ROOT / "schemas" / "report_manifest.schema.json"
MEDIA_TYPE_JSON = "application/json"
MEDIA_TYPE_MARKDOWN = "text/markdown; charset=utf-8"
MEDIA_TYPE_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
ALLOWED_AGENT_BLOCKS = {"paragraph", "bullet_list", "numbered_list"}
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
SUMMARY_FIELDS = [
    "conclusions",
    "key_facts",
    "conditions",
    "risks",
    "recommendations",
    "unresolved_items",
]


class OperationCancelled(Exception):
    """Cooperative cancellation signal."""


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled("report operation cancelled")


def diagnostic(level: str, code: str, message: str) -> dict[str, str]:
    return {"level": level, "code": code, "message": message}


def required_string(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BusinessValidationError(f"{key} must be a non-empty string")
    return value.strip()


def reject_undeclared_fields(
    payload: dict[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    extra = sorted(set(payload) - allowed)
    if extra:
        raise BusinessValidationError(f"{label} has unsupported fields: {extra}")


def get_path_or_empty(data: dict[str, Any], path: str) -> Any:
    value = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part, "")
    return value


def load_json_from_host(host_client: HostClient, logical_path: str) -> dict[str, Any]:
    try:
        payload = host_client.get_file(logical_path, kind="bytes")
    except FileNotFoundError as exc:
        raise BusinessValidationError(f"logical path not found: {logical_path}") from exc
    except Exception as exc:  # noqa: BLE001 - storage adapter boundary
        raise HostStorageError(f"HostClient.get_file failed for {logical_path}: {exc}") from exc
    try:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
        raise BusinessValidationError(f"{logical_path} must contain a JSON object")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BusinessValidationError(
            f"{logical_path} must contain valid JSON object: {exc}"
        ) from exc


def save_json_to_host(
    host_client: HostClient,
    logical_path: str,
    payload: dict[str, Any],
) -> None:
    try:
        host_client.save_file(logical_path, payload)
    except Exception as exc:  # noqa: BLE001 - storage adapter boundary
        raise HostStorageError(f"HostClient.save_file failed for {logical_path}: {exc}") from exc


def save_bytes_to_host(
    host_client: HostClient,
    logical_path: str,
    payload: bytes,
) -> None:
    try:
        host_client.save_file(logical_path, payload)
    except Exception as exc:  # noqa: BLE001 - storage adapter boundary
        raise HostStorageError(f"HostClient.save_file failed for {logical_path}: {exc}") from exc


def read_bytes_from_host(host_client: HostClient, logical_path: str) -> bytes:
    try:
        payload = host_client.get_file(logical_path, kind="bytes")
    except Exception as exc:  # noqa: BLE001 - storage adapter boundary
        raise HostStorageError(f"HostClient.get_file failed for {logical_path}: {exc}") from exc
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    raise HostStorageError(f"HostClient.get_file returned unsupported type for {logical_path}")


def validate_with_schema(payload: dict[str, Any], schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


def build_work_package(
    facts: dict[str, Any],
    engineering_facts_path: str,
    report_context: dict[str, Any],
    logical_prefix: str,
    diagnostics: list[dict[str, str]],
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    try:
        facts = normalize_fact_roots(facts)
    except ValueError as exc:
        raise BusinessValidationError(str(exc)) from exc
    check_cancel(cancel_event)
    template = load_template()
    project_name = _project_name(facts, report_context)
    leaf_section_ids = _leaf_section_ids(template["sections"])

    sections = []
    research_task_groups: dict[str, dict[str, Any]] = {}
    writing_tasks = []
    synthesis_tasks = []
    sections_by_id: dict[str, dict[str, Any]] = {}
    context_payloads: list[tuple[str, dict[str, Any]]] = []

    for section in template["sections"]:
        check_cancel(cancel_event)
        section_contract = section.get("contract", {})
        fact_paths = section_contract.get("fact_paths", [])
        fact_slice = build_fact_slice(facts, fact_paths)
        deterministic_blocks = build_blocks(section, facts, diagnostics)
        agent_slot = _agent_slot(section)
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

        if _has_block(section, "llm_section"):
            context_path = (
                f"{logical_prefix}/chapter_contexts/{_safe_section_id(section['id'])}.json"
            )
            context_payloads.append(
                (context_path, {"section_id": section["id"], "fact_slice": fact_slice})
            )
            research_task_id = _register_research_task(
                research_task_groups,
                section["id"],
                section_contract,
            )
            writing_tasks.append(
                {
                    "task_id": f"writing:{section['id']}",
                    "section_id": section["id"],
                    "section_title": section["title"],
                    "contract": section_contract,
                    "fact_paths": fact_paths,
                    "context_path": context_path,
                    "research_task_id": research_task_id,
                    "output_contract": _output_contract(section["id"]),
                }
            )
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
            "path": engineering_facts_path,
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
    validate_with_schema(work_package, PACKAGE_SCHEMA_PATH)
    return work_package, context_payloads


def build_report_result(
    package: dict[str, Any],
    results: dict[str, Any],
    results_provided: bool,
    host_client: HostClient,
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, Any], str, list[dict[str, str]], dict[str, int]]:
    validate_work_package(package)
    validate_work_results(results)

    diagnostics: list[dict[str, str]] = []
    if not results_provided:
        diagnostics.append(
            diagnostic(
                "warning",
                "WORK_RESULTS_NOT_PROVIDED",
                "未提供 Agent 工作结果（work_results_path 未传入），本次报告为不完整初稿，Agent 章节均使用模板 fallback 兜底。",
            )
        )
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
        check_cancel(cancel_event)
        blocks = list(section.get("deterministic_blocks", []))
        slot = section.get("agent_slot")
        if slot:
            result = (
                synthesis_results.get(section["id"])
                if slot == "synthesis"
                else writing_results.get(section["id"])
            )
            agent_blocks = _valid_agent_blocks(result, section["id"])
            if agent_blocks is None:
                fallback_count += 1
                diagnostics.append(
                    diagnostic(
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
    report = {
        "project_name": package["project_name"],
        "cover": _build_cover(package, host_client),
        "properties": {
            "title": f"{package['project_name']}可行性研究报告（初稿）",
            "subject": "流程工业改造项目可行性研究报告",
        },
        "sections": report_sections,
    }
    markdown_content = render_export_to_host_payload(report)
    summary = {
        "section_count": len(report_sections),
        "fallback_section_count": fallback_count,
    }
    return report, markdown_content, diagnostics, summary


def render_export_to_host_payload(report: dict[str, Any]) -> str:
    with tempfile.TemporaryDirectory() as temp_dir:
        markdown_path = Path(temp_dir) / "report.md"
        export_markdown(report, markdown_path)
        if not markdown_path.is_file() or not markdown_path.read_text(encoding="utf-8").strip():
            raise ValueError("exported markdown is empty or missing")
        return markdown_path.read_text(encoding="utf-8")


def export_report_files(report: dict[str, Any]) -> tuple[bytes, bytes]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        markdown_path = temp_root / "可行性研究报告_初稿.md"
        docx_path = temp_root / "可行性研究报告_初稿.docx"
        export_markdown(report, markdown_path)
        export_docx(report, docx_path)
        _validate_export_group(markdown_path, docx_path)
        return (
            markdown_path.read_text(encoding="utf-8").encode("utf-8"),
            docx_path.read_bytes(),
        )


def file_stat(payload: bytes) -> dict[str, Any]:
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def verify_host_bytes(
    host_client: HostClient,
    logical_path: str,
    expected_payload: bytes,
) -> None:
    actual = read_bytes_from_host(host_client, logical_path)
    expected = file_stat(expected_payload)
    observed = file_stat(actual)
    if observed != expected:
        raise HostStorageIntegrityError(
            f"HostClient readback mismatch for {logical_path}: expected {expected}, observed {observed}"
        )


def build_manifest(
    *,
    markdown_path: str,
    markdown_payload: bytes,
    docx_path: str,
    docx_payload: bytes,
    summary: dict[str, int],
) -> dict[str, Any]:
    manifest = {
        "schema_version": "1.0",
        "created_at": _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z"),
        "markdown": {
            "path": markdown_path,
            "media_type": MEDIA_TYPE_MARKDOWN,
            **file_stat(markdown_payload),
        },
        "docx": {
            "path": docx_path,
            "media_type": MEDIA_TYPE_DOCX,
            **file_stat(docx_payload),
        },
        "summary": summary,
    }
    validate_with_schema(manifest, MANIFEST_SCHEMA_PATH)
    return manifest


def empty_work_results() -> dict[str, Any]:
    return {"schema_version": "1.0", "writing_results": [], "synthesis_results": []}


def validate_work_package(package: dict[str, Any]) -> None:
    try:
        validate_with_schema(package, PACKAGE_SCHEMA_PATH)
    except jsonschema.ValidationError as exc:
        raise BusinessValidationError(f"work_package schema invalid: {exc.message}") from exc
    if package.get("status") != "prepared":
        raise BusinessValidationError("work_package must have status=prepared")
    if (package.get("engineering_facts") or {}).get("root_keys") != FACT_ROOTS:
        raise BusinessValidationError("work_package fact roots do not match the contract")


def validate_work_results(results: dict[str, Any]) -> None:
    # Preserve the established fallback contract: malformed per-section blocks
    # are rejected section-by-section by ``_valid_agent_blocks`` and replaced by
    # template fallback. Envelope/identity violations remain request failures.
    _validate_results_envelope(results)


def _build_cover(package: dict[str, Any], host_client: HostClient) -> dict[str, Any]:
    company_name = ""
    facts_path = (package.get("engineering_facts") or {}).get("path")
    if facts_path:
        try:
            facts = load_json_from_host(host_client, facts_path)
            company_name = str(get_path_or_empty(facts, "basic_info.construction_unit") or "").strip()
        except (FileNotFoundError, ValueError):
            company_name = ""
    return {
        "company_name": company_name,
        "project_name": package.get("project_name", ""),
        "report_title": "可行性研究报告（初稿）",
        "project_code": "xxxxxx",
    }


def _project_name(facts: dict[str, Any], report_context: dict[str, Any]) -> str:
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
    group = str(section_contract.get("research_group") or "").strip()
    objectives = section_contract.get("research_objectives", [])
    if not group or not objectives:
        return None
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


def _build_deterministic_summaries(
    sections_by_id: dict[str, dict[str, Any]],
    writing_tasks: list[dict[str, Any]],
    synthesis_tasks: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    writing_section_ids = {task["section_id"] for task in writing_tasks}
    synthesis_section_ids = {task["section_id"] for task in synthesis_tasks}
    source_section_ids: set[str] = set()
    for task in synthesis_tasks:
        source_section_ids.update(task["context"].get("source_section_ids", []))

    summaries: dict[str, dict[str, list[str]]] = {}
    for section_id, section in sections_by_id.items():
        if section_id not in source_section_ids:
            continue
        if section_id in writing_section_ids or section_id in synthesis_section_ids:
            continue
        summaries[section_id] = _deterministic_section_summary(section)
    return summaries


def _deterministic_section_summary(section: dict[str, Any]) -> dict[str, list[str]]:
    summary = {field: [] for field in SUMMARY_FIELDS}
    fallback = str(section.get("fallback", "")).strip()
    for block in section.get("deterministic_blocks", []):
        block_type = block.get("type")
        if block_type == "paragraph":
            text = str(block.get("text", "")).strip()
            if text:
                target = "unresolved_items" if text == fallback else "key_facts"
                summary[target].append(text)
        elif block_type in {"bullet_list", "numbered_list"}:
            summary["key_facts"].extend(
                str(item).strip()
                for item in block.get("items", [])[:12]
                if str(item).strip()
            )
        elif block_type == "table":
            heading = " ".join(
                str(value).strip()
                for value in (block.get("table_id"), block.get("caption"))
                if str(value or "").strip()
            )
            if heading:
                summary["key_facts"].append(heading)
            summary["key_facts"].extend(_table_summary_rows(block))
    if not any(summary.values()) and fallback:
        summary["unresolved_items"].append(fallback)
    return summary


def _table_summary_rows(block: dict[str, Any]) -> list[str]:
    headers = [str(item).strip() for item in block.get("headers", []) if str(item).strip()]
    rows = []
    for row in block.get("rows", [])[:8]:
        cells = [str(item).strip() for item in row if str(item).strip()]
        if not cells:
            continue
        if headers and len(headers) == len(cells):
            rows.append("；".join(f"{header}：{cell}" for header, cell in zip(headers, cells, strict=True)))
        else:
            rows.append("；".join(cells))
    return rows


def _safe_section_id(section_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", section_id).strip("_")
    return safe or "section"


def _deterministic_created_at(facts: dict[str, Any]) -> str:
    generated_at = get_path_or_empty(facts, "meta.generated_at")
    if isinstance(generated_at, str) and generated_at.strip():
        return generated_at.strip()
    return "1970-01-01T00:00:00+00:00"


def _leaf_section_ids(sections: list[dict[str, Any]]) -> set[str]:
    section_ids = [section["id"] for section in sections]
    parents = {
        section_id
        for section_id in section_ids
        for candidate in section_ids
        if candidate != section_id and candidate.startswith(f"{section_id}.")
    }
    return set(section_ids) - parents


def _agent_slot(section: dict[str, Any]) -> str | None:
    if _has_block(section, "synthesis_section"):
        return "synthesis"
    if _has_block(section, "llm_section"):
        return "writing"
    return None


def _has_block(section: dict[str, Any], block_type: str) -> bool:
    return any(block.get("type") == block_type for block in section.get("blocks", []))


def _output_contract(section_id: str) -> dict[str, Any]:
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


def _validate_results_envelope(results: dict[str, Any]) -> None:
    if not isinstance(results, dict):
        raise BusinessValidationError("work_results must be an object")
    required = {"schema_version", "writing_results", "synthesis_results"}
    missing = sorted(required - set(results))
    if missing:
        raise BusinessValidationError(f"work_results missing required fields: {missing}")
    extra = sorted(set(results) - required)
    if extra:
        raise BusinessValidationError(f"work_results has unsupported fields: {extra}")
    if results.get("schema_version") != "1.0":
        raise BusinessValidationError("work_results schema_version must be 1.0")


def _result_map(
    results: Any,
    expected_section_ids: set[str],
    label: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(results, list):
        raise BusinessValidationError(f"{label} must be a list")
    mapped: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("section_id"), str):
            raise BusinessValidationError(f"{label} item missing section_id")
        section_id = item["section_id"]
        if section_id not in expected_section_ids:
            raise BusinessValidationError(f"{label} contains unknown section_id: {section_id}")
        if item.get("implementation_schedule") is not None and section_id != IMPLEMENTATION_SCHEDULE_SECTION_ID:
            raise BusinessValidationError("implementation_schedule is only supported for section 18.2")
        if section_id in mapped:
            raise BusinessValidationError(f"{label} contains duplicate section_id: {section_id}")
        mapped[section_id] = item
    return mapped


def _valid_agent_blocks(result: dict[str, Any] | None, section_id: str) -> list[dict[str, Any]] | None:
    if not isinstance(result, dict):
        return None
    blocks = result.get("blocks")
    summary = result.get("section_summary")
    if not isinstance(blocks, list) or not blocks or not _valid_summary(summary):
        return None
    clean_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in ALLOWED_AGENT_BLOCKS:
            return None
        if block["type"] == "paragraph":
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            if _contains_raw_internal_marker(text):
                return None
            clean_blocks.append({"type": "paragraph", "text": text})
        else:
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
    if not isinstance(summary, dict):
        return False
    for field in SUMMARY_FIELDS:
        value = summary.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return False
    return True


def _validate_export_group(markdown_path: Path, docx_path: Path) -> None:
    if not markdown_path.is_file() or not markdown_path.read_text(encoding="utf-8").strip():
        raise ValueError("exported markdown is empty or missing")
    if not docx_path.is_file() or docx_path.stat().st_size == 0:
        raise ValueError("exported docx is empty or missing")
    if not zipfile.is_zipfile(docx_path):
        raise ValueError("exported docx is not a valid OOXML package")
