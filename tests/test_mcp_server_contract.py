"""MCP server public contract tests."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest

import httpx
import jsonschema
from fastmcp.exceptions import ToolError

from mcp_server.server import _run_with_cancellation, mcp
from mcp_server.duck_implement.host_client import MCPHostClient
from src.errors import HostStorageError
from src.report_prepare import OperationCancelled
from src.report_prepare import execute as execute_prepare
from tests.fakes import FakeContent


def _run(awaitable):
    return asyncio.run(awaitable)


def _tool(name: str):
    # 使用 tools/list 的真实对外契约（schema 已内联展开）
    for tool in _run(mcp.list_tools()):
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name} not found")


def _engineering_facts_success_envelope() -> dict:
    return {
        "status": "completed",
        "data": {
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
        "status": "prepared",
        "data": {
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
        "status": "completed",
        "data": {
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

    def test_unexpected_internal_error_is_translated_to_tool_error(self) -> None:
        def fail() -> dict:
            raise RuntimeError("algorithm bug")
        with self.assertLogs("mcp_server.server", level="ERROR"):
            with self.assertRaises(ToolError):
                _run(_run_with_cancellation(fail, threading.Event(), "bug", "internal"))

    def test_mcp_host_bad_json_is_business_failed_but_5xx_is_storage_error(self) -> None:
        def bad_json_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{bad json")

        bad_client = MCPHostClient(base_url="http://host", client=httpx.Client(transport=httpx.MockTransport(bad_json_handler)))
        result = execute_prepare({"engineering_facts_path": "inputs/facts.json"}, content=FakeContent(), host_client=bad_client)
        self.assertEqual(result["status"], "failed")
        self.assertIn("valid JSON object", result["diagnostics"][0]["message"])

        def unavailable_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "unavailable"})

        unavailable = MCPHostClient(base_url="http://host", client=httpx.Client(transport=httpx.MockTransport(unavailable_handler)))
        with self.assertRaises(HostStorageError):
            execute_prepare({"engineering_facts_path": "inputs/facts.json"}, content=FakeContent(), host_client=unavailable)

    def test_internal_storage_error_is_translated_to_tool_error(self) -> None:
        def fail() -> dict:
            raise HostStorageError("simulated storage outage")

        with self.assertLogs("mcp_server.server", level="ERROR"):
            with self.assertRaises(ToolError):
                _run(
                    _run_with_cancellation(
                        fail,
                        threading.Event(),
                        "storage-test",
                        "内部存储错误",
                    )
                )

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
        self.assertEqual(schema["required"], ["source_location"])
        self.assertEqual(
            set(schema["properties"]), {"source_location", "construction_unit"}
        )
        self.assertIn("description", schema["properties"]["construction_unit"])
        source = schema["properties"]["source_location"]
        self.assertFalse(source.get("additionalProperties", True))
        self.assertEqual(set(source["required"]), {"provider", "location"})
        self.assertEqual(source["properties"]["provider"]["const"], "local_directory")

    def test_report_prepare_input_schema(self) -> None:
        schema = _tool("report_prepare").parameters
        self.assertFalse(schema.get("additionalProperties", True))
        self.assertEqual(schema["required"], ["input"])
        self.assertEqual(set(schema["properties"]), {"input"})
        prepare_input = schema["properties"]["input"]
        self.assertFalse(prepare_input.get("additionalProperties", True))
        self.assertEqual(prepare_input["required"], ["engineering_facts_path"])
        self.assertEqual(
            set(prepare_input["properties"]),
            {"engineering_facts_path", "report_context"},
        )
        report_context = prepare_input["properties"]["report_context"]["anyOf"][0]
        self.assertFalse(report_context.get("additionalProperties", True))
        self.assertEqual(set(report_context["properties"]), {"project_name"})
        self.assertNotIn("project_name", schema["properties"])
        self.assertNotIn("work_results", prepare_input["properties"])

    def test_report_finalize_input_schema(self) -> None:
        schema = _tool("report_finalize").parameters
        self.assertFalse(schema.get("additionalProperties", True))
        self.assertEqual(schema["required"], ["input"])
        self.assertEqual(set(schema["properties"]), {"input"})
        finalize_input = schema["properties"]["input"]
        self.assertFalse(finalize_input.get("additionalProperties", True))
        self.assertEqual(finalize_input["required"], ["work_package_path"])
        self.assertEqual(
            set(finalize_input["properties"]),
            {"work_package_path", "work_results_path"},
        )
        # MCP 层不再公开 inline work_results 对象，只接受逻辑路径字符串
        self.assertNotIn("work_results", finalize_input["properties"])

    def test_output_schemas_are_discriminated_unions(self) -> None:
        cases = {
            "engineering_facts": ("completed", "failed"),
            "report_prepare": ("prepared", "failed"),
            "report_finalize": ("completed", "failed"),
        }
        for name, (success_status, failure_status) in cases.items():
            with self.subTest(tool=name):
                schema = _tool(name).output_schema
                self.assertEqual(schema["type"], "object")
                branches = schema["oneOf"]
                self.assertEqual(len(branches), 2)
                status_consts = {
                    branch["properties"]["status"]["const"] for branch in branches
                }
                self.assertEqual(status_consts, {success_status, failure_status})
                for branch in branches:
                    # warnings 有默认值，不在 required；status 与 data 必填
                    self.assertEqual(set(branch["required"]), {"status", "data"})
                    self.assertIn("warnings", branch["properties"])
                    self.assertFalse(branch.get("additionalProperties", True))

    def test_engineering_facts_output_schema_accepts_valid_envelopes(self) -> None:
        schema = _tool("engineering_facts").output_schema
        jsonschema.validate(_engineering_facts_success_envelope(), schema)
        failure = {
            "status": "failed",
            "data": {
                "artifact": None,
                "summary": {},
                "error": {"code": "NO_RECOGNIZED_SOURCES", "message": "无有效来源", "retryable": True},
            },
            "warnings": [
                {"level": "warning", "code": "PLANT_INFO_MISSING", "message": "缺少装置信息"}
            ],
        }
        jsonschema.validate(failure, schema)

    def test_report_prepare_output_schema_accepts_valid_envelopes(self) -> None:
        schema = _tool("report_prepare").output_schema
        jsonschema.validate(_report_prepare_success_envelope(), schema)
        failure = {
            "status": "failed",
            "data": {
                "artifact": None,
                "summary": {},
                "error": {"code": "FACTS_PATH_INVALID", "message": "工程事实路径无效", "retryable": True},
            },
            "warnings": [],
        }
        jsonschema.validate(failure, schema)

    def test_report_finalize_output_schema_accepts_valid_envelopes(self) -> None:
        schema = _tool("report_finalize").output_schema
        jsonschema.validate(_report_finalize_success_envelope(), schema)
        failure = {
            "status": "failed",
            "data": {
                "artifacts": None,
                "summary": {},
                "error": {"code": "WORK_PACKAGE_INVALID", "message": "工作包无效", "retryable": True},
            },
            "warnings": [],
        }
        jsonschema.validate(failure, schema)

    def test_output_schemas_reject_cross_combinations(self) -> None:
        import copy

        cases = {
            "engineering_facts": _engineering_facts_success_envelope(),
            "report_prepare": _report_prepare_success_envelope(),
            "report_finalize": _report_finalize_success_envelope(),
        }
        error_info = {"code": "SOME_CODE", "message": "some error", "retryable": True}

        for name, success in cases.items():
            schema = _tool(name).output_schema
            data = success["data"]

            # 成功状态 + 非 null error 必须被拒绝
            with_error = copy.deepcopy(success)
            with_error["data"]["error"] = error_info
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(with_error, schema)

            # 成功状态 + 缺少产物字段必须被拒绝
            missing_artifact = copy.deepcopy(success)
            artifact_key = "artifacts" if "artifacts" in data else "artifact"
            del missing_artifact["data"][artifact_key]
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(missing_artifact, schema)

            # 失败状态 + 产物必须被拒绝
            with_artifact = copy.deepcopy(success)
            with_artifact["status"] = "failed"
            with_artifact["data"]["error"] = error_info
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(with_artifact, schema)

            # 失败状态 + 缺少 error 必须被拒绝
            no_error = copy.deepcopy(success)
            no_error["status"] = "failed"
            del no_error["data"]["error"]
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(no_error, schema)

    def test_tool_descriptions_cover_required_elements(self) -> None:
        for name in ("engineering_facts", "report_prepare", "report_finalize"):
            with self.subTest(tool=name):
                description = _tool(name).description or ""
                for keyword in ("职责", "适用场景", "失败情况"):
                    self.assertIn(keyword, description)


if __name__ == "__main__":
    unittest.main()
