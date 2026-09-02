"""MCP Server 入口：可研交付 4 阶段 tool 定义 + HTTP 启动。

Tool 清单（对应 ``tools/`` 下 4 个 Tool 的进程内核心逻辑，已重写至 ``src/``）：

    1. ``engineering_facts``       —— 阶段①工程事实整理（长任务，report_progress 推进度）。
    2. ``engineering_confirmation`` —— 阶段②关键事实确认（长任务，report_progress 推进度）。
    3. ``chapter_planning``        —— 阶段③章节规划与研究/LLM 路由（长任务，report_progress 推进度）。
    4. ``report_generation``       —— 阶段④报告生成（长任务，文件读写 + report_progress）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from duck.content import Content
from duck.host_client import HostClient
from mcp_server.duck_implement import MCPContent, make_host_client
from src.chapter_planning import ChapterPlanningWorkflow
from src.engineering_confirmation import EngineeringConfirmationWorkflow
from src.engineering_facts import EngineeringFactsWorkflow
from src.report_generation import ReportGenerationWorkflow

mcp: FastMCP = FastMCP("feasibility-report-generation")

# engineering_confirmation 独立 UI 项目的构建产物路径
_ENGINEERING_CONFIRMATION_UI_HTML = os.path.join(
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
@mcp.resource("ui://mcp-app-ui/engineering_confirmation/index.html")
def get_engineering_confirmation_ui_html() -> str:
    """返回 engineering_confirmation 的 UI HTML（engineering-confirmation-ui 项目的构建产物）。"""
    with open(_ENGINEERING_CONFIRMATION_UI_HTML, "r", encoding="utf-8") as f:
        return f.read()


@mcp.resource("ui://mcp-app-ui/report_generation/index.html")
def get_report_generation_ui_html() -> str:
    """返回 report_generation 的 UI HTML（report-generation-ui 项目的构建产物）。"""
    with open(_REPORT_GENERATION_UI_HTML, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Tool 1: 阶段①工程事实整理
# ---------------------------------------------------------------------------
@mcp.tool()
async def engineering_facts(
    ctx: Context,
    output_dir: str = "report_output_dir",
    workspace: Optional[str] = None,
    source_manifest: Optional[str] = None,
    profile: Optional[str] = None,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    project_level: str = "unit",
    project_type: str = "mixed",
    user_inputs: Optional[str] = None,
    construction_unit: Optional[str] = None,
    annual_operating_hours: Optional[float] = None,
    project_location: Optional[str] = None,
    implementation_schedule: Optional[str] = None,
) -> dict[str, Any]:
    """阶段①工程事实整理：来源登记 -> Profile 解析 -> 事实构建。

    内部调用 ``src.engineering_facts.EngineeringFactsWorkflow``，通过
    ``MCPContent`` 桥接进度推送。

    Args:
        ctx: fastmcp 上下文（自动注入）。
        output_dir: 可选，默认 ``report_output_dir``，产物写入该目录。
        workspace: 算法输出工作区目录；与 source_manifest 二选一必填。
        source_manifest: 来源清单文件；与 workspace 二选一。
        profile: 可选显式覆盖的项目画像文件。
        project_name: 项目名称（来源登记元信息）。
        project_id: 项目编号（来源登记元信息）。
        project_level: 项目层级，equipment/unit/system/plant，默认 unit。
        project_type: 项目类型，默认 mixed。
        user_inputs: 用户输入文件路径。
        construction_unit: 建设单位。
        annual_operating_hours: 年运行时长。
        project_location: 项目地点。
        implementation_schedule: 可选，宿主提供的实施进度估算 JSON 文件。

    Returns:
        含 ``exit_code``（0=facts_ready，2=needs_resolution）与阶段摘要字段的字典。
    """
    await ctx.info(f"engineering_facts start: output_dir={output_dir}")
    content: Content = MCPContent(ctx, asyncio.get_running_loop())

    def _run() -> dict[str, Any]:
        workflow = EngineeringFactsWorkflow(content=content)
        return workflow.run(
            workspace=workspace,
            source_manifest=source_manifest,
            profile=profile,
            project_name=project_name,
            project_id=project_id,
            project_level=project_level,
            project_type=project_type,
            user_inputs=user_inputs,
            construction_unit=construction_unit,
            annual_operating_hours=annual_operating_hours,
            project_location=project_location,
            implementation_schedule=implementation_schedule,
            output_dir=output_dir,
        )

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Tool 2: 阶段②关键事实确认
# ---------------------------------------------------------------------------
@mcp.tool(app=AppConfig(resource_uri="ui://mcp-app-ui/engineering_confirmation/index.html"))
async def engineering_confirmation(
    ctx: Context,
    facts: str,
    output_dir: str = "report_output_dir",
    confirm_as_is: bool = False,
    confirmation_response: Optional[str] = None,
) -> dict[str, Any]:
    """阶段②关键事实确认：年化 merge + 生成确认单 + 人工确认门槛。

    内部调用 ``src.engineering_confirmation.EngineeringConfirmationWorkflow``。

    Args:
        ctx: fastmcp 上下文（自动注入）。
        facts: 必填，阶段①产出的 project_facts.json 路径（会被年化 merge 回写）。
        output_dir: 可选，默认 ``report_output_dir``。
        confirm_as_is: 显式确认 report_confirmation.json 当前内容，不做修改。
        confirmation_response: 用户确认/修正后的确认响应 JSON/YAML 路径。

    Returns:
        含 ``exit_code``（0=confirmed，12=needs_confirmation）与阶段摘要字段的字典。
    """
    await ctx.info(f"engineering_confirmation start: facts={facts}")
    content: Content = MCPContent(ctx, asyncio.get_running_loop())

    def _run() -> dict[str, Any]:
        workflow = EngineeringConfirmationWorkflow(content=content)
        return workflow.run(
            facts=facts,
            output_dir=output_dir,
            confirm_as_is=confirm_as_is,
            confirmation_response=confirmation_response,
        )

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Tool 3: 阶段③章节规划与研究/LLM 路由
# ---------------------------------------------------------------------------
@mcp.tool()
async def chapter_planning(
    ctx: Context,
    facts: str,
    profile: str,
    output_dir: str = "report_output_dir",
    research_evidence: Optional[str] = None,
    section_drafts: Optional[str] = None,
    ai_mode: str = "host_agent",
    run_mode: str = "production",
    skip_user_inputs: bool = False,
) -> dict[str, Any]:
    """阶段③章节规划与研究/LLM 路由：章节计划 -> 缺口分析 -> 研究任务 -> 路由。

    内部调用 ``src.chapter_planning.ChapterPlanningWorkflow``。

    Args:
        ctx: fastmcp 上下文（自动注入）。
        facts: 必填，confirmed_project_facts.json 路径。
        profile: 必填，项目画像文件路径。
        output_dir: 可选，默认 ``report_output_dir``。
        research_evidence: 宿主提供的研究证据文件路径。
        section_drafts: 宿主提供的章节草稿文件路径。
        ai_mode: host_agent（默认）/ disabled。
        run_mode: production（默认）/ test（要求环境变量 FEASIBILITY_SKILL_DEV_TEST=1）。
        skip_user_inputs: 跳过编制信息一次性提问，保留缺口继续。

    Returns:
        含 ``exit_code``（0=planning_ready，9=needs_user_input，10=needs_research，
        11=needs_llm）与阶段摘要字段的字典。
    """
    await ctx.info(f"chapter_planning start: facts={facts}")
    content: Content = MCPContent(ctx, asyncio.get_running_loop())

    def _run() -> dict[str, Any]:
        workflow = ChapterPlanningWorkflow(content=content)
        return workflow.run(
            facts=facts,
            profile=profile,
            output_dir=output_dir,
            research_evidence=research_evidence,
            section_drafts=section_drafts,
            ai_mode=ai_mode,
            run_mode=run_mode,
            skip_user_inputs=skip_user_inputs,
        )

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Tool 4: 阶段④报告生成
# ---------------------------------------------------------------------------
@mcp.tool(app=AppConfig(resource_uri="ui://mcp-app-ui/report_generation/index.html"))
async def report_generation(
    ctx: Context,
    facts: str,
    profile: str,
    chapter_plan: str,
    output_dir: str = "report_output_dir",
    section_drafts: Optional[str] = None,
    strict_consistency: bool = False,
) -> dict[str, Any]:
    """阶段④报告生成：拓扑/表格 -> 报告模型 -> 一致性门槛 -> DOCX/Markdown 导出。

    内部调用 ``src.report_generation.ReportGenerationWorkflow``，生成成功后
    通过 ``MCPHostClient`` 把最终报告推送到宿主存储（best-effort）。

    Args:
        ctx: fastmcp 上下文（自动注入）。
        facts: 必填，confirmed_project_facts.json 路径。
        profile: 必填，项目画像文件路径。
        chapter_plan: 必填，chapter_plan.json 路径。
        output_dir: 可选，默认 ``report_output_dir``。
        section_drafts: 可选，阶段③产物章节草稿。
        strict_consistency: 将上游高严重性事实冲突升级为阻断。

    Returns:
        含 ``exit_code``（0=generated，3=consistency_blocked）与阶段摘要字段的字典。
    """
    await ctx.info(f"report_generation start: facts={facts}")
    content: Content = MCPContent(ctx, asyncio.get_running_loop())
    host_client: HostClient = make_host_client(
        ctx, base_url=os.getenv("MCP_HOST_URL", "http://127.0.0.1:9000")
    )

    def _run() -> dict[str, Any]:
        workflow = ReportGenerationWorkflow(content=content, host_client=host_client)
        return workflow.run(
            facts=facts,
            profile=profile,
            chapter_plan=chapter_plan,
            output_dir=output_dir,
            section_drafts=section_drafts,
            strict_consistency=strict_consistency,
        )

    return await asyncio.to_thread(_run)


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
