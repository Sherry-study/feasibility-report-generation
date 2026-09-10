# Tool 层改造设计文档

> 日期：2026-09-08
> 状态：已按 2026-09-08 评审意见修订
> 范围：改 MCP Tool 契约、`src` 业务核心边界、duck 能力注入、取消支持及既有 UI 的必要协议适配；不改根目录 `提示词.md`，不改 UI 页面组件/视觉/业务展示。

---

## 1. 背景：为什么要改

### 1.1 规范依据

对照《MCP-Tool 编写规范速览》5 条规则和《Skill 与 Tool 编写示例》，当前两个 Tool 存在以下不符合规范的问题：

| # | 规范要求 | 当前问题 |
|---|---------|---------|
| 1 | 返回统一信封 `{status, data, warnings}` | 没有 `data` 包裹层，字段平铺在顶层 |
| 2 | 大结果落盘、模型侧摘要优先 | `engineering_facts` 和 Markdown 已落盘，但完整对象仍进入模型可见返回；既有 UI 又依赖这些数据 |
| 3 | 入参顶级 ≤5 个 | 旧 `report_generation` 有 6 个顶级参数，且 `operation` 路由导致不同分支参数语义混杂 |
| 4 | 协作式取消检查点 | 两个 Tool 都是 `async def` + `asyncio.to_thread` 模式，完全没有 `threading.Event` 取消信号桥接 |
| 5 | Tool description 四要素（职责/场景/返回关键字段/错误/何时不调用） | 缺少"何时不调用"边界说明 |
| 6 | 用 `ToolError` 而非裸异常 | 当前业务核心会把预期异常转换为 `status=failed`，但 MCP 层尚未区分业务失败、未预期异常和取消 |
| 7 | 明确输出 Schema | 两个 Tool 均返回 `dict[str, Any]`，运行时 output schema 只是允许任意字段的 object |

### 1.2 架构问题

旧 `report_generation` 用 `operation` 参数做内部路由（prepare / finalize），两个分支的入参、出参、场景完全不同，耦合在一起违反单一职责：

- **prepare**：需要工程事实文件路径，产出 `work_package.json`
- **finalize**：需要工作包路径和工作结果路径，产出 DOCX/Markdown

当前实现虽然在 FastMCP 层注册了 `report_prepare` / `report_finalize` 两个入口，但二者仍映射到同一个 `src/report_generation/core.py` 旧路由器。这只是在 MCP 外壳层拆分，没有形成“每个 Tool 一个独立业务核心”的真实边界。

此外，`mcp_server/server.py` 中已经出现 `make_host_client(ctx)` 调用，但返回值未传入 `src` 核心，相当于创建了 HostClient 后立即丢弃。业务核心仍直接使用本地文件 I/O，duck 协议没有真实接入。

---

## 2. 改造目标

1. 返回结构统一为 `{status, data, warnings}` 信封，并为三个 Tool 提供明确的 Pydantic 输出 Schema
2. `report_generation` 拆为 `report_prepare` + `report_finalize` 两个独立 Tool，并拆出对应的独立 `src` 核心入口
3. 模型可见结果只返回产物信息和摘要；既有 UI 需要的完整对象通过 progress 通知的 `uiEvent.final_result` 传递
4. 不新增 `read_artifact`、`read_file` 或其他文件读取 Tool
5. 不新增用户可见或模型可见的 `run_id`；内部运行目录继续用时间戳 + UUID 隔离，但内部时间戳 + UUID 不写入公开产物字段，也不作为 MCP 参数或返回独立字段
6. 添加真正进入同步业务核心的 `threading.Event` 协作式取消检查点
7. 可预期业务失败返回失败信封；未预期内部异常才使用 `ToolError`
8. description 补全职责、场景、返回、错误和不调用边界，入参细节写进 Field description

9. FastMCP 层仅做适配：创建 `MCPContent(ctx, loop)` 与 `make_host_client(ctx)`，并把实际需要的能力注入对应 `src` 核心
10. `src` 业务层只依赖 `duck.content.Content` / `duck.host_client.HostClient`，不依赖 FastMCP、Context、ToolResult 或 `mcp_server`

### 2.1 运行环境前提

本方案从“本地文件路径引用”切换为“HostClient 逻辑路径”作为 Tool 间产物交换方式。完整链路为：

1. `engineering_facts` 通过 `HostClient.save_file()` 写入工程事实文件，返回 `engineering_facts_path`；
2. `report_prepare` 通过 `HostClient.get_file(engineering_facts_path)` 读取工程事实，先生成并完整校验 `work_package.json` 内容，再保存全部章节 context 文件，最后保存 `work_package.json` 并返回 `work_package_path`；
3. Host Agent 使用宿主已有工作区文件能力读取工作包，完成研究/编写/汇总任务，并把 `work_results.json` 写回宿主工作区；
4. `report_finalize` 通过 `HostClient.get_file(work_package_path)` 读取工作包；若提供 `work_results_path`，再通过 `HostClient.get_file(work_results_path)` 读取工作结果；若未提供，则沿用既有业务语义生成 fallback 未闭合草稿；
5. `report_finalize` 最后写入 manifest；只有通过 Schema、路径关联、hash、size 校验的 manifest 指向同一组 Markdown/DOCX 时，这组报告才被视为有效交付物。

`source_location.provider=local_directory` 的例外边界必须严格收窄：仅允许在用户明确传入的本地根目录内枚举并读取受支持源文件；不得越出该根目录，不得读取无关文件。此例外只用于 `engineering_facts` 的工程事实输入采集，不覆盖跨 Tool 产物或输出；工程事实、工作包、工作结果、章节 context、Markdown、DOCX、manifest 全部走 HostClient 逻辑路径。

这里依赖的是 **Host 已有的工作区文件能力**，不是本 MCP Server 再暴露文件读取 Tool。若部署环境不具备该能力，应停止实施并调整部署架构，不能用新增 `read_artifact` 或 `read_file` 来补洞。

---

## 3. 改造方案

### 3.1 Tool 层变更

#### 改造前（2 个 Tool）

| Tool | 入参 | 返回 |
|------|------|------|
| `engineering_facts` | `source_location` + `construction_unit` | `{status, artifact, engineering_facts, summary, diagnostics}` |
| `report_generation` | `operation` + prepare/finalize 混合参数 | `{status, artifact/artifacts, markdown_content, summary, diagnostics}` |

#### 改造后（3 个 Tool）

| Tool | 入参 | 返回 | UI |
|------|------|------|-----|
| `engineering_facts` | `source_location` + 可选 `construction_unit` | `{status, data: {artifact, summary, error}, warnings}` | 工程事实 UI |
| `report_prepare` | `engineering_facts_path` + 可选 `project_name` | `{status, data: {artifact, summary, error}, warnings}` | 无 |
| `report_finalize` | `work_package_path` + 可选 `work_results_path` | `{status, data: {artifacts, manifest_path, summary, error}, warnings}` | 原“报告编写与生成”UI |

三个 Tool 的业务参数均不超过 2 个，无需再增加 `inp` 外层包装。`report_prepare` 和 `report_finalize` 不再暴露 `operation` 参数，也不得在 `mcp_server` 中映射到旧的 `src/report_generation/core.py` 路由器。每个公开 Tool 必须调用自己的 `src` 核心入口。

目标结构：

```text
duck/
├── content.py
└── host_client.py

src/
├── engineering_facts/
│   └── core.py
├── report_prepare/
│   └── core.py
├── report_finalize/
│   └── core.py
└── report_shared/
    ├── context_builder.py
    ├── deterministic_builders.py
    ├── exporters.py
    ├── template_loader.py
    ├── rules/
    └── templates/

mcp_server/
├── server.py
└── duck_implement/
    ├── content.py
    └── host_client.py
```

#### 3.1.1 `engineering_facts` 改造

**入参模型**：保持当前直接参数形式，只继续使用 `SourceLocation` 约束嵌套对象，不新增 `inp` 包装。

```python
async def engineering_facts(
    ctx: Context,
    source_location: SourceLocation,
    construction_unit: str | None = None,
) -> ToolResult:
    ...
```

`construction_unit` 未提供时按空值处理；当前业务核心不会自动推断建设单位，description 不得写成“从工程事实中推断”。

**返回信封**：

```python
{
    "status": "completed" | "failed",
    "data": {
        "artifact": {
            "path": "runs/2026-09-08T10-30-00Z-<uuid>/engineering_facts.json",
            "media_type": "application/json",
            "schema_version": "2.0"
        },
        "summary": {"source_count": 3, "equipment_count": 5, ...},
        "error": null
    },
    "warnings": [{"level": "warning", "code": "...", "message": "..."}]
}
```

**关键变化**：
- 模型可见的 `structured_content` 不再包含完整 `engineering_facts`，只包含产物引用、摘要、错误和 warnings。
- 为保持现有工程事实 UI 不变，完整 `engineering_facts` 通过 progress 通知的 `uiEvent.final_result.engineering_facts` 传给 UI。
- fatal diagnostic 映射为 `data.error`，非 fatal diagnostic 映射为 `warnings`；不得把 fatal 当作 warning。
- `local_directory` 只在用户传入根目录内枚举并读取受支持源文件；最终 `engineering_facts.json` 必须通过 `HostClient.save_file()` 写入逻辑路径。

#### 3.1.2 `report_prepare`（新 Tool）

**入参模型**：

```python
async def report_prepare(
    ctx: Context,
    engineering_facts_path: str,
    project_name: str | None = None,
) -> ToolResult:
    ...
```

FastMCP 适配层创建 `content` / `host_client` 后，直接调用 `src.report_prepare.core.execute()`。`src.report_prepare` 不接收 `operation`，不依赖旧 `src.report_generation` 路由器。

当前 `ReportContext` 只有 `project_name` 一个字段，因此 MCP 入参只保留 `project_name`。若未来扩展 `ReportContext`，应先单独评审是否增加 MCP 入参，不能静默丢弃字段。

**返回信封**：

```python
{
    "status": "prepared" | "failed",
    "data": {
        "artifact": {
            "path": "runs/2026-09-08T10-30-00Z-<uuid>/work_package.json",
            "media_type": "application/json",
            "schema_version": "1.0"
        },
        "summary": {"research_task_count": 3, "writing_task_count": 5, "synthesis_task_count": 1},
        "error": null
    },
    "warnings": [...]
}
```

**不绑定 UI 资源**：`report_prepare` 是中间步骤，在对话流中以文本卡片展示摘要即可，不需要独占 iframe。

**context 提交规则**：
- `report_prepare` 在任何 HostClient 写入前，必须先生成完整 `work_package.json` 内容，并完成 Schema 校验和内部一致性校验；
- `report_prepare` 确认工作包内容有效后，再通过 `HostClient.save_file()` 保存全部章节 context 文件；
- 全部 context 保存成功后，最后保存 `work_package.json`；
- `work_package.json` 是这组 context 的提交标志；
- 任何 context 保存失败时，不保存 `work_package.json`；
- 失败或取消时不得返回 `prepared`，不得返回 `data.artifact.path`；
- 由于 HostClient 没有删除能力，失败或取消时允许残留不可达的孤儿 context 文件，但没有 `work_package.json` 指向的 context 不得被 Agent、Skill 或验证脚本视为有效工作包组成部分。

#### 3.1.3 `report_finalize`（新 Tool）

**入参模型**：MCP 层只接收工作包和工作结果 JSON 的逻辑路径，不再暴露较大的 inline `work_results`。

```python
async def report_finalize(
    ctx: Context,
    work_package_path: str,
    work_results_path: str | None = None,
) -> ToolResult:
    ...
```

FastMCP 适配层创建 `content` / `host_client` 后，直接调用 `src.report_finalize.core.execute()`。`src.report_finalize` 不接收 `operation`，不依赖旧 `src.report_generation` 路由器。

`work_results_path` 在 MCP Tool 中保持可选。未提供时沿用既有业务语义：继续生成由模板 fallback 兜底的未闭合草稿，并返回明确 warning，例如 `WORK_RESULTS_NOT_PROVIDED`。这不是完整交付，只能作为草稿进入 UI 和 Skill 流程。

此处 `completed` 只表示 Markdown/DOCX 与通过校验的 manifest 已成组提交成功，不代表报告达到正式交付条件。只要存在 fallback，UI 和 Skill 都必须将结果表述为“存在未闭合内容的草稿”，不得标记为“数据完整”或“正式终稿”。

**返回信封**：

```python
{
    "status": "completed" | "failed",
    "data": {
        "artifacts": {
            "docx_path": "runs/.../可行性研究报告_初稿.docx",
            "markdown_path": "runs/.../可行性研究报告_初稿.md"
        },
        "manifest_path": "runs/.../report_manifest.json",
        "summary": {"section_count": 20, "fallback_section_count": 3},
        "error": null
    },
    "warnings": [...]
}
```

**关键变化**：
- 模型可见的 `structured_content` 不再包含完整 `markdown_content`。
- 为保持现有报告 UI，不新增文件读取 Tool；完整 Markdown 通过 progress 通知的 `uiEvent.final_result.markdown_content` 传给 UI。
- MCP 层保留可选 `work_results_path`；未提供时生成 fallback 未闭合草稿并返回 warning。
- Markdown、DOCX 和 manifest 使用 HostClient 写入；manifest 最后写入，是报告成组有效性的唯一提交标志。

#### 3.1.4 输出 Schema（三个 Tool 通用）

不能只定义输入模型。三个 Tool 必须分别定义明确的 Pydantic `data` 模型和输出信封，并通过 `@mcp.tool(output_schema=...)` 暴露真实 output schema。

通用字段：

```python
class WarningItem(BaseModel):
    level: Literal["info", "warning"] = "warning"
    code: str
    message: str

class ErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False

class ArtifactRef(BaseModel):
    path: str
    media_type: str
    schema_version: str | None = None

class ReportArtifacts(BaseModel):
    docx_path: str
    markdown_path: str

class EmptySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

`ArtifactRef` 用于 `engineering_facts.json`、`work_package.json` 这类单一 JSON 产物。`report_finalize` 使用 `ReportArtifacts` 返回 DOCX、Markdown 的逻辑路径，并在 `data.manifest_path` 中返回提交清单路径字符串；不定义额外的 manifest 引用模型。

每个 Tool 分别定义成功/失败模型，并用 `status` 作为判别字段导出联合 Schema。不得继续使用通用 `dict[str, Any]`，也不得让无效状态组合通过校验：

```python
class EngineeringFactsSuccessData(BaseModel):
    artifact: ArtifactRef
    summary: EngineeringFactsSummary
    error: None = None

class EngineeringFactsFailureData(BaseModel):
    artifact: None = None
    summary: EmptySummary = Field(default_factory=EmptySummary)
    error: ErrorInfo

class EngineeringFactsSuccess(BaseModel):
    status: Literal["completed"]
    data: EngineeringFactsSuccessData
    warnings: list[WarningItem] = Field(default_factory=list)

class EngineeringFactsFailure(BaseModel):
    status: Literal["failed"]
    data: EngineeringFactsFailureData
    warnings: list[WarningItem] = Field(default_factory=list)

EngineeringFactsOutput = Annotated[
    EngineeringFactsSuccess | EngineeringFactsFailure,
    Field(discriminator="status"),
]
```

`report_prepare` 和 `report_finalize` 采用相同结构分别定义自己的联合输出。必须保证：

- `completed`/`prepared`：业务产物必填，`error` 必须为 `null`；
- `failed`：`data` 中的产物必须为 `null`、summary 为空对象、`error` 必填；
- Pydantic 校验和 MCP 导出的 `outputSchema` 都能拒绝交叉组合。

fatal diagnostic 映射到 `data.error`；warning/info diagnostics 进入 `warnings`。

MCP 返回使用 FastMCP `ToolResult` 仅承载模型可见信封，UI 大字段由 progress 通知承载：

```python
await _send_progress_with_data(
    ctx,
    100,
    100,
    "任务已完成",
    {"final_result": ui_final_result},
)
return ToolResult(
    structured_content=envelope.model_dump(),
)
```

#### 3.1.5 `report_manifest` Schema

新增 `schemas/report_manifest.schema.json`，作为报告产物组的提交清单契约。manifest 使用逻辑 path，不使用本地绝对路径。

字段写死为：

```json
{
  "schema_version": "1.0",
  "created_at": "2026-09-08T10:30:00Z",
  "markdown": {
    "path": "runs/.../可行性研究报告_初稿.md",
    "sha256": "<64 hex chars>",
    "size_bytes": 12345,
    "media_type": "text/markdown"
  },
  "docx": {
    "path": "runs/.../可行性研究报告_初稿.docx",
    "sha256": "<64 hex chars>",
    "size_bytes": 67890,
    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  },
  "summary": {
    "section_count": 20,
    "fallback_section_count": 3
  }
}
```

约束：

- `schema_version` 固定为 `"1.0"`；
- 同一次 finalize 仅靠内部唯一逻辑路径前缀关联；内部时间戳 + UUID 不写入 manifest 字段，也不作为 MCP 参数或返回独立字段；
- `markdown.path`、`docx.path` 和 `manifest_path` 必须位于同一个内部唯一逻辑路径前缀下；
- `sha256` 必须是对应本地临时文件上传前计算出的 64 位十六进制哈希；
- `size_bytes` 必须是对应本地临时文件上传前的字节数，且大于 0；
- manifest 保存前必须先用 `schemas/report_manifest.schema.json` 校验；
- 验收不能只检查“manifest 存在”，必须校验 manifest Schema、路径关联、hash 和 size。

#### 3.1.6 取消检查点（三个 Tool 通用）

```python
import threading
from fastmcp.exceptions import ToolError

async def engineering_facts(...) -> ToolResult:
    cancel_event = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        execute_engineering_facts,
        request,
        content=content,
        host_client=host_client,
        cancel_event=cancel_event,
    ))

    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError as cancelled:
        cancel_event.set()
        try:
            await asyncio.shield(worker)
        except OperationCancelled:
            pass
        except Exception:
            logger.exception("核心任务在取消清理期间异常退出")
        raise cancelled
    except OperationCancelled as exc:
        raise asyncio.CancelledError() from exc
    except Exception as exc:
        raise ToolError("工程事实整理出现未预期内部错误") from exc
```

必须同时满足三个细节：

- `worker` 必须显式保存，不能直接裸 `await asyncio.to_thread(...)`；
- 首次等待使用 `asyncio.shield()`，避免外层取消把 worker 包装任务一并取消；
- 收到取消后先设置 Event，再等待 worker 完成清理，最后传播原始 `CancelledError`。清理期间的内部异常只写服务端日志，不能覆盖客户端取消语义。

只修改 MCP 外壳不够。三个业务核心的 `execute()` 都要接收实际需要的 duck 能力和向后兼容的可选取消参数，并用专用异常穿过现有的宽泛异常边界：

```python
class OperationCancelled(Exception):
    pass

def execute(
    request,
    *,
    content: Content,
    host_client: HostClient,
    cancel_event: threading.Event | None = None,
):
    try:
        ...
        if cancel_event and cancel_event.is_set():
            raise OperationCancelled()
        ...
    except OperationCancelled:
        raise
    except ExpectedBusinessError as exc:
        return failed_result(exc)
```

核心中任何 `except Exception` 之前都必须先透传 `OperationCancelled`，否则取消会被误包装成 `status="failed"`。

并在真实耗时边界检查：

| Tool | 检查点 |
|------|--------|
| `engineering_facts` | 来源发现前后、每个来源文件读取前后、事实组装前后、最终 JSON 写入前 |
| `report_prepare` | 工程事实读取前后、章节循环顶部、章节 context 写入前、工作包写入前 |
| `report_finalize` | 工作包/结果读取前后、章节循环顶部、本地临时 Markdown/DOCX 导出前后、HostClient 写入前、manifest 写入前 |

在正式产物提交点之前观察到取消时，不得发布未完成产物，临时 `.tmp` 文件应清理。`CancelledError` 必须重新抛出，不能返回正常信封，否则 MCP 框架可能二次响应。外层协程取消后，后台线程应在下一个检查点协作退出；测试必须等待该线程退出后再检查目录，而不是只断言外层协程已取消。

DOCX 与 Markdown 必须作为一组报告产物发布，但现有 `HostClient` 没有事务、删除或跨文件原子提交能力，因此不能虚称“整组原子写入”。本方案采用通过 Schema 校验的 manifest 作为唯一有效提交标志：

1. 在本地临时目录导出 Markdown 和 DOCX；
2. 校验两个本地临时文件均完整可读，并计算 `sha256`、`size_bytes`；
3. 在 HostClient 写入前检查取消信号；若已取消，清理本地临时文件并抛出 `OperationCancelled`；
4. 通过 `HostClient.save_file()` 将 Markdown 和 DOCX 写入同一个唯一逻辑路径前缀下；
5. 上传后通过 `HostClient.get_file()` 回读 Markdown 和 DOCX，重新核对 `sha256` 与 `size_bytes`；
6. 回读核验通过后生成 manifest，校验 `schemas/report_manifest.schema.json`、路径关联、hash 和 size；
7. 两个文件写入成功、回读核验成功且 manifest 校验通过后，最后通过 `HostClient.save_file()` 写入 `report_manifest.json`；
8. 只有 manifest 写入成功，核心才返回 `status="completed"`、`artifacts` 和 `manifest_path`，UI/Skill 才能把这组报告视为有效结果。

取消/异常规则：

- manifest 写入前观察到取消：不写 manifest，不返回 `completed`；
- Markdown 或 DOCX 已经通过 HostClient 写入、但 manifest 未写入时，允许留下不可达的孤儿文件，因为 HostClient 无删除能力；
- 没有 manifest 的孤儿文件不得被 UI、Skill、验证脚本或 Agent 视为有效交付；
- HostClient 写入 Markdown/DOCX 或 manifest 失败时，核心抛内部存储异常，MCP 层转为 `ToolError`，不返回 `completed`；
- 上传后 `HostClient.get_file()` 回读失败属于 HostClient 存储故障，核心抛内部存储异常，MCP 层转为 `ToolError`；
- 上传后回读内容的 `sha256` 或 `size_bytes` 与本地临时文件不匹配，视为内部存储完整性错误，核心抛内部存储异常，不写 manifest，MCP 层转为 `ToolError`；
- manifest 写入成功后才是提交点，之后取消不回滚已完成结果。

每个生产 Tool 调用入口继续使用 `make_host_client(ctx)`，不改成全局或静态 client，也不能创建后丢弃。三核心都要向宿主工作区输出或读取产物，因此 `host_client` 是必需依赖；三核心都要报告真实阶段进度，因此 `content` 也是必需依赖。只有 `cancel_event` 保持可选。

### 3.1.7 duck 真实注入

参考 `.trae/skills/build_mcp-server/business-mcp-demo/mcp_server/duck_implement`，本项目采用同样边界：

- `duck/content.py` 和 `duck/host_client.py` 是业务层可依赖的协议；
- `mcp_server/duck_implement/content.py` 和 `mcp_server/duck_implement/host_client.py` 是 MCP 环境下的具体实现；
- `mcp_server/server.py` 在每次 Tool 调用内创建实现对象，并传入 `src` 核心；
- `src` 只能看到 `Content` / `HostClient` 协议，不能 import FastMCP、Context、ToolResult、ToolError 或 `mcp_server.duck_implement`。

示意：

```python
content = MCPContent(ctx, asyncio.get_running_loop())
host_client = make_host_client(ctx)

result = execute_report_prepare(
    request,
    content=content,
    host_client=host_client,
    cancel_event=cancel_event,
)
```

不能出现这种形式：

```python
make_host_client(ctx)  # 返回值未使用
result = execute_report_prepare(request)
```

能力注入规则：

- 三个核心都接收 `content`，用于真实阶段进度和必要的 UI 中间事件；
- 三个核心都接收 `host_client`，用于读取/保存宿主工作区产物；
- `source_location.provider=local_directory` 的目录枚举仍可使用本地文件系统；
- Tool 间产物传递和报告最终产物必须走 `HostClient` 逻辑路径。

#### 3.1.8 错误分类

| 场景 | 对外语义 | 处理方式 |
|---|---|---|
| MCP 参数缺失、类型错误 | 协议校验失败 | 交给 Pydantic/FastMCP 返回参数校验错误，不进入业务核心 |
| 用户提供的逻辑路径不存在、路径指向内容不是预期 JSON/二进制、输入内容或 Schema 无效、不支持的 provider、源文件缺失、工作包或工作结果无效 | 可预期业务失败 | 返回 `status="failed"` 信封和稳定 `data.error.code`；可由用户修正时 `retryable=true` |
| HostClient 鉴权失败、连接失败、超时、存储服务 5xx、`save_file`/`get_file` 存储服务异常、未分类内部异常 | 内部系统异常 | 核心抛内部存储异常或内部异常，MCP 层记录服务端日志并转为 `ToolError`；禁止返回 `status="failed"` |
| 客户端取消 | 已取消 | 设置取消信号，核心协作退出，对外传播 `CancelledError`，不返回失败信封 |

### 3.2 UI 层变更

**页面零改动，结果协议适配层最小修改。**

- `EngineeringFactsPage.tsx`、`ReportGenerationPage.tsx` 的布局、组件和业务展示保持不变。
- 两个 UI 的 `core/mcpApp.ts` 在 `useNormalizedToolResult()` 中读取 `structuredContent.data` 和 `progress.uiEvent.final_result`，合并成页面当前使用的扁平 view model。
- `warnings` 与 `data.error` 在归一化层映射为页面现有的 `diagnostics`，页面无需重写。
- `report_prepare` 不绑定 UI，在对话流中展示模型可见摘要。
- 不新增 `read_artifact`、`read_file` 或任何 UI 反向文件读取 Tool。

概念映射：

```typescript
return {
  ...envelope.data,
  engineering_facts: progressFinalResult.engineering_facts,
  markdown_content: progressFinalResult.markdown_content,
  status: envelope.status,
  diagnostics: [...warnings, ...(error ? [{ level: 'fatal', ...error }] : [])],
};
```

实现时不能展开整个 `final_result` 覆盖结构化结果。只允许白名单读取 `engineering_facts`、`markdown_content` 两个大字段；状态、产物路径、摘要、warnings 和 error 始终以 `structuredContent` 为准。不同页面只取自己需要的白名单字段。

由于 progress 扩展字段的转发取决于实际 MCP Host，落地前必须用代表性数据完成集成验证：

- UI 能收到 `progress.uiEvent.final_result`，Agent 模型上下文不包含完整大字段；
- 约 780 KB 的工程事实和约 42 KB 的 Markdown 不会被 Host 截断；
- 无 UI 客户端仍能仅凭 `structuredContent` 和 Host 已有文件系统能力完成链路。

允许先完成代码实现和本地测试，但在真实 Host 完成上述验证前，不得宣布最终验收、不得发布为可交付版本。若真实 Host 不满足 progress 扩展字段转发、不截断或 E2E 链路要求，应暂停最终验收并单独评审大结果传输方案；不能在严格新信封中临时塞回旧顶层字段，也不能因此新增 `read_artifact` 或 `read_file`。只有另行明确内联字段在成功 `data` 模型中的 Schema 和 UI 归一化协议后，才能采用内联方案。

### 3.3 Skill 层变更

`SKILL.md` 和 `skill.yaml` 中 `tools` 声明从 2 个变为 3 个：

```yaml
# skill.yaml
tools:
  - engineering_facts
  - report_prepare
  - report_finalize
```

`SKILL.md` 状态闭环表更新：

| 状态 | Agent 动作 | 完成条件 |
|------|-----------|----------|
| `engineering_facts.status=failed` | 展示 `data.error` 和 `warnings`，修正后重试 | 返回 `completed` |
| `engineering_facts.status=completed` | 核对 `data.artifact.path`、`data.summary` | 可进入 `report_prepare` |
| `report_prepare.status=failed` | 展示 `data.error` 和 `warnings`，修正后重试 | 返回 `prepared` |
| `report_prepare.status=prepared` | Host Agent 用已有文件系统能力读取 `data.artifact.path` 指向的 `work_package.json`，按 `research_tasks`/`writing_tasks`/`synthesis_tasks` 完成工作 | 进入编写，或显式选择跳过工作结果生成 fallback 草稿 |
| `report_finalize.status=failed` | 展示 `data.error` 和 `warnings`，修正后重试 | 返回 `completed` |
| `report_finalize.status=completed` 且 `fallback_section_count=0` | 核对 `data.artifacts`、`data.manifest_path` 和摘要并作为完整报告交付 | manifest 通过 Schema、路径关联、hash、size 校验，两个文件存在且无 fallback |
| `report_finalize.status=completed` 且 `fallback_section_count>0` | 通过现有 summary/diagnostics 明确标注“未闭合草稿”，交付草稿并继续补充工作结果 | manifest 通过 Schema、路径关联、hash、size 校验，两个文件存在，fallback 数量已披露 |

### 3.4 业务层（`src/`）变更

业务算法保持不变，但业务核心边界必须重划。迁移完成后的有效入口只有：

```text
src.engineering_facts.core.execute(...)
src.report_prepare.core.execute(...)
src.report_finalize.core.execute(...)
```

`src/report_generation/core.py` 的 `operation` 路由器属于被替代文件。迁移并全仓清除旧引用后，删除整个 `src/report_generation/` 目录，不保留空包或兼容 `__init__.py`。`context_builder.py`、`deterministic_builders.py`、`template_loader.py`、`exporters.py`、`rules/`、`templates/` 等 prepare/finalize 共用算法移动到 `src/report_shared/`。移动只改变模块归属和 import 路径，不改变算法规则。

三个核心职责：

| 核心 | 职责 | 主要依赖 |
|---|---|---|
| `src.engineering_facts` | 从 `source_location` 识别工程事实，保存 `engineering_facts.json` | 本地目录枚举、`Content`、`HostClient` |
| `src.report_prepare` | 读取工程事实，构造 Agent 编写工作包，保存 `work_package.json` | `HostClient`、`Content`、`report_shared` |
| `src.report_finalize` | 读取工作包和工作结果，导出 Markdown/DOCX/manifest | `HostClient`、`Content`、`report_shared` |

接口原则：

- 三个核心均为同步 `def execute(...)`，不改成 `async def`；
- 核心必须接收 `content: Content`、`host_client: HostClient`，并可接收 `cancel_event: threading.Event | None`；
- 核心返回原有业务结果语义，统一信封转换仍在 `mcp_server/server.py`；
- 核心不 import FastMCP、Context、ToolResult、ToolError 或 `mcp_server`；
- `report_prepare` / `report_finalize` 请求模型不再包含 `operation`；
- 本地脚本或单元测试注入 fake/local `Content` 与 fake/local `HostClient` adapter，不需要启动 MCP Server。

理由：

- 每个公开 Tool 都有独立可测试的业务入口；
- FastMCP 层只负责协议适配、取消桥接、信封转换和 UI payload；
- 共享报告算法进入 `report_shared` 后，prepare/finalize 不复制逻辑；
- `threading.Event | None` 只提供通用协作式停止能力，不改变计算规则；
- 内部时间戳 + UUID 目录机制继续存在，但不作为公开字段返回。

---

## 4. 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `duck/content.py` | 新增 | 放置业务层可依赖的 Content 协议 |
| `duck/host_client.py` | 新增 | 放置业务层可依赖的 HostClient 协议 |
| `mcp_server/duck_implement/content.py` | 保留并校验 | MCPContent 作为 Content 的 MCP 实现 |
| `mcp_server/duck_implement/host_client.py` | 保留并校验 | `make_host_client(ctx)` 与 MCPHostClient，按 `.trae` demo 模式实现 |
| `mcp_server/server.py` | 局部改造 | 拆 Tool、Pydantic 输入/输出 Schema、统一信封、ToolResult metadata、取消桥接、ToolError、真实 duck 注入 |
| `src/engineering_facts/core.py` | 改造 | 独立核心入口，增加 duck 能力和取消检查点，不改事实算法 |
| `src/report_prepare/core.py` | 新增 | prepare 独立核心入口，不接收 `operation` |
| `src/report_finalize/core.py` | 新增 | finalize 独立核心入口，不接收 `operation`，负责报告成组提交和 manifest |
| `src/report_shared/` | 新增并迁移 | 承接 prepare/finalize 共享的上下文构造、确定性构建器、模板、规则和导出逻辑 |
| `src/report_generation/` | 迁移后删除整个目录 | 迁移并全仓清除旧引用后删除整个目录，不保留空包或兼容 `__init__.py` |
| `SKILL.md` | 编辑 | 更新 tool 声明、状态闭环表、description |
| `skill.yaml` | 编辑 | `tools` 列表更新 |
| `README.md` | 编辑 | 更新公开 Tool 清单和调用链 |
| `schemas/report_work_package.schema.json` | 编辑 | 工程事实引用字段改为 `engineering_facts.path`；写作任务上下文字段改为 `context_path` |
| `schemas/report_work_results.schema.json` | 复核 | 当前未发现需要迁移的旧路径字段；保持结果块 schema，验证无需迁移 |
| `schemas/engineering_facts.schema.json` | 复核 | 当前为工程事实内容 schema，不承载 Tool 产物引用；验证无需迁移 |
| `schemas/report_manifest.schema.json` | 新增 | 定义 manifest 的 `schema_version`、Markdown/DOCX 逻辑 path、sha256、size_bytes、media_type 和 summary |
| `scripts/run_report_generation.py` | 改造 | 从旧 `src.report_generation.execute(operation=...)` 改为调用 `src.report_prepare` + `src.report_finalize`；默认仍允许不传 `work_results_path` 生成 fallback 草稿 |
| `scripts/run_engineering_facts.py` | 改造 | 注入本地 `Content` / `HostClient` adapter，输出逻辑路径而非旧 artifact 引用字段 |
| `skills/feasibility-report-generation/scripts/validate_output.py` | 改造 | 从检查输出目录改为检查 `report_manifest.json` 指向的 Markdown/DOCX 逻辑路径 |
| `skills/feasibility-report-generation/scripts/package_result.py` | 改造 | 打包入口改为 manifest；不得把无 manifest 的孤儿文件打包为有效交付 |
| `scripts/local_adapters.py` | 新增 | 提供 CLI 用本地 `Content` 与本地 `HostClient` adapter，不放进业务 `src` |
| `tests/fakes.py` | 新增 | 提供单测用 fake `Content` 与 fake `HostClient` adapter，不放进业务 `src` |
| 工程事实 UI 的 `ui/src/core/mcpApp.ts` | 小改 | 解包信封并白名单读取 `progress.uiEvent.final_result.engineering_facts`；页面组件不改 |
| 报告 UI 的 `ui/src/core/mcpApp.ts` | 小改 | 将 UI 绑定从旧 `report_generation` 结果切到 `report_finalize` 结果；白名单读取 `progress.uiEvent.final_result.markdown_content`；页面组件不改 |
| 两个 UI 的 `host/src/mockData.ts` 及必要协议类型 | 编辑 | 同步新 Tool 名称、统一信封和 progress UI 数据 |
| `tests/test_mcp_server_contract.py` | 编辑 | 更新 tools/list、UI 绑定、输入/输出 Schema 断言 |
| `tests/test_engineering_facts_tool.py` | 编辑 | 增加取消检查点与取消后不发布产物测试 |
| `tests/test_report_prepare_tool.py` | 新增 | 覆盖 prepare 独立核心、HostClient 读写、取消、错误映射 |
| `tests/test_report_finalize_tool.py` | 新增 | 覆盖 finalize 独立核心、HostClient 读写、manifest、取消、错误映射 |
| `tests/test_report_generation_tool.py` | 迁移后删除 | 旧二合一 report 测试拆到 prepare/finalize 后删除，不保留旧核心测试文件 |
| 两个 UI/Host 的 dist | 重新构建 | 同步实际被 Server/Host 使用的构建产物，不手工编辑 |

---

## 5. 兼容性说明

- **破坏性变更**：模型可见返回从顶层平铺结构变为 `{status, data, warnings}`，旧调用方需要适配
- **Tool 名称变更**：`report_generation` 不再存在，拆为 `report_prepare` + `report_finalize`
- **MCP 输入变更**：公开路径字段统一使用 `*_path`；`report_finalize` 保留可选 `work_results_path`，不再公开 inline `work_results`
- **UI 兼容**：页面代码和展示不变；公共结果归一化层读取 `data` 和 `progress.uiEvent.final_result`
- **业务层变更**：不再保留 prepare/finalize 二合一路由器；三个 Tool 分别调用三个 `src` 核心入口
- **产物兼容**：内部唯一目录保留；对外只返回 HostClient 逻辑路径，不新增显式 `run_id`
- **Tool 数量边界**：不新增 `read_artifact`、`read_file` 或其他文件读取 Tool

---

## 6. 改造前后对比速查

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| Tool 数量 | 2 | 3 |
| 返回格式 | 字段平铺、output schema 宽松 | `{status, data, warnings}` + 明确 Pydantic output schema |
| 大结果返回 | 与模型结果混在一起 | 模型侧路径/摘要，UI 侧 `progress.uiEvent.final_result` |
| 文件读取 Tool | 无 | 仍然无，不新增 `read_artifact` |
| run_id | 内部唯一目录隐含实现 | 保持内部实现，不新增公开字段 |
| 取消支持 | async task 取消后线程继续 | Event 逐层传入真实阶段检查 |
| 异常处理 | 业务失败与内部错误边界不清 | 业务失败信封；未预期异常 `ToolError` |
| 入参数量 | `report_generation` 6 个顶级参数 | 每个 Tool 2 个业务参数 |
| description | 缺"何时不调用" | 四要素齐全 |
| UI 绑定 | `engineering_facts` → 工程事实 UI / `report_generation` → 报告编写与生成 UI | `engineering_facts` → 工程事实 UI / `report_prepare` → 无 / `report_finalize` → 报告编写与生成 UI |
| 业务核心 | `engineering_facts` + `report_generation` 二合一路由核心 | `engineering_facts` + `report_prepare` + `report_finalize` 三个核心，共享逻辑在 `report_shared` |

---

## 7. 验证标准

### 7.1 MCP 契约

- `tools/list` 只公开 `engineering_facts`、`report_prepare`、`report_finalize`。
- 不存在新增的 `read_artifact`、`read_file` 或其他 app-only 文件 Tool。
- 三个 Tool 的输入 Schema 准确表达 required、可选字段、枚举和 `additionalProperties`。
- `report_finalize` 的输入 Schema 必须是 `work_package_path` 必填、`work_results_path` 可选。
- 三个 Tool 的 output schema 包含明确的 `status`、`data`、`warnings` 和各自 data 字段，不再是任意 object。
- 三个 Tool 的成功/失败联合输出能拒绝 `completed/prepared + error`、`failed + 产物` 等无效组合。
- 每个 Tool description 包含职责、适用场景、返回关键字段、错误和不调用边界。
- 每个生产 Tool 入口继续调用 `make_host_client(ctx)`，并把返回值传入对应 `src` 核心。
- 全仓扫描旧公开字段：所有 `_uri` 后缀字段、旧 artifact 引用字段、旧 context 引用字段不得作为新 MCP/Schema/Skill/README/skills/feasibility-report-generation/scripts/tests 契约残留；历史说明必须明确标注为改造前。
- 全仓扫描 `src.report_generation`、`src/report_generation`、旧 `operation` 路由字段和 `report_generation` 旧核心引用；迁移完成后，除历史说明外不得残留。

### 7.2 返回与错误

- 成功和可预期失败都通过对应 Pydantic 输出 Schema 校验。
- fatal diagnostic 进入 `data.error`，warning diagnostic 进入 `warnings`。
- 未预期异常表现为 MCP `ToolError`，不返回裸 traceback。
- `ToolError` 和失败信封均不泄露 traceback 或不必要的敏感绝对路径。
- `CancelledError` 设置 Event 后重新抛出，不产生第二次 MCP 响应。
- 用户提供的逻辑路径不存在、内容格式错误或 Schema 无效时返回业务失败信封。
- HostClient 鉴权、连接、超时、存储服务异常和 `save_file`/`get_file` 存储异常必须转为 `ToolError`，不得返回 `status="failed"`。

### 7.3 取消

- 业务核心收到已设置 Event 时，在下一个检查点停止。
- prepare/finalize 的章节循环能够中途停止。
- 在工程事实和工作包提交点前取消时，不发布最终工程事实或工作包，也不遗留本地临时 `.tmp`。
- `report_prepare` 的 `work_package.json` 是章节 context 组的提交标志；失败或取消时不得返回 `prepared` 和 `data.artifact.path`，无工作包指向的孤儿 context 不得被视为有效。
- 在报告 manifest 写入前取消或异常时，不返回 `completed`，不写 manifest；允许 HostClient 中已写入但没有 manifest 指向的 Markdown/DOCX 成为不可达孤儿文件。
- 没有 manifest 的 Markdown/DOCX 不得被验证脚本、Skill、UI 或 Agent 当成有效报告交付。
- manifest 存在不等于有效；必须校验 `schemas/report_manifest.schema.json`、Markdown/DOCX 路径关联、sha256 和 size_bytes。
- finalize 上传 Markdown/DOCX 后必须用 `HostClient.get_file()` 回读并重新核对 sha256 和 size_bytes；回读失败或核验不一致时，不写 manifest，不返回 `completed`，MCP 层返回 `ToolError`。
- MCP 集成取消后 session 不崩溃，后台线程在检查点退出。
- 测试等待后台线程退出后，再断言本次运行没有半成品和 `.tmp` 文件。

### 7.4 UI 与回归

- 工程事实页面继续显示采用方案、设备汇总和事实文件信息。
- 报告页面继续显示完整 Markdown、目录、DOCX/Markdown 路径和摘要。
- 页面组件无业务改动，只由归一化层适配信封和 progress UI 数据。
- `提示词.md` 零差异；`EngineeringFactsPage.tsx`、`ReportGenerationPage.tsx` 页面组件零差异。
- 真实 Host 能用返回的工作包路径，通过已有文件系统能力完成 prepare → 编制 → finalize，不依赖任何新增读取 Tool。
- 代表性大数据下 `progress.uiEvent.final_result` 完整到达 UI 且不进入模型上下文；该验证未通过前，允许本地实现和本地测试完成，但不得宣布最终验收或发布。
- 两个 UI 和两个 Host 构建通过，实际使用的 dist 已同步并完成生产构建产物运行时验证。
- 并发运行的内部目录和产物相互隔离，不覆盖、不串读；不增加公开 `run_id`。
- 现有工程事实、报告、Schema、Markdown、DOCX 测试继续通过。
- `schemas/report_work_package.schema.json` 使用 `engineering_facts.path` 和 `context_path` 后，prepare 产物与 schema 校验一致。
- `report_prepare` 必须先保存全部 context，再保存并校验 `work_package.json`；验收必须检查工作包中的每个 `context_path` 都位于同一逻辑路径前缀下并能被 HostClient 读取。
- 新增 `schemas/report_manifest.schema.json` 后，finalize manifest 产物与 schema、路径关联、hash 和 size 校验一致。
- `scripts/run_report_generation.py` 走 `report_prepare` + `report_finalize` 双核心链路，并验证"无 `work_results_path` 时生成 fallback 草稿"的既有语义。
- `scripts/run_engineering_facts.py` 使用本地 adapter 注入 `content` / `host_client`，不依赖 FastMCP。
- `scripts/validate_output.py` 以 manifest 为有效性入口，并校验 manifest Schema、Markdown/DOCX 路径关联、hash 和 size；不按目录存在两个文件就判定有效。
- `scripts/validate_output.py` 使用 HostClient 兼容的本地 adapter 回读 manifest 指向的 Markdown/DOCX，重新计算 sha256 和 size_bytes。
- `scripts/local_adapters.py` 和 `tests/fakes.py` 提供 CLI/测试 adapter；业务 `src` 不包含具体 local/fake adapter。
- MCP 契约测试、编译检查、Skill 校验和 `git diff --check` 通过。
