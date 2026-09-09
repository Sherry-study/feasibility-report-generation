"""Core logic for the public ``report_prepare`` Tool."""

from __future__ import annotations

import threading
from typing import Any

from duck.content import Content
from duck.host_client import HostClient
from src.artifact_paths import new_logical_prefix
from src.errors import BusinessValidationError, HostStorageError
from src.report_shared.workflow import (
    MEDIA_TYPE_JSON,
    OperationCancelled,
    build_work_package,
    check_cancel,
    diagnostic,
    load_json_from_host,
    reject_undeclared_fields,
    required_string,
    save_json_to_host,
)


def execute(
    request: dict[str, Any],
    *,
    content: Content,
    host_client: HostClient,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Prepare a report work package and commit it through HostClient."""
    try:
        if not isinstance(request, dict):
            raise BusinessValidationError("request must be an object")
        reject_undeclared_fields(
            request,
            {"engineering_facts_path", "report_context"},
            "report_prepare request",
        )
        engineering_facts_path = required_string(request, "engineering_facts_path")
        report_context = request.get("report_context") or {}
        if not isinstance(report_context, dict):
            raise BusinessValidationError("report_context must be an object")
        reject_undeclared_fields(report_context, {"project_name"}, "report_context")

        content.report_progress(0, 100, "读取工程事实")
        check_cancel(cancel_event)
        facts = load_json_from_host(host_client, engineering_facts_path)
        check_cancel(cancel_event)

        content.report_progress(25, 100, "生成报告工作包")
        diagnostics: list[dict[str, str]] = []
        logical_prefix = new_logical_prefix("report_prepare")
        work_package, context_payloads = build_work_package(
            facts,
            engineering_facts_path,
            report_context,
            logical_prefix,
            diagnostics,
            cancel_event,
        )
        work_package_path = f"{logical_prefix}/work_package.json"

        content.report_progress(70, 100, "提交章节上下文")
        for context_path, context_payload in context_payloads:
            check_cancel(cancel_event)
            save_json_to_host(host_client, context_path, context_payload)

        check_cancel(cancel_event)
        content.report_progress(90, 100, "提交工作包")
        check_cancel(cancel_event)
        save_json_to_host(host_client, work_package_path, work_package)
        content.report_progress(100, 100, "工作包已生成")
        return {
            "status": "prepared",
            "artifact": {
                "path": work_package_path,
                "schema_version": "1.0",
                "media_type": MEDIA_TYPE_JSON,
            },
            "summary": {
                "research_task_count": len(work_package["research_tasks"]),
                "writing_task_count": len(work_package["writing_tasks"]),
                "deterministic_summary_count": len(work_package["deterministic_summaries"]),
                "synthesis_task_count": len(work_package["synthesis_tasks"]),
            },
            "diagnostics": diagnostics,
        }
    except OperationCancelled:
        raise
    except HostStorageError:
        raise
    except BusinessValidationError as exc:
        return {
            "status": "failed",
            "artifact": None,
            "summary": {},
            "diagnostics": [diagnostic("fatal", "REPORT_PREPARE_FAILED", str(exc))],
        }
