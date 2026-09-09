"""MCP Server 入口：可研交付三 Tool 定义 + HTTP 启动。

公开 Tool 清单：

    1. ``engineering_facts`` -- 工程事实整理，读取本地上游算法产物目录。
    2. ``report_prepare``    -- 报告准备，基于工程事实生成章节工作包。
    3. ``report_finalize``   -- 报告定稿，合并 Agent 工作结果并导出报告。

三个 Tool 均返回统一信封 ``{status, data, warnings}``，并通过显式
``output_schema`` 暴露由 Pydantic 模型合成的成功/失败判别联合 Schema。
完整工程事实与 Markdown 不进入模型上下文，只通过同一 Tool Result 的
``_meta.ui_payload`` 提供给宿主 UI；本 Server 不提供任何文件读取 Tool。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from typing import Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from mcp_server.duck_implement import MCPContent, make_host_client
from src.engineering_facts import OperationCancelled as EngineeringFactsCancelled
from src.engineering_facts import execute as execute_engineering_facts
from src.report_finalize import OperationCancelled as ReportFinalizeCancelled
from src.report_finalize import execute as execute_report_finalize
from src.report_prepare import OperationCancelled as ReportPrepareCancelled
from src.report_prepare import execute as execute_report_prepare

logger = logging.getLogger(__name__)

# 三个业务核心各自定义 OperationCancelled；MCP 层统一捕获三者
_CANCELLED_ERRORS = (
    EngineeringFactsCancelled,
    ReportPrepareCancelled,
    ReportFinalizeCancelled,
)

mcp: FastMCP = FastMCP("feasibility-report-generation")

# engineering_facts 独立 UI 项目的构建产物路径（沿用现有 UI 目录名，不改磁盘路径）
_ENGINEERING_FACTS_UI_HTML = os.path.join(
    os.path.dirname(__file__),
    "engineering-confirmation-ui",
    "ui",
    "dist",
    "index.html",
)

# report_finalize 独立 UI 项目的构建产物路径（沿用现有 UI 目录名，不改磁盘路径）
_REPORT_FINALIZE_UI_HTML = os.path.join(
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


@mcp.resource("ui://mcp-app-ui/report_finalize/index.html")
def get_report_finalize_ui_html() -> str:
    """返回 report_finalize 的 UI HTML（report-generation-ui 项目的构建产物）。"""
    with open(_REPORT_FINALIZE_UI_HTML, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------
class SourceLocation(BaseModel):
    """工程事实来源位置。"""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["local_directory"] = Field(
        description="工程事实来源提供方；当前仅支持 local_directory。"
    )
    location: str = Field(
        description="上游工程或算法产物所在的本地目录路径；Tool 只读取该目录内受支持的输入文件。"
    )


class ReportContext(BaseModel):
    """报告编制上下文。"""

    model_config = ConfigDict(extra="forbid")

    project_name: str | None = Field(
        default=None,
        description="可选项目名称；用于覆盖工作包中的报告项目名称，不提供时由工程事实内容确定。",
    )


class ReportPrepareInput(BaseModel):
    """报告准备输入。"""

    model_config = ConfigDict(extra="forbid")

    engineering_facts_path: str = Field(
        description="engineering_facts.json 在宿主存储中的逻辑路径，通常来自工程事实整理产物。"
    )
    report_context: ReportContext | None = Field(
        default=None,
        description="可选报告上下文；用于补充或覆盖不属于工程事实本体的报告编制信息。",
    )


class ReportFinalizeInput(BaseModel):
    """报告定稿输入。"""

    model_config = ConfigDict(extra="forbid")

    work_package_path: str = Field(
        description="report_prepare 生成的 work_package.json 宿主逻辑路径。"
    )
    work_results_path: str | None = Field(
        default=None,
        description="可选章节工作结果 JSON 的宿主逻辑路径；未提供时生成带 fallback 标记的未闭合草稿。",
    )


# ---------------------------------------------------------------------------
# 信封通用模型
# ---------------------------------------------------------------------------
class WarningItem(BaseModel):
    """信封 warnings 中的告警项（由非 fatal 诊断映射而来）。"""

    model_config = ConfigDict(extra="forbid")

    level: Literal["info", "warning"] = Field(default="warning", description="告警级别。")
    code: str = Field(description="稳定告警代码。")
    message: str = Field(description="告警信息。")


class ErrorInfo(BaseModel):
    """失败信封 data.error 中的错误信息（由 fatal 诊断映射而来）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="稳定错误代码。")
    message: str = Field(description="错误说明。")
    retryable: bool = Field(
        default=False,
        description="用户修正输入后重试是否可能解决。",
    )


class ArtifactRef(BaseModel):
    """单一 JSON 产物引用（engineering_facts.json / work_package.json）。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="宿主存储中的逻辑产物路径。")
    media_type: str = Field(description="产物媒体类型。")
    schema_version: str | None = Field(default=None, description="产物 schema 版本。")


class ReportArtifacts(BaseModel):
    """finalize 的成组报告产物。"""

    model_config = ConfigDict(extra="forbid")

    docx_path: str = Field(description="DOCX 报告文件的宿主逻辑路径。")
    docx_media_type: str = Field(description="DOCX 报告媒体类型。")
    markdown_path: str = Field(description="Markdown 报告文件的宿主逻辑路径。")
    markdown_media_type: str = Field(description="Markdown 报告媒体类型。")


class EmptySummary(BaseModel):
    """失败信封的空 summary 占位对象。"""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# engineering_facts 输出模型
# ---------------------------------------------------------------------------
class EngineeringFactsSummary(BaseModel):
    """工程事实摘要（由核心 summary 映射，字段与算法输出一致）。"""

    model_config = ConfigDict(extra="forbid")

    source_count: int
    equipment_count: int
    candidate_count: int
    user_candidate_count: int
    adopted_scheme_count: int
    selected_names: list[str]
    optimized: bool | None
    derived_fact_count: int
    annualized_stream_quantity_count: int
    annualized_material_consumption_count: int


class EngineeringFactsSuccessData(BaseModel):
    """成功信封 data：产物必填、error 必须为 null。"""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    summary: EngineeringFactsSummary
    error: None = None


class EngineeringFactsFailureData(BaseModel):
    """失败信封 data：产物为 null、summary 为空对象、error 必填。"""

    model_config = ConfigDict(extra="forbid")

    artifact: None = None
    summary: EmptySummary = Field(default_factory=EmptySummary)
    error: ErrorInfo


class EngineeringFactsSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed"]
    data: EngineeringFactsSuccessData
    warnings: list[WarningItem] = Field(default_factory=list)


class EngineeringFactsFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["failed"]
    data: EngineeringFactsFailureData
    warnings: list[WarningItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# report_prepare 输出模型
# ---------------------------------------------------------------------------
class ReportPrepareSummary(BaseModel):
    """工作包任务计数摘要。"""

    model_config = ConfigDict(extra="forbid")

    research_task_count: int
    writing_task_count: int
    deterministic_summary_count: int
    synthesis_task_count: int


class ReportPrepareSuccessData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    summary: ReportPrepareSummary
    error: None = None


class ReportPrepareFailureData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: None = None
    summary: EmptySummary = Field(default_factory=EmptySummary)
    error: ErrorInfo


class ReportPrepareSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["prepared"]
    data: ReportPrepareSuccessData
    warnings: list[WarningItem] = Field(default_factory=list)


class ReportPrepareFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["failed"]
    data: ReportPrepareFailureData
    warnings: list[WarningItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# report_finalize 输出模型
# ---------------------------------------------------------------------------
class ReportFinalizeSummary(BaseModel):
    """报告导出摘要；fallback_section_count>0 表示存在未闭合草稿章节。"""

    model_config = ConfigDict(extra="forbid")

    section_count: int
    fallback_section_count: int


class ReportFinalizeSuccessData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: ReportArtifacts
    manifest_path: str
    summary: ReportFinalizeSummary
    error: None = None


class ReportFinalizeFailureData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: None = None
    summary: EmptySummary = Field(default_factory=EmptySummary)
    error: ErrorInfo


class ReportFinalizeSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed"]
    data: ReportFinalizeSuccessData
    warnings: list[WarningItem] = Field(default_factory=list)


class ReportFinalizeFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["failed"]
    data: ReportFinalizeFailureData
    warnings: list[WarningItem] = Field(default_factory=list)


def _envelope_output_schema(*branches: type[BaseModel]) -> dict[str, Any]:
    """由成功/失败信封模型合成顶层为 object 的判别联合 outputSchema。

    MCP 规范要求 outputSchema 顶层必须是 object，因此把两个完整信封模型
    放入顶层 ``oneOf`` 并显式声明 ``type: object``。分支内部通过 ``status``
    常量与各自的 data 结构互相排斥，可拒绝 completed+error、failed+产物 等
    交叉组合。
    """
    branch_schemas: list[dict[str, Any]] = []
    defs: dict[str, Any] = {}
    for model in branches:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        branch_schemas.append(schema)
    return {"type": "object", "oneOf": branch_schemas, "$defs": defs}


_ENGINEERING_FACTS_OUTPUT_SCHEMA = _envelope_output_schema(
    EngineeringFactsSuccess,
    EngineeringFactsFailure,
)
_REPORT_PREPARE_OUTPUT_SCHEMA = _envelope_output_schema(
    ReportPrepareSuccess,
    ReportPrepareFailure,
)
_REPORT_FINALIZE_OUTPUT_SCHEMA = _envelope_output_schema(
    ReportFinalizeSuccess,
    ReportFinalizeFailure,
)


# ---------------------------------------------------------------------------
# 信封组装：核心原始返回 -> {status, data, warnings}
# ---------------------------------------------------------------------------
def _warnings_from_diagnostics(diagnostics: list[dict[str, Any]]) -> list[WarningItem]:
    """非 fatal 诊断映射为 warnings；未知级别按 warning 处理。"""
    items: list[WarningItem] = []
    for item in diagnostics:
        if item.get("level") == "fatal":
            continue
        items.append(
            WarningItem(
                level="info" if item.get("level") == "info" else "warning",
                code=str(item.get("code", "UNKNOWN")),
                message=str(item.get("message", "")),
            )
        )
    return items


def _error_from_diagnostics(diagnostics: list[dict[str, Any]]) -> ErrorInfo:
    """首个 fatal 诊断映射为 data.error。

    能走到这里的都是可预期业务失败（内部错误码已在信封组装前转为
    ToolError），用户修正输入后重试可能解决，因此 retryable=true。
    """
    for item in diagnostics:
        if item.get("level") == "fatal":
            return ErrorInfo(
                code=str(item.get("code", "UNKNOWN_FATAL")),
                message=str(item.get("message", "")),
                retryable=True,
            )
    return ErrorInfo(code="UNKNOWN_FATAL", message="unknown fatal diagnostic", retryable=True)


def _engineering_facts_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """把工程事实核心返回组装为统一信封。"""
    diagnostics = [d for d in result.get("diagnostics") or [] if isinstance(d, dict)]
    warnings = _warnings_from_diagnostics(diagnostics)
    if result.get("status") == "completed":
        envelope: BaseModel = EngineeringFactsSuccess(
            status="completed",
            data=EngineeringFactsSuccessData(
                artifact=ArtifactRef.model_validate(result["artifact"]),
                summary=EngineeringFactsSummary.model_validate(result.get("summary") or {}),
            ),
            warnings=warnings,
        )
    else:
        envelope = EngineeringFactsFailure(
            status="failed",
            data=EngineeringFactsFailureData(error=_error_from_diagnostics(diagnostics)),
            warnings=warnings,
        )
    return envelope.model_dump()


def _report_prepare_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """把报告准备核心返回组装为统一信封。"""
    diagnostics = [d for d in result.get("diagnostics") or [] if isinstance(d, dict)]
    warnings = _warnings_from_diagnostics(diagnostics)
    if result.get("status") == "prepared":
        envelope: BaseModel = ReportPrepareSuccess(
            status="prepared",
            data=ReportPrepareSuccessData(
                artifact=ArtifactRef.model_validate(result["artifact"]),
                summary=ReportPrepareSummary.model_validate(result.get("summary") or {}),
            ),
            warnings=warnings,
        )
    else:
        envelope = ReportPrepareFailure(
            status="failed",
            data=ReportPrepareFailureData(error=_error_from_diagnostics(diagnostics)),
            warnings=warnings,
        )
    return envelope.model_dump()


def _report_finalize_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """把报告定稿核心返回组装为统一信封。"""
    diagnostics = [d for d in result.get("diagnostics") or [] if isinstance(d, dict)]
    warnings = _warnings_from_diagnostics(diagnostics)
    if result.get("status") == "completed":
        envelope: BaseModel = ReportFinalizeSuccess(
            status="completed",
            data=ReportFinalizeSuccessData(
                artifacts=ReportArtifacts.model_validate(result["artifacts"]),
                manifest_path=str(result["manifest_path"]),
                summary=ReportFinalizeSummary.model_validate(result.get("summary") or {}),
            ),
            warnings=warnings,
        )
    else:
        envelope = ReportFinalizeFailure(
            status="failed",
            data=ReportFinalizeFailureData(error=_error_from_diagnostics(diagnostics)),
            warnings=warnings,
        )
    return envelope.model_dump()


# ---------------------------------------------------------------------------
# 协作式取消桥接
# ---------------------------------------------------------------------------
async def _run_with_cancellation(
    run: Callable[[], dict[str, Any]],
    cancel_event: threading.Event,
    log_label: str,
    tool_error_message: str,
) -> dict[str, Any]:
    """在线程中执行业务核心，并桥接协作式取消。

    - worker Task 显式保存，首次等待使用 ``asyncio.shield``，避免外层取消
      把 worker 一并取消后线程失控；
    - 收到客户端取消后先设置 Event，等待 worker 完成清理，再传播原始
      ``CancelledError``；
    - 核心在检查点抛出的 ``OperationCancelled`` 对外表现为已取消；
    - 未预期内部异常记录服务端日志并抛 ``ToolError``，不向调用方泄露
      traceback 或敏感绝对路径。
    """
    worker = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancelled:
        # 客户端取消：设置信号后等待后台线程协作退出，再传播原始取消
        cancel_event.set()
        try:
            await asyncio.shield(worker)
        except _CANCELLED_ERRORS:
            pass
        except Exception:  # noqa: BLE001 - 清理期异常只记服务端日志，不覆盖取消语义
            logger.exception("%s worker 在取消清理期间异常退出", log_label)
        raise cancelled
    except _CANCELLED_ERRORS as exc:
        # 核心已在检查点协作退出：对外表现为已取消，不返回业务信封
        raise asyncio.CancelledError() from exc
    except Exception as exc:
        logger.exception("%s 出现未预期内部错误", log_label)
        raise ToolError(tool_error_message) from exc


# ---------------------------------------------------------------------------
# 公开 Tool 定义
# ---------------------------------------------------------------------------
@mcp.tool(
    app=AppConfig(resource_uri="ui://mcp-app-ui/engineering_facts/index.html"),
    output_schema=_ENGINEERING_FACTS_OUTPUT_SCHEMA,
)
async def engineering_facts(
    ctx: Context,
    source_location: SourceLocation,
    construction_unit: str | None = Field(
        default=None,
        description="可选建设单位名称；未提供时按空值处理，不自动推断。",
    ),
) -> ToolResult:
    """工程事实整理：基于本地工程或算法产物生成 engineering_facts.json。

    【职责】识别受支持的工程输入文件，整理设备、物料、能耗、方案等可复核
    工程事实，并保存为结构化 JSON 产物。
    【适用场景】已有本地工程或算法输出目录，需要形成可研编制所需的统一
    工程事实文件。
    【返回关键结果】structuredContent 为 {status, data, warnings} 信封：
    status=completed 时 data.artifact.path 指向 engineering_facts.json，
    data.summary 提供来源、设备、方案和派生事实计数；status=failed 时
    data.error 提供稳定错误代码和可重试标记。完整工程事实通过
    _meta.ui_payload.engineering_facts 提供给宿主 UI。
    【失败情况】来源目录缺失、来源 JSON 损坏、输入文件无法识别等业务失败
    返回 failed 信封；未预期内部异常以 MCP 协议错误返回。
    """
    await ctx.info(f"engineering_facts start: {source_location.location}")
    loop = asyncio.get_running_loop()
    content = MCPContent(ctx, loop)
    host_client = make_host_client(
        ctx,
        base_url=os.getenv("MCP_HOST_URL", "http://127.0.0.1:9000"),
    )
    request = {
        "source_location": source_location.model_dump(),
        "construction_unit": construction_unit,
    }
    cancel_event = threading.Event()

    def _run() -> dict[str, Any]:
        return execute_engineering_facts(
            request,
            content=content,
            host_client=host_client,
            cancel_event=cancel_event,
        )

    result = await _run_with_cancellation(
        _run, cancel_event, "engineering_facts", "工程事实整理出现未预期内部错误"
    )
    ui_payload: dict[str, Any] = {}
    if result.get("status") == "completed":
        # 完整工程事实只进 UI payload，不进模型可见 structuredContent
        ui_payload["engineering_facts"] = result.get("engineering_facts")
    return ToolResult(
        structured_content=_engineering_facts_envelope(result),
        meta={"ui_payload": ui_payload},
    )


@mcp.tool(output_schema=_REPORT_PREPARE_OUTPUT_SCHEMA)
async def report_prepare(
    ctx: Context,
    input: ReportPrepareInput,
) -> ToolResult:
    """报告准备：基于工程事实文件生成可研报告章节工作包。

    【职责】读取 engineering_facts.json，构建章节结构、确定性内容块和报告
    编制任务包，并保存 work_package.json。
    【适用场景】已有结构化工程事实文件，需要拆解可研报告编制任务并形成
    可交付的章节工作包。
    【返回关键结果】structuredContent 为 {status, data, warnings} 信封：
    status=prepared 时 data.artifact.path 指向 work_package.json，
    data.summary 提供研究、写作、确定性摘要和综合任务计数；status=failed
    时 data.error 提供稳定错误代码和可重试标记。
    【失败情况】工程事实路径无效、工程事实 JSON 损坏、工程事实根结构不符合
    契约等业务失败返回 failed 信封；未预期内部异常以 MCP 协议错误返回。
    """
    await ctx.info("report_prepare start")
    loop = asyncio.get_running_loop()
    content = MCPContent(ctx, loop)
    host_client = make_host_client(
        ctx,
        base_url=os.getenv("MCP_HOST_URL", "http://127.0.0.1:9000"),
    )
    request: dict[str, Any] = input.model_dump(exclude_none=True)
    cancel_event = threading.Event()

    def _run() -> dict[str, Any]:
        return execute_report_prepare(
            request,
            content=content,
            host_client=host_client,
            cancel_event=cancel_event,
        )

    result = await _run_with_cancellation(
        _run, cancel_event, "report_prepare", "报告准备出现未预期内部错误"
    )
    # prepare 是中间步骤，不绑定 UI；工作包由宿主 Agent 按逻辑路径读取
    return ToolResult(
        structured_content=_report_prepare_envelope(result),
        meta={"ui_payload": {}},
    )


@mcp.tool(
    app=AppConfig(resource_uri="ui://mcp-app-ui/report_finalize/index.html"),
    output_schema=_REPORT_FINALIZE_OUTPUT_SCHEMA,
)
async def report_finalize(
    ctx: Context,
    input: ReportFinalizeInput,
) -> ToolResult:
    """报告定稿：基于报告工作包和章节工作结果导出 DOCX/Markdown 报告。

    【职责】读取 work_package.json 与可选 work_results.json，合成可行性研究
    报告内容，并导出 DOCX、Markdown 和报告清单。
    【适用场景】已有章节工作包，需要生成最终报告文件；章节工作结果缺失时
    可生成带 fallback 标记的未闭合草稿。
    【返回关键结果】structuredContent 为 {status, data, warnings} 信封：
    status=completed 时 data.artifacts 提供 DOCX/Markdown 逻辑路径，
    data.manifest_path 指向报告清单，data.summary.fallback_section_count 标记
    fallback 章节数量；完整 Markdown 通过 _meta.ui_payload.markdown_content
    提供给宿主 UI。status=failed 时 data.error 提供稳定错误代码和可重试标记。
    【失败情况】工作包路径无效、工作包 JSON 损坏、章节工作结果不符合契约等
    业务失败返回 failed 信封；未预期内部异常以 MCP 协议错误返回。
    """
    await ctx.info("report_finalize start")
    loop = asyncio.get_running_loop()
    content = MCPContent(ctx, loop)
    host_client = make_host_client(
        ctx,
        base_url=os.getenv("MCP_HOST_URL", "http://127.0.0.1:9000"),
    )
    request: dict[str, Any] = input.model_dump(exclude_none=True)
    cancel_event = threading.Event()

    def _run() -> dict[str, Any]:
        return execute_report_finalize(
            request,
            content=content,
            host_client=host_client,
            cancel_event=cancel_event,
        )

    result = await _run_with_cancellation(
        _run, cancel_event, "report_finalize", "报告定稿出现未预期内部错误"
    )
    ui_payload: dict[str, Any] = {}
    if result.get("status") == "completed":
        # 完整 Markdown 只进 UI payload，不进模型可见 structuredContent
        ui_payload["markdown_content"] = result.get("markdown_content")
    return ToolResult(
        structured_content=_report_finalize_envelope(result),
        meta={"ui_payload": ui_payload},
    )


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
