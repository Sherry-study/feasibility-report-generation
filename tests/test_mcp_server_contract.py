"""MCP server public contract tests."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from mcp_server.server import (
    EngineeringFactsError,
    EngineeringFactsFailure,
    EngineeringFactsInput,
    EngineeringFactsSuccess,
    ReportFinalizeError,
    ReportFinalizeFailure,
    ReportFinalizeInput,
    ReportFinalizeSuccess,
    ReportPrepareError,
    ReportPrepareFailure,
    ReportPrepareInput,
    ReportPrepareSuccess,
    SourceLocationParam,
    _run_with_cancellation,
    engineering_facts,
    main,
    mcp,
    report_finalize,
    report_prepare,
    setup_logging,
)
from mcp_server.duck_implement import _send_progress_with_data
from mcp_server.duck_implement.host_client import MCPHostClient
from src.errors import HostStorageError
from src.report_prepare import OperationCancelled
from src.report_prepare import execute as execute_prepare
from tests.fakes import FakeContent


def _run(awaitable):
    return asyncio.run(awaitable)


def _unwrap_optional(param_schema: dict) -> dict:
    """解包可选参数的 anyOf: [schema, null] 形态，返回真实模型 schema。"""
    if "anyOf" in param_schema and "properties" not in param_schema:
        return param_schema["anyOf"][0]
    return param_schema


def _tool(name: str):
    # 使用 tools/list 的真实对外契约（schema 已内联展开）
    for tool in _run(mcp.list_tools()):
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name} not found")


def _platform_ctx(capability: str):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(
                model_extra={
                    "io.industrial.platform": {
                        "capability": capability,
                    }
                }
            )
        )
    )


class FakeCtx:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def info(self, message: str) -> None:
        self.messages.append(message)


class FakeProgressMeta:
    progressToken = "progress-token-1"


class FakeRequestContext:
    meta = FakeProgressMeta()


class FakeProgressSession:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    async def send_notification(
        self,
        notification: object,
        related_request_id: object = None,
    ) -> None:
        self.calls.append((notification, related_request_id))


class FakeProgressCtx:
    request_context = FakeRequestContext()
    request_id = "request-1"

    def __init__(self) -> None:
        self.session = FakeProgressSession()
        self.fallback_calls: list[tuple[float, float | None, str | None]] = []

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.fallback_calls.append((progress, total, message))


def _engineering_facts_success_envelope() -> dict:
    return {
        "status": "success",
        "data": {
            "business_status": "completed",
            "artifact": {
                "path": "runs/engineering_facts/engineering_facts.json",
                "media_type": "application/json",
                "schema_version": "2.0",
            },
            "summary": {
                "source_count": 3,
                "equipment_count": 5,
                "candidate_count": 2,
                "user_candidate_count": 1,
                "adopted_scheme_count": 1,
                "selected_names": ["方案A"],
                "optimized": True,
                "derived_fact_count": 10,
                "annualized_stream_quantity_count": 4,
                "annualized_material_consumption_count": 2,
            },
            "error": None,
        },
        "warnings": [],
    }


def _report_prepare_success_envelope() -> dict:
    return {
        "status": "success",
        "data": {
            "business_status": "prepared",
            "artifact": {
                "path": "runs/report_prepare/work_package.json",
                "media_type": "application/json",
                "schema_version": "1.0",
            },
            "summary": {
                "research_task_count": 3,
                "writing_task_count": 5,
                "deterministic_summary_count": 7,
                "synthesis_task_count": 2,
            },
            "error": None,
        },
        "warnings": [],
    }


def _report_finalize_success_envelope() -> dict:
    return {
        "status": "success",
        "data": {
            "business_status": "completed",
            "artifacts": {
                "docx_path": "runs/report_finalize/report.docx",
                "docx_media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "markdown_path": "runs/report_finalize/report.md",
                "markdown_media_type": "text/markdown; charset=utf-8",
            },
            "manifest_path": "runs/report_finalize/report_manifest.json",
            "summary": {
                "section_count": 26,
                "fallback_section_count": 0,
            },
            "error": None,
        },
        "warnings": [],
    }


class MCPServerContractTests(unittest.TestCase):
    def test_setup_logging_adds_east_8_timestamp_formatter(self) -> None:
        root = logging.getLogger()
        old_handlers = list(root.handlers)
        old_level = root.level
        old_uvicorn_state = {
            name: (
                list(logging.getLogger(name).handlers),
                logging.getLogger(name).propagate,
            )
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        }
        try:
            setup_logging()
            stream = io.StringIO()
            root.handlers[0].stream = stream

            logging.getLogger("uvicorn.access").info(
                '127.0.0.1:65150 - "GET /mcp HTTP/1.1" 406'
            )

            line = stream.getvalue().strip()
            self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - INFO - ")
            self.assertIn('"GET /mcp HTTP/1.1" 406', line)
            formatter = root.handlers[0].formatter
            record = logging.LogRecord("test", logging.INFO, "", 0, "epoch", (), None)
            record.created = 0
            self.assertTrue(formatter)
            self.assertEqual(
                formatter.formatTime(record, "%Y-%m-%d %H:%M:%S"),
                "1970-01-01 08:00:00",
            )
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
                self.assertTrue(logging.getLogger(name).propagate)
        finally:
            root.handlers.clear()
            root.handlers.extend(old_handlers)
            root.setLevel(old_level)
            for name, (handlers, propagate) in old_uvicorn_state.items():
                uvicorn_logger = logging.getLogger(name)
                uvicorn_logger.handlers.clear()
                uvicorn_logger.handlers.extend(handlers)
                uvicorn_logger.propagate = propagate

    def test_main_disables_uvicorn_log_config(self) -> None:
        with patch("mcp_server.server.mcp.run") as run:
            main()

        self.assertEqual(run.call_args.kwargs["uvicorn_config"], {"log_config": None})

    def test_platform_host_client_reads_and_saves_workspace_files(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, content=b'{"ok": true}')
            return httpx.Response(201, text="created")

        client = MCPHostClient(
            base_url="http://host",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            ctx=_platform_ctx("cap-token"),
        )
        logical_path = "runs/engineering_facts/run-1/engineering_facts.json"
        client.save_file(logical_path, {"ok": True})
        payload = client.get_file(logical_path, kind="bytes")

        self.assertEqual(payload, b'{"ok": true}')
        self.assertEqual(len(requests), 2)
        encoded_path = base64.urlsafe_b64encode(logical_path.encode("utf-8"))
        encoded_path = encoded_path.rstrip(b"=").decode("ascii")
        # 写操作走 /files/ 端点，不带平台专用 Content-Type（平台工作区 API 不支持创建新文件）
        save_req, get_req = requests
        self.assertEqual(save_req.method, "POST")
        self.assertEqual(
            str(save_req.url),
            "http://host/files/runs/engineering_facts/run-1/engineering_facts.json",
        )
        self.assertNotIn("Authorization", save_req.headers)
        self.assertEqual(save_req.headers["Content-Type"], "application/json")
        # 读操作仍走平台工作区 API
        self.assertEqual(get_req.method, "GET")
        self.assertEqual(
            str(get_req.url),
            f"http://host/internal/platform/workspace/files/{encoded_path}",
        )
        self.assertEqual(get_req.headers["Authorization"], "Bearer cap-token")

    def test_progress_with_data_keeps_related_request_id(self) -> None:
        ctx = FakeProgressCtx()

        _run(
            _send_progress_with_data(
                ctx, 100, 100, "工程事实已完成", {"final_result": {"ok": True}}
            )
        )

        self.assertEqual(len(ctx.session.calls), 1)
        notification, related_request_id = ctx.session.calls[0]
        self.assertEqual(related_request_id, "request-1")
        progress = notification.params
        self.assertEqual(progress.progressToken, "progress-token-1")
        self.assertEqual(progress.progress, 100)
        self.assertEqual(progress.total, 100)
        self.assertEqual(progress.message, "工程事实已完成")
        self.assertEqual(progress.model_extra["uiEvent"], {"final_result": {"ok": True}})
        # 线上序列化须保留 uiEvent（mcp 2.x 默认会丢弃多余字段）
        wire = notification.model_dump(by_alias=True, mode="json", exclude_none=True)
        self.assertEqual(wire["params"]["progressToken"], "progress-token-1")
        self.assertEqual(wire["params"]["uiEvent"], {"final_result": {"ok": True}})
        self.assertEqual(ctx.fallback_calls, [])

    def test_cancellation_bridge_returns_normal_result(self) -> None:
        event = threading.Event()
        result = _run(_run_with_cancellation(lambda: {"status": "ok"}, event, "normal", "internal"))
        self.assertEqual(result, {"status": "ok"})
        self.assertFalse(event.is_set())

    def test_core_operation_cancelled_becomes_cancelled_error(self) -> None:
        def cancel() -> dict:
            raise OperationCancelled("cancelled")
        with self.assertRaises(asyncio.CancelledError):
            _run(_run_with_cancellation(cancel, threading.Event(), "cancel", "internal"))

    def test_outer_cancel_sets_event_and_waits_for_worker_exit(self) -> None:
        async def scenario() -> None:
            cancel_event = threading.Event()
            worker_exited = threading.Event()

            def cooperate() -> dict:
                while not cancel_event.is_set():
                    time.sleep(0.001)
                worker_exited.set()
                raise OperationCancelled("cooperative exit")

            task = asyncio.create_task(_run_with_cancellation(cooperate, cancel_event, "outer", "internal"))
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cancel_event.is_set())
            self.assertTrue(worker_exited.is_set())

        _run(scenario())

    def test_engineering_facts_pushes_final_ui_result_via_progress(self) -> None:
        core_result = {
            "status": "completed",
            "artifact": {
                "path": "runs/engineering_facts/engineering_facts.json",
                "media_type": "application/json",
                "schema_version": "2.0",
            },
            "engineering_facts": {"unit": {"name": "测试装置"}},
            "summary": _engineering_facts_success_envelope()["data"]["summary"],
            "diagnostics": [],
        }

        async def scenario() -> None:
            progress = AsyncMock()
            with (
                patch("mcp_server.server.make_host_client", return_value=object()),
                patch("mcp_server.server._run_with_cancellation", new=AsyncMock(return_value=core_result)),
                patch("mcp_server.server._send_progress_with_data", new=progress),
            ):
                tool_result = await engineering_facts(
                    FakeCtx(),
                    EngineeringFactsInput(provider="local_directory", root="inputs"),
                )
            self.assertEqual(tool_result.structured_content, _engineering_facts_success_envelope())
            self.assertIsNone(tool_result.meta)
            args = progress.await_args.args
            self.assertEqual(args[3], "工程事实已完成")
            ui_event = args[4]
            final_result = ui_event["final_result"]
            self.assertEqual(final_result, _engineering_facts_success_envelope())
            EngineeringFactsSuccess.model_validate(final_result)
            self.assertEqual(ui_event["engineering_facts"], {"unit": {"name": "测试装置"}})

        _run(scenario())

    def test_report_prepare_uses_host_client_and_returns_envelope(self) -> None:
        core_result = {
            "status": "prepared",
            "artifact": {
                "path": "runs/report_prepare/work_package.json",
                "media_type": "application/json",
                "schema_version": "1.0",
            },
            "summary": _report_prepare_success_envelope()["data"]["summary"],
            "diagnostics": [],
        }

        async def scenario() -> None:
            host_client = object()
            make_client = patch("mcp_server.server.make_host_client", return_value=host_client)
            run_core = patch(
                "mcp_server.server._run_with_cancellation",
                new=AsyncMock(return_value=core_result),
            )
            with make_client as make_host_client, run_core as run_with_cancellation:
                tool_result = await report_prepare(
                    FakeCtx(),
                    ReportPrepareInput(
                        engineering_facts_path="runs/engineering_facts/engineering_facts.json"
                    ),
                )
            self.assertEqual(tool_result.structured_content, _report_prepare_success_envelope())
            self.assertIsNone(tool_result.meta)
            make_host_client.assert_called_once()
            run_args = run_with_cancellation.await_args.args
            self.assertEqual(run_args[2], "report_prepare")

        _run(scenario())

    def test_report_finalize_pushes_markdown_ui_result_via_progress(self) -> None:
        schema = _tool("report_finalize").output_schema
        core_result = {
            "status": "completed",
            "artifacts": {
                "docx_path": "runs/report_finalize/report.docx",
                "docx_media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "markdown_path": "runs/report_finalize/report.md",
                "markdown_media_type": "text/markdown; charset=utf-8",
            },
            "manifest_path": "runs/report_finalize/report_manifest.json",
            "markdown_content": "# 可行性研究报告\n\n正文",
            "summary": _report_finalize_success_envelope()["data"]["summary"],
            "diagnostics": [],
        }

        async def scenario() -> None:
            progress = AsyncMock()
            with (
                patch("mcp_server.server.make_host_client", return_value=object()),
                patch("mcp_server.server._run_with_cancellation", new=AsyncMock(return_value=core_result)),
                patch("mcp_server.server._send_progress_with_data", new=progress),
            ):
                tool_result = await report_finalize(
                    FakeCtx(),
                    ReportFinalizeInput(work_package_path="runs/report_prepare/work_package.json"),
                )
            self.assertEqual(tool_result.structured_content, _report_finalize_success_envelope())
            self.assertIsNone(tool_result.meta)
            args = progress.await_args.args
            self.assertEqual(args[3], "报告已生成")
            ui_event = args[4]
            final_result = ui_event["final_result"]
            self.assertEqual(final_result, _report_finalize_success_envelope())
            ReportFinalizeSuccess.model_validate(final_result)
            self.assertEqual(ui_event["markdown_content"], "# 可行性研究报告\n\n正文")

        _run(scenario())

    def test_engineering_facts_error_progress_final_result_is_error_envelope(self) -> None:
        core_result = {
            "status": "error",
            "diagnostics": [
                {
                    "level": "fatal",
                    "code": "ENGINEERING_FACTS_ARTIFACT_SAVE_FAILED",
                    "message": "HostClient failed to save engineering facts artifact",
                    "retryable": False,
                }
            ],
        }

        async def scenario() -> None:
            progress = AsyncMock()
            with (
                patch("mcp_server.server.make_host_client", return_value=object()),
                patch("mcp_server.server._run_with_cancellation", new=AsyncMock(return_value=core_result)),
                patch("mcp_server.server._send_progress_with_data", new=progress),
            ):
                tool_result = await engineering_facts(
                    FakeCtx(),
                    EngineeringFactsInput(provider="local_directory", root="inputs"),
                )
            EngineeringFactsError.model_validate(tool_result.structured_content)
            ui_event = progress.await_args.args[4]
            EngineeringFactsError.model_validate(ui_event["final_result"])
            self.assertNotIn("business_status", ui_event["final_result"])
            self.assertIsNone(ui_event["engineering_facts"])

        _run(scenario())

    def test_engineering_facts_accepts_legacy_top_level_source_location(self) -> None:
        """中转层旧声明形态：顶层 source_location + construction_unit。"""
        core_result = {
            "status": "completed",
            "artifact": {
                "path": "runs/engineering_facts/engineering_facts.json",
                "media_type": "application/json",
                "schema_version": "2.0",
            },
            "summary": _engineering_facts_success_envelope()["data"]["summary"],
            "diagnostics": [],
        }

        async def scenario() -> None:
            ctx = FakeCtx()
            with (
                patch("mcp_server.server.make_host_client", return_value=object()),
                patch("mcp_server.server._run_with_cancellation", new=AsyncMock(return_value=core_result)),
                patch("mcp_server.server._send_progress_with_data", new=AsyncMock()),
            ):
                tool_result = await engineering_facts(
                    ctx,
                    source_location=SourceLocationParam(
                        provider="local_directory", location="inputs"
                    ),
                    construction_unit="测试建设单位",
                )
            self.assertEqual(
                tool_result.structured_content, _engineering_facts_success_envelope()
            )
            # 归一化后进入核心的 provider/root 与顶层 source_location 一致
            self.assertIn(
                "engineering_facts start: local_directory:inputs", ctx.messages
            )

        _run(scenario())

    def test_source_location_param_unwraps_nested_input(self) -> None:
        """Agent 双侧妥协产生的 {input: {...}} 混合嵌套也能归一化。"""
        param = SourceLocationParam.model_validate(
            {"input": {"provider": "local_directory", "location": "."}}
        )
        self.assertEqual(param.provider, "local_directory")
        self.assertEqual(param.location, ".")

    def test_report_prepare_accepts_legacy_top_level_params(self) -> None:
        core_result = {
            "status": "prepared",
            "artifact": {
                "path": "runs/report_prepare/work_package.json",
                "media_type": "application/json",
                "schema_version": "1.0",
            },
            "summary": _report_prepare_success_envelope()["data"]["summary"],
            "diagnostics": [],
        }

        async def scenario() -> None:
            with (
                patch("mcp_server.server.make_host_client", return_value=object()),
                patch("mcp_server.server._run_with_cancellation", new=AsyncMock(return_value=core_result)),
            ):
                tool_result = await report_prepare(
                    FakeCtx(),
                    engineering_facts_path="runs/engineering_facts/engineering_facts.json",
                    project_name="测试项目",
                )
            self.assertEqual(
                tool_result.structured_content, _report_prepare_success_envelope()
            )

        _run(scenario())

    def test_report_finalize_accepts_legacy_top_level_params(self) -> None:
        core_result = {
            "status": "completed",
            "artifacts": {
                "docx_path": "runs/report_finalize/report.docx",
                "docx_media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "markdown_path": "runs/report_finalize/report.md",
                "markdown_media_type": "text/markdown; charset=utf-8",
            },
            "manifest_path": "runs/report_finalize/report_manifest.json",
            "markdown_content": "# 报告",
            "summary": _report_finalize_success_envelope()["data"]["summary"],
            "diagnostics": [],
        }

        async def scenario() -> None:
            with (
                patch("mcp_server.server.make_host_client", return_value=object()),
                patch("mcp_server.server._run_with_cancellation", new=AsyncMock(return_value=core_result)),
                patch("mcp_server.server._send_progress_with_data", new=AsyncMock()),
            ):
                tool_result = await report_finalize(
                    FakeCtx(),
                    work_package_path="runs/report_prepare/work_package.json",
                )
            self.assertEqual(
                tool_result.structured_content, _report_finalize_success_envelope()
            )

        _run(scenario())

    def test_tools_raise_clear_error_when_no_input_provided(self) -> None:
        async def scenario() -> None:
            for tool in (engineering_facts, report_prepare, report_finalize):
                with self.assertRaises(ValueError):
                    await tool(FakeCtx())

        _run(scenario())

    def test_unexpected_internal_error_is_translated_to_error_result(self) -> None:
        def fail() -> dict:
            raise RuntimeError("algorithm bug")

        with self.assertLogs("mcp_server.server", level="ERROR"):
            result = _run(_run_with_cancellation(fail, threading.Event(), "bug", "internal"))
        # relay 侧只认 status=failed，status=error 会导致 -32602 掩盖真实错误
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["diagnostics"][0]["code"], "INTERNAL_ERROR")
        self.assertFalse(result["diagnostics"][0]["retryable"])

    def test_mcp_host_bad_json_is_business_failed_but_5xx_is_storage_error(self) -> None:
        def bad_json_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{bad json")

        bad_client = MCPHostClient(
            base_url="http://host",
            client=httpx.Client(transport=httpx.MockTransport(bad_json_handler)),
        )
        result = execute_prepare(
            {"engineering_facts_path": "inputs/facts.json"},
            content=FakeContent(),
            host_client=bad_client,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("valid JSON object", result["diagnostics"][0]["message"])

        def unavailable_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "unavailable"})

        unavailable = MCPHostClient(
            base_url="http://host",
            client=httpx.Client(transport=httpx.MockTransport(unavailable_handler)),
        )
        with self.assertRaises(HostStorageError):
            execute_prepare(
                {"engineering_facts_path": "inputs/facts.json"},
                content=FakeContent(),
                host_client=unavailable,
            )

    def test_internal_storage_error_is_translated_to_error_result(self) -> None:
        def fail() -> dict:
            raise HostStorageError("simulated storage outage")

        with self.assertLogs("mcp_server.server", level="ERROR"):
            result = _run(
                _run_with_cancellation(
                    fail,
                    threading.Event(),
                    "storage-test",
                    "内部存储错误",
                )
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["diagnostics"][0]["code"], "HOST_STORAGE_ERROR")
        self.assertIn("内部存储错误", result["diagnostics"][0]["message"])
        self.assertIn("simulated storage outage", result["diagnostics"][0]["message"])
        self.assertFalse(result["diagnostics"][0]["retryable"])

    def test_public_tools_are_limited_to_three_tool_contract(self) -> None:
        tools = _run(mcp.list_tools())
        self.assertEqual(
            [tool.name for tool in tools],
            ["engineering_facts", "report_prepare", "report_finalize"],
        )

    def test_no_file_read_tools_are_exposed(self) -> None:
        tools = _run(mcp.list_tools())
        for tool in tools:
            self.assertNotIn("read", tool.name.lower())
        names = {tool.name for tool in tools}
        self.assertNotIn("read_artifact", names)
        self.assertNotIn("read_file", names)

    def test_tools_are_bound_to_expected_ui_resources(self) -> None:
        expected = {
            "engineering_facts": "ui://mcp-app-ui/engineering_facts/index.html",
            "report_finalize": "ui://mcp-app-ui/report_finalize/index.html",
        }

        for name, resource_uri in expected.items():
            tool = _tool(name)
            self.assertEqual(tool.meta["ui"]["resourceUri"], resource_uri)

    def test_report_prepare_is_not_bound_to_ui(self) -> None:
        tool = _tool("report_prepare")
        self.assertNotIn("ui", tool.meta or {})

    def test_ui_resources_are_registered(self) -> None:
        resources = _run(mcp.list_resources())
        self.assertEqual(
            [str(resource.uri) for resource in resources],
            [
                "ui://mcp-app-ui/engineering_facts/index.html",
                "ui://mcp-app-ui/report_finalize/index.html",
            ],
        )

    def test_engineering_facts_input_schema(self) -> None:
        schema = _tool("engineering_facts").parameters
        self.assertFalse(schema.get("additionalProperties", True))
        # input 与旧版顶层参数（中转层声明形态）并存，均非必填
        self.assertEqual(set(schema.get("required", [])), set())
        self.assertEqual(
            set(schema["properties"]),
            {"input", "source_location", "construction_unit"},
        )
        tool_input = _unwrap_optional(schema["properties"]["input"])
        # extra="ignore" allows legacy format (source_location) to pass through
        self.assertTrue(tool_input.get("additionalProperties", True))
        # provider and root now have defaults (for legacy format compatibility)
        self.assertEqual(set(tool_input.get("required", [])), set())
        self.assertEqual(
            set(tool_input["properties"]),
            {"provider", "root", "file_overrides", "construction_unit"},
        )
        self.assertIn("description", tool_input["properties"]["construction_unit"])
        provider_values = tool_input["properties"]["provider"].get("enum") or [
            tool_input["properties"]["provider"].get("const")
        ]
        self.assertEqual(set(provider_values), {"local_directory", "host_directory"})
        overrides = tool_input["properties"]["file_overrides"]["anyOf"][0]
        self.assertFalse(overrides.get("additionalProperties", True))
        self.assertIn("scheme_path", overrides["properties"])
        self.assertIn("plant_result_path", overrides["properties"])
        # 旧版顶层 source_location 参数保持同名结构
        legacy = _unwrap_optional(schema["properties"]["source_location"])
        self.assertEqual(
            set(legacy["properties"]), {"provider", "location", "file_overrides"}
        )

    def test_report_prepare_input_schema(self) -> None:
        schema = _tool("report_prepare").parameters
        self.assertFalse(schema.get("additionalProperties", True))
        self.assertEqual(set(schema.get("required", [])), set())
        self.assertEqual(
            set(schema["properties"]),
            {"input", "engineering_facts_path", "project_name"},
        )
        prepare_input = _unwrap_optional(schema["properties"]["input"])
        self.assertFalse(prepare_input.get("additionalProperties", True))
        self.assertEqual(prepare_input["required"], ["engineering_facts_path"])
        self.assertEqual(
            set(prepare_input["properties"]),
            {"engineering_facts_path", "report_context"},
        )
        facts_path_description = prepare_input["properties"][
            "engineering_facts_path"
        ]["description"]
        self.assertIn("artifact.path", facts_path_description)
        self.assertIn("宿主逻辑路径", facts_path_description)
        self.assertIn("不能填写本地文件系统路径", facts_path_description)
        report_context = prepare_input["properties"]["report_context"]["anyOf"][0]
        self.assertFalse(report_context.get("additionalProperties", True))
        self.assertEqual(set(report_context["properties"]), {"project_name"})
        # project_name 现以顶层旧版参数形式并存（中转层兼容）
        self.assertIn("project_name", schema["properties"])
        self.assertNotIn("work_results", prepare_input["properties"])

    def test_report_finalize_input_schema(self) -> None:
        schema = _tool("report_finalize").parameters
        self.assertFalse(schema.get("additionalProperties", True))
        self.assertEqual(set(schema.get("required", [])), set())
        self.assertEqual(
            set(schema["properties"]),
            {"input", "work_package_path", "work_results_path"},
        )
        finalize_input = _unwrap_optional(schema["properties"]["input"])
        self.assertFalse(finalize_input.get("additionalProperties", True))
        self.assertEqual(finalize_input["required"], ["work_package_path"])
        self.assertEqual(
            set(finalize_input["properties"]),
            {"work_package_path", "work_results_path"},
        )
        package_path_description = finalize_input["properties"][
            "work_package_path"
        ]["description"]
        results_path_description = finalize_input["properties"][
            "work_results_path"
        ]["description"]
        self.assertIn("artifact.path", package_path_description)
        self.assertIn("宿主逻辑路径", package_path_description)
        self.assertIn("不能填写本地文件系统路径", package_path_description)
        self.assertIn("章节 Agent 工作结果", results_path_description)
        self.assertIn("宿主逻辑路径", results_path_description)
        # MCP 层不再公开 inline work_results 对象，只接受逻辑路径字符串
        self.assertNotIn("work_results", finalize_input["properties"])

    def test_tools_do_not_declare_output_schema(self) -> None:
        """中转层 outputSchema 校验器不支持 oneOf 联合形态，会以 -32602
        拒绝合法的错误信封；因此 Tool 不声明 outputSchema，信封契约改由
        Pydantic 模型在组装时强制（见下方信封模型测试）。"""
        for name in ("engineering_facts", "report_prepare", "report_finalize"):
            with self.subTest(tool=name):
                self.assertIsNone(_tool(name).output_schema)

    def test_envelope_models_validate_three_states(self) -> None:
        cases = [
            (
                EngineeringFactsSuccess,
                EngineeringFactsFailure,
                EngineeringFactsError,
                _engineering_facts_success_envelope(),
            ),
            (
                ReportPrepareSuccess,
                ReportPrepareFailure,
                ReportPrepareError,
                _report_prepare_success_envelope(),
            ),
            (
                ReportFinalizeSuccess,
                ReportFinalizeFailure,
                ReportFinalizeError,
                _report_finalize_success_envelope(),
            ),
        ]
        for success_model, failure_model, error_model, success in cases:
            with self.subTest(tool=type(success_model).__name__):
                success_model.model_validate(success)
                data = success["data"]
                artifact_key = "artifacts" if "artifacts" in data else "artifact"
                failure = {
                    "status": "failed",
                    "data": {
                        "business_status": "failed",
                        artifact_key: None,
                        "summary": {},
                        "error": {"code": "SOME_CODE", "message": "失败", "retryable": True},
                    },
                    "warnings": [],
                }
                failure_model.model_validate(failure)
                error = {
                    "status": "error",
                    "data": {
                        "business_status": "error",
                        artifact_key: None,
                        "summary": {},
                        "error": {"code": "INTERNAL_ERROR", "message": "内部错误", "retryable": False},
                    },
                    "warnings": [],
                }
                error_model.model_validate(error)

    def test_envelope_models_reject_cross_combinations(self) -> None:
        import copy

        cases = [
            (EngineeringFactsSuccess, _engineering_facts_success_envelope()),
            (ReportPrepareSuccess, _report_prepare_success_envelope()),
            (ReportFinalizeSuccess, _report_finalize_success_envelope()),
        ]
        error_info = {"code": "SOME_CODE", "message": "some error", "retryable": True}

        for success_model, success in cases:
            with self.subTest(tool=success_model.__name__):
                data = success["data"]
                artifact_key = "artifacts" if "artifacts" in data else "artifact"

                # 成功状态 + 非 null error 必须被拒绝
                with_error = copy.deepcopy(success)
                with_error["data"]["error"] = error_info
                with self.assertRaises(ValidationError):
                    success_model.model_validate(with_error)

                # 成功状态 + 缺少产物字段必须被拒绝
                missing_artifact = copy.deepcopy(success)
                del missing_artifact["data"][artifact_key]
                with self.assertRaises(ValidationError):
                    success_model.model_validate(missing_artifact)

                # 失败状态 + 产物必须被拒绝
                with_artifact = copy.deepcopy(success)
                with_artifact["status"] = "failed"
                with_artifact["data"]["error"] = error_info
                with self.assertRaises(ValidationError):
                    success_model.model_validate(with_artifact)

                # 失败状态 + 缺少 error 必须被拒绝
                no_error = copy.deepcopy(success)
                no_error["status"] = "failed"
                del no_error["data"]["error"]
                with self.assertRaises(ValidationError):
                    success_model.model_validate(no_error)

    def test_tool_descriptions_cover_required_elements(self) -> None:
        for name in ("engineering_facts", "report_prepare", "report_finalize"):
            with self.subTest(tool=name):
                description = _tool(name).description or ""
                for keyword in ("职责", "适用场景", "返回", "失败情况", "不适用"):
                    self.assertIn(keyword, description)
                for forbidden in (
                    "structuredContent",
                    "_meta",
                    "ui" + "_payload",
                    "宿主 UI",
                    "Agent",
                    "MCP 协议",
                    "调用顺序",
                ):
                    self.assertNotIn(forbidden, description)


if __name__ == "__main__":
    unittest.main()
