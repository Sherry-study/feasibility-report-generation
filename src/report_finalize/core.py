"""Core logic for the public ``report_finalize`` Tool."""

from __future__ import annotations

import threading
from typing import Any

from duck.content import Content
from duck.host_client import HostClient
from src.artifact_paths import new_logical_prefix
from src.errors import BusinessValidationError, HostStorageError
from src.report_shared.workflow import (
    MEDIA_TYPE_DOCX,
    MEDIA_TYPE_MARKDOWN,
    OperationCancelled,
    build_manifest,
    build_report_result,
    check_cancel,
    diagnostic,
    empty_work_results,
    export_report_files,
    load_json_from_host,
    reject_undeclared_fields,
    required_string,
    save_bytes_to_host,
    save_json_to_host,
    verify_host_bytes,
)


def execute(
    request: dict[str, Any],
    *,
    content: Content,
    host_client: HostClient,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Finalize a prepared work package into Markdown/DOCX plus manifest."""
    try:
        if not isinstance(request, dict):
            raise BusinessValidationError("request must be an object")
        reject_undeclared_fields(
            request,
            {"work_package_path", "work_results_path"},
            "report_finalize request",
        )
        work_package_path = required_string(request, "work_package_path")
        raw_results_path = request.get("work_results_path")
        if raw_results_path is not None and (
            not isinstance(raw_results_path, str) or not raw_results_path.strip()
        ):
            raise BusinessValidationError(
                "work_results_path must be a non-empty string when provided"
            )
        work_results_path = raw_results_path.strip() if isinstance(raw_results_path, str) else None

        content.report_progress(0, 100, "读取工作包")
        check_cancel(cancel_event)
        package = load_json_from_host(host_client, work_package_path)
        check_cancel(cancel_event)

        content.report_progress(20, 100, "读取工作结果")
        if work_results_path:
            results = load_json_from_host(host_client, work_results_path)
            results_provided = True
        else:
            results = empty_work_results()
            results_provided = False
        check_cancel(cancel_event)

        content.report_progress(45, 100, "组装报告内容")
        report, markdown_content, diagnostics, summary = build_report_result(
            package,
            results,
            results_provided,
            host_client,
            cancel_event,
        )
        check_cancel(cancel_event)

        content.report_progress(65, 100, "导出 Markdown 与 DOCX")
        markdown_payload, docx_payload = export_report_files(report)
        check_cancel(cancel_event)
        logical_prefix = new_logical_prefix("report_finalize")
        markdown_path = f"{logical_prefix}/可行性研究报告_初稿.md"
        docx_path = f"{logical_prefix}/可行性研究报告_初稿.docx"
        manifest_path = f"{logical_prefix}/report_manifest.json"

        content.report_progress(80, 100, "保存并回读校验报告产物")
        check_cancel(cancel_event)
        save_bytes_to_host(host_client, markdown_path, markdown_payload)
        check_cancel(cancel_event)
        save_bytes_to_host(host_client, docx_path, docx_payload)
        check_cancel(cancel_event)
        verify_host_bytes(host_client, markdown_path, markdown_payload)
        check_cancel(cancel_event)
        verify_host_bytes(host_client, docx_path, docx_payload)

        check_cancel(cancel_event)
        content.report_progress(95, 100, "保存报告清单")
        check_cancel(cancel_event)
        manifest = build_manifest(
            markdown_path=markdown_path,
            markdown_payload=markdown_payload,
            docx_path=docx_path,
            docx_payload=docx_payload,
            summary=summary,
        )
        save_json_to_host(host_client, manifest_path, manifest)
        content.report_progress(100, 100, "报告已生成")
        return {
            "status": "completed",
            "artifacts": {
                "docx_path": docx_path,
                "docx_media_type": MEDIA_TYPE_DOCX,
                "markdown_path": markdown_path,
                "markdown_media_type": MEDIA_TYPE_MARKDOWN,
            },
            "manifest_path": manifest_path,
            "markdown_content": markdown_content,
            "summary": summary,
            "diagnostics": diagnostics,
        }
    except OperationCancelled:
        raise
    except HostStorageError:
        raise
    except BusinessValidationError as exc:
        return {
            "status": "failed",
            "artifacts": None,
            "manifest_path": None,
            "markdown_content": "",
            "summary": {},
            "diagnostics": [diagnostic("fatal", "REPORT_FINALIZE_FAILED", str(exc))],
        }
