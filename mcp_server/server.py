"""MCP Server 入口：可研交付两 Tool 定义 + HTTP 启动。

公开 Tool 清单：

    1. ``engineering_facts``  -- 工程事实整理，读取本地上游算法产物目录。
    2. ``report_generation`` -- 报告准备/定稿，operation=prepare|finalize。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig
from pydantic import BaseModel, Field
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from mcp_server.duck_implement import make_host_client
from src.engineering_facts import execute as execute_engineering_facts
from src.report_generation import execute as execute_report_generation

mcp: FastMCP = FastMCP("feasibility-report-generation")

# engineering_facts 独立 UI 项目的构建产物路径（沿用现有 UI 目录名，不改磁盘路径）
_ENGINEERING_FACTS_UI_HTML = os.path.join(
    os.path.dirname(__file__),
    "engineering-confirmation-ui",
    "ui",
    "dist",
    "index.html",
)

# report_generation 独立 UI 项目的构建产物路径
_REPORT_GENERATION_UI_HTML = os.path.join(
    os.path.dirname(__file__),
    "report-generation-ui",
    "ui",
    "dist",
    "index.html",
)


# ---------------------------------------------------------------------------
# UI 资源：一个工具一个独立 ui 项目，返回其构建产物
# ---------------------------------------------------------------------------
@mcp.resource("ui://mcp-app-ui/engineering_facts/index.html")
def get_engineering_facts_ui_html() -> str:
    """返回 engineering_facts 的 UI HTML（engineering-confirmation-ui 项目的构建产物）。"""
    with open(_ENGINEERING_FACTS_UI_HTML, encoding="utf-8") as f:
        return f.read()


@mcp.resource("ui://mcp-app-ui/report_generation/index.html")
def get_report_generation_ui_html() -> str:
    """返回 report_generation 的 UI HTML（report-generation-ui 项目的构建产物）。"""
    with open(_REPORT_GENERATION_UI_HTML, encoding="utf-8") as f:
        return f.read()


class SourceLocation(BaseModel):
    """工程事实来源位置。"""

    provider: Literal["local_directory"] = Field(
        description="来源提供方；当前仅支持 local_directory。"
    )
    location: str = Field(description="上游算法产物所在的本地目录路径。")


class ReportContext(BaseModel):
    """报告准备阶段可选上下文。"""

    project_name: str | None = Field(
        default=None,
        description="可选项目名称；不传时由工程事实中的建设单位和装置名称确定。",
    )


def _artifact_dir(tool_name: str, operation: str | None = None) -> Path:
    root = Path(os.getenv("FEASIBILITY_ARTIFACT_ROOT", "report_output_dir/mcp_runs"))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parts = [timestamp, tool_name]
    if operation:
        parts.append(operation)
    parts.append(uuid4().hex[:8])
    return (root / "-".join(parts)).resolve()


@mcp.tool(app=AppConfig(resource_uri="ui://mcp-app-ui/engineering_facts/index.html"))
async def engineering_facts(
    ctx: Context,
    source_location: SourceLocation,
    construction_unit: str | None = None,
) -> dict[str, Any]:
    """工程事实整理：读取本地上游算法产物目录，生成 engineering_facts.json。

    本工具只做确定性事实整理：识别上游算法产物、解析最终 adopted_scheme、
    汇总设备/物料/能耗等可复算事实。不做人审提交，不生成报告正文，不推算缺失
    的投资、收益、回收期、IRR 等经济指标。

    Args:
        ctx: fastmcp 上下文（自动注入）。
        source_location: 来源目录；当前支持 provider=local_directory + location。
        construction_unit: 可选建设单位，空值按未提供处理。

    Returns:
        业务核心原始 dict：status、artifact、engineering_facts、summary、diagnostics。
    """
    await ctx.info(f"engineering_facts start: {source_location.location}")
    make_host_client(ctx, base_url=os.getenv("MCP_HOST_URL", "http://127.0.0.1:9000"))
    await ctx.report_progress(1, 4, "校验来源参数")
    request = {
        "source_location": source_location.model_dump(),
        "construction_unit": construction_unit,
    }
    artifact_dir = _artifact_dir("engineering_facts")

    def _run() -> dict[str, Any]:
        return execute_engineering_facts(request, artifact_dir=artifact_dir)

    await ctx.report_progress(2, 4, "读取并识别上游产物")
    result = await asyncio.to_thread(_run)
    await ctx.report_progress(4, 4, "工程事实整理完成")
    return result


@mcp.tool(app=AppConfig(resource_uri="ui://mcp-app-ui/report_generation/index.html"))
async def report_generation(
    ctx: Context,
    operation: Literal["prepare", "finalize"],
    engineering_facts_uri: str | None = None,
    report_context: ReportContext | None = None,
    work_package_uri: str | None = None,
    work_results_uri: str | None = None,
    work_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """报告准备/定稿：prepare 生成工作包，finalize 合并 Agent 结果并导出报告。

    prepare 分支接收 engineering_facts_uri 与可选 report_context，输出
    work_package.json 供 Agent 研究与写作。finalize 分支接收必填的
    work_package_uri，以及可选的 Agent 工作结果（work_results_uri 文件或
    work_results 对象，二选一）；未提供 Agent 结果时仍会导出一份由模板
    fallback 兜底的不完整初稿，并在 diagnostics 中提示。导出 Markdown/DOCX，
    并在返回中内联 Markdown 正文便于 UI 展示。

    Args:
        ctx: fastmcp 上下文（自动注入）。
        operation: prepare 或 finalize。
        engineering_facts_uri: prepare 必填，engineering_facts.json 的 file URI 或本地路径。
        report_context: prepare 可选，目前只接受 project_name。
        work_package_uri: finalize 必填，prepare 返回的 work_package.json URI。
        work_results_uri: finalize 可选，Agent 工作结果 JSON 文件 URI。
        work_results: finalize 可选，Agent 工作结果对象；与 work_results_uri 二选一，
            同时传入会返回参数冲突错误。

    Returns:
        业务核心原始 dict：prepare 返回 prepared 工作包，finalize 返回 completed 报告产物。
    """
    await ctx.info(f"report_generation start: operation={operation}")
    make_host_client(ctx, base_url=os.getenv("MCP_HOST_URL", "http://127.0.0.1:9000"))
    total = 4 if operation == "finalize" else 3
    await ctx.report_progress(1, total, "校验报告生成参数")

    request: dict[str, Any] = {"operation": operation}
    if operation == "prepare":
        if engineering_facts_uri is not None:
            request["engineering_facts_uri"] = engineering_facts_uri
        if report_context is not None:
            request["report_context"] = report_context.model_dump(exclude_none=True)
    else:
        if work_package_uri is not None:
            request["work_package_uri"] = work_package_uri
        if work_results_uri is not None:
            request["work_results_uri"] = work_results_uri
        if work_results is not None:
            request["work_results"] = work_results

    artifact_dir = _artifact_dir("report_generation", operation)

    def _run() -> dict[str, Any]:
        return execute_report_generation(request, artifact_dir=artifact_dir)

    await ctx.report_progress(2, total, "执行业务核心")
    result = await asyncio.to_thread(_run)
    await ctx.report_progress(total, total, "报告生成分支完成")
    return result


# ---------------------------------------------------------------------------
# HTTP 启动入口
# ---------------------------------------------------------------------------
def main() -> None:
    """HTTP 启动：默认 127.0.0.1:8000，可通过环境变量覆盖。"""
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    cors_middleware = Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    mcp.run(
        transport="http",
        host=host,
        port=port,
        host_origin_protection=False,
        middleware=[cors_middleware],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
