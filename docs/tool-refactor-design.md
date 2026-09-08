# Tool 层改造设计文档

> 日期：2026-09-08
> 状态：待评审
> 范围：只改 MCP Tool 契约、取消支持及既有 UI 的必要协议适配；不改业务算法和 UI 页面设计。

---

## 1. 背景：为什么要改

### 1.1 规范依据

对照《MCP-Tool 编写规范速览》5 条规则和《Skill 与 Tool 编写示例》，当前两个 Tool 存在以下不符合规范的问题：

| # | 规范要求 | 当前问题 |
|---|---------|---------|
| 1 | 返回统一信封 `{status, data, warnings}` | 没有 `data` 包裹层，字段平铺在顶层 |
| 2 | 大结果落盘、模型侧摘要优先 | `engineering_facts` 和 Markdown 已落盘，但完整对象仍进入模型可见返回；既有 UI 又依赖这些数据 |
| 3 | 入参顶级 ≤5 个 | `report_generation` 有 6 个顶级参数，且 `operation` 路由导致不同分支参数语义混杂 |
| 4 | 协作式取消检查点 | 两个 Tool 都是 `async def` + `asyncio.to_thread` 模式，完全没有 `threading.Event` 取消信号桥接 |
| 5 | Tool description 四要素（职责/场景/返回关键字段/错误/何时不调用） | 缺少"何时不调用"边界说明 |
| 6 | 用 `ToolError` 而非裸异常 | 当前业务核心会把预期异常转换为 `status=failed`，但 MCP 层尚未区分业务失败、未预期异常和取消 |
| 7 | 明确输出 Schema | 两个 Tool 均返回 `dict[str, Any]`，运行时 output schema 只是允许任意字段的 object |

### 1.2 架构问题

`report_generation` 用 `operation` 参数做内部路由（prepare / finalize），两个分支的入参、出参、场景完全不同，耦合在一起违反单一职责：

- **prepare**：需要 `engineering_facts_uri`，产出 `work_package.json`
- **finalize**：需要 `work_package_uri`，产出 DOCX/Markdown

两个分支各有一个 `operation` 下才有效的参数，入参语义在调用时才能确定，对调用方不友好。

---

## 2. 改造目标

1. 返回结构统一为 `{status, data, warnings}` 信封，并为三个 Tool 提供明确的 Pydantic 输出 Schema
2. `report_generation` 拆为 `report_prepare` + `report_finalize` 两个独立 Tool
3. 模型可见结果只返回产物信息和摘要；既有 UI 需要的完整对象通过同一次 Tool Result 的 `_meta.ui_payload` 传递
4. 不新增 `read_artifact`、`read_file` 或其他文件读取 Tool
5. 不新增用户可见或模型可见的 `run_id`；保留当前 `_artifact_dir()` 的唯一运行目录机制
6. 添加真正进入同步业务核心的 `threading.Event` 协作式取消检查点
7. 可预期业务失败返回失败信封；未预期内部异常才使用 `ToolError`
8. description 补全职责、场景、返回、错误和不调用边界，入参细节写进 Field description

### 2.1 运行环境前提

本方案延续当前“本地目录 + 本地文件 URI”的产品形态，要求 MCP Server 与执行编制任务的 Host Agent 处于同一受控文件系统环境。完整链路为：

1. `report_prepare` 返回 `work_package.json` 的 URI；
2. Host Agent 使用自身已有的文件系统能力读取该文件并完成编制任务；
3. Host Agent 将结果写成 `work_results.json`，再把 URI 传给 `report_finalize`。

这里依赖的是 **Host 已有的文件系统读写能力**，不是本 MCP Server 再暴露文件读取 Tool。若部署环境不具备该能力，则当前本地文件 URI 工作流本身不成立，应停止实施并调整部署架构，不能用新增 `read_artifact` 或 `read_file` 来补洞。

---

## 3. 改造方案

### 3.1 Tool 层变更

#### 改造前（2 个 Tool）

| Tool | 入参 | 返回 |
|------|------|------|
| `engineering_facts` | `source_location` + `construction_unit` | `{status, artifact, engineering_facts, summary, diagnostics}` |
| `report_generation` | `operation` + `engineering_facts_uri` + `report_context` + `work_package_uri` + `work_results_uri` + `work_results` | `{status, artifact/artifacts, markdown_content, summary, diagnostics}` |

#### 改造后（3 个 Tool）

| Tool | 入参 | 返回 | UI |
|------|------|------|-----|
| `engineering_facts` | `source_location` + 可选 `construction_unit` | `{status, data: {artifact, summary, error}, warnings}` | engineering-confirmation-ui |
| `report_prepare` | `engineering_facts_uri` + 可选 `project_name` | `{status, data: {artifact, summary, error}, warnings}` | 无（对话流文本卡片） |
| `report_finalize` | `work_package_uri` + 可选 `work_results_uri` | `{status, data: {artifacts, summary, error}, warnings}` | report-generation-ui |

三个 Tool 的业务参数均不超过 2 个，无需再增加 `inp` 外层包装。`report_prepare` 和 `report_finalize` 不再暴露 `operation` 参数，由 MCP 适配层映射到现有业务核心。

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
            "uri": "file:///.../engineering_facts.json",
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
- 为保持现有工程事实 UI 不变，完整 `engineering_facts` 通过同一次 Tool Result 的 `_meta.ui_payload.engineering_facts` 传给 UI。
- fatal diagnostic 映射为 `data.error`，非 fatal diagnostic 映射为 `warnings`；不得把 fatal 当作 warning。

#### 3.1.2 `report_prepare`（新 Tool）

**入参模型**：

```python
async def report_prepare(
    ctx: Context,
    engineering_facts_uri: str,
    project_name: str | None = None,
) -> ToolResult:
    ...
```

MCP 适配层映射为现有业务核心请求：

```python
request = {
    "operation": "prepare",
    "engineering_facts_uri": engineering_facts_uri,
    "report_context": {"project_name": project_name} if project_name else {},
}
```

当前 `ReportContext` 只有 `project_name` 一个字段，因此上述映射没有删减已有业务入参。若未来扩展 `ReportContext`，应先单独评审是否增加 MCP 入参，不能静默丢弃字段。

**返回信封**：

```python
{
    "status": "prepared" | "failed",
    "data": {
        "artifact": {
            "uri": "file:///.../work_package.json",
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

#### 3.1.3 `report_finalize`（新 Tool）

**入参模型**：MCP 层只接收工作结果 JSON 的 URI，不再暴露较大的 inline `work_results`。现有业务核心可继续保留 inline 能力，供本地 Python 调用和单元测试使用。

```python
async def report_finalize(
    ctx: Context,
    work_package_uri: str,
    work_results_uri: str | None = None,
) -> ToolResult:
    ...
```

未提供 `work_results_uri` 时继续沿用当前 fallback 行为，并返回明确 warning。MCP 适配层把两个参数映射为内部 `operation="finalize"` 请求。

此处 `completed` 只表示“本次导出动作已完成”，不代表数据已闭合，也不代表报告达到正式交付条件。只要存在 fallback，UI 和 Skill 都必须将结果表述为“存在未闭合内容的草稿”，不得标记为“数据完整”或“正式终稿”。

**返回信封**：

```python
{
    "status": "completed" | "failed",
    "data": {
        "artifacts": {
            "docx": "file:///.../可行性研究报告_初稿.docx",
            "markdown": "file:///.../可行性研究报告_初稿.md"
        },
        "summary": {"section_count": 20, "fallback_section_count": 3},
        "error": null
    },
    "warnings": [...]
}
```

**关键变化**：
- 模型可见的 `structured_content` 不再包含完整 `markdown_content`。
- 为保持现有报告 UI，不新增文件读取 Tool；完整 Markdown 通过同一次 Tool Result 的 `_meta.ui_payload.markdown_content` 传给 UI。
- MCP 层只保留 `work_results_uri`；不提供时继续生成带 fallback 告警的不完整初稿。

#### 3.1.4 输出 Schema（三个 Tool 通用）

不能只定义输入模型。三个 Tool 必须分别定义明确的 Pydantic `data` 模型和输出信封，并通过函数返回注解或 `@mcp.tool(output_schema=...)` 暴露真实 output schema。

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
    uri: str
    media_type: str
    schema_version: str | None = None

class ReportArtifacts(BaseModel):
    docx: str
    markdown: str

class EmptySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

`ArtifactRef` 用于 `engineering_facts.json`、`work_package.json` 这类单一 JSON 产物。`report_finalize` 为保持现有页面契约，使用 `ReportArtifacts` 返回 DOCX 和 Markdown 的 URI 字符串，不与 `ArtifactRef` 混用。

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

MCP 返回使用 FastMCP `ToolResult` 分离模型数据与 UI 数据：

```python
return ToolResult(
    structured_content=envelope.model_dump(),
    meta={"ui_payload": ui_payload},
)
```

#### 3.1.5 取消检查点（三个 Tool 通用）

```python
import threading
from fastmcp.exceptions import ToolError

async def engineering_facts(...) -> ToolResult:
    cancel_event = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        execute_engineering_facts,
        request,
        artifact_dir,
        cancel_event,
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

只修改 MCP 外壳不够。两个业务核心的 `execute()` 都要增加向后兼容的可选参数，并用专用异常穿过现有的宽泛异常边界：

```python
class OperationCancelled(Exception):
    pass

def execute(request, artifact_dir, cancel_event: threading.Event | None = None):
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
| `report_finalize` | 工作包/结果读取前后、章节循环顶部、Markdown/DOCX 导出前后、最终 `os.replace` 前 |

在正式产物提交点之前观察到取消时，不得发布未完成产物，临时 `.tmp` 文件应清理。`CancelledError` 必须重新抛出，不能返回正常信封，否则 MCP 框架可能二次响应。外层协程取消后，后台线程应在下一个检查点协作退出；测试必须等待该线程退出后再检查目录，而不是只断言外层协程已取消。

DOCX 与 Markdown 必须作为一组发布，并定义唯一提交点：

1. 两个文件都先写入本次运行目录的临时名称；
2. 校验两个临时文件均完整可读；
3. 在进入提交段前最后检查一次取消信号；若已取消，删除整组临时文件并抛出 `OperationCancelled`；
4. 进入提交段后不再检查取消信号，连续完成两个 `os.replace`；
5. 只有两个正式文件都发布成功，核心才返回 artifacts，UI/Skill 才能把它们视为有效结果。

取消的线性化时点就是第 3 步：在此之前取消，保证不发布正式产物；进入第 4 步后才收到取消，则完成整组提交，并按“已经完成的原子发布不回滚”处理。若任一 replace 因系统错误失败，清理本次运行中可清理的半组文件并抛出内部异常，不返回 artifacts。

每个生产 Tool 调用入口继续使用 `make_host_client(ctx)`，不改成全局或静态 client。

#### 3.1.6 错误分类

| 场景 | 对外语义 | 处理方式 |
|---|---|---|
| MCP 参数缺失、类型错误 | 协议校验失败 | 交给 Pydantic/FastMCP 返回参数校验错误，不进入业务核心 |
| 不支持的 provider、源文件缺失、JSON/Schema 无效、工作包或工作结果无效 | 可预期业务失败 | 返回 `status="failed"` 信封和稳定 `data.error.code`；可由用户修正时 `retryable=true` |
| 输出目录权限失败、原子写失败、未分类内部异常 | 系统异常 | 记录服务端日志并抛出 `ToolError`，不得把 traceback 或敏感绝对路径返回给模型/UI |
| 客户端取消 | 已取消 | 设置取消信号，核心协作退出，对外传播 `CancelledError`，不返回失败信封 |

### 3.2 UI 层变更

**页面零改动，结果协议适配层最小修改。**

- `EngineeringFactsPage.tsx`、`ReportGenerationPage.tsx` 的布局、组件和业务展示保持不变。
- 两个 UI 的 `core/mcpApp.ts` 在 `useNormalizedToolResult()` 中读取 `structuredContent.data` 和 `_meta.ui_payload`，合并成页面当前使用的扁平 view model。
- `warnings` 与 `data.error` 在归一化层映射为页面现有的 `diagnostics`，页面无需重写。
- `report_prepare` 不绑定 UI，在对话流中展示模型可见摘要。
- 不新增 `read_artifact`、`read_file` 或任何 UI 反向文件读取 Tool。

概念映射：

```typescript
return {
  ...envelope.data,
  engineering_facts: uiPayload.engineering_facts,
  markdown_content: uiPayload.markdown_content,
  status: envelope.status,
  diagnostics: [...warnings, ...(error ? [{ level: 'fatal', ...error }] : [])],
};
```

实现时不能展开整个 `ui_payload` 覆盖结构化结果。只允许白名单读取 `engineering_facts`、`markdown_content` 两个大字段；状态、产物 URI、摘要、warnings 和 error 始终以 `structuredContent` 为准。不同页面只取自己需要的白名单字段。

由于 `_meta` 的传输和可见性取决于实际 MCP Host，落地前必须用代表性数据完成集成验证：

- UI 能收到 `_meta.ui_payload`，Agent 模型上下文不包含它；
- 约 780 KB 的工程事实和约 42 KB 的 Markdown 不会被 Host 截断；
- 无 UI 客户端仍能仅凭 `structuredContent` 和 Host 已有文件系统能力完成链路。

若真实 Host 不满足上述条件，本轮改造暂停，保留改造前的现有实现并单独评审大结果传输方案；不能在严格新信封中临时塞回旧顶层字段，也不能因此新增 `read_artifact`。只有另行明确内联字段在成功 `data` 模型中的 Schema 和 UI 归一化协议后，才能采用内联方案。该验证完成前不实施 `_meta` 搬迁。

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
| `engineering_facts.status=completed` | 核对 `data.artifact.uri`、`data.summary` | 可进入 `report_prepare` |
| `report_prepare.status=failed` | 展示 `data.error` 和 `warnings`，修正后重试 | 返回 `prepared` |
| `report_prepare.status=prepared` | Host Agent 用已有文件系统能力读取 `work_package.json`，按 `research_tasks`/`writing_tasks`/`synthesis_tasks` 完成工作 | `work_results.json` 满足 schema |
| `report_finalize.status=failed` | 展示 `data.error` 和 `warnings`，修正后重试 | 返回 `completed` |
| `report_finalize.status=completed` 且 `fallback_section_count=0` | 核对 DOCX、Markdown 和摘要并作为完整报告交付 | 两个文件存在且无 fallback |
| `report_finalize.status=completed` 且 `fallback_section_count>0` | 通过现有 summary/diagnostics 明确标注“未闭合草稿”，交付草稿并继续补充工作结果 | 两个文件存在，fallback 数量已披露 |

### 3.4 业务层（`src/`）变更

**业务算法不变，接口只增加取消参数。** `execute()` 的既有 request 和业务返回结构保持现状，信封转换仍在 `mcp_server/server.py` 完成；为使取消真正生效，两个核心增加默认值为 `None` 的 `cancel_event` 参数和阶段检查点。

`src/engineering_facts/core.py` 与 `src/report_generation/core.py` 是本方案唯一有效的业务核心来源，当前 `mcp_server/server.py` 也直接导入二者。不得重新接入旧的四阶段 runner、历史兼容包装或其他同名实现。

理由：
- 业务层不依赖 MCP Context、ToolResult 或 ToolError，仍可独立测试。
- `threading.Event | None` 只提供通用协作式停止能力，不改变计算规则。
- 现有本地脚本和测试不传该参数时，行为保持不变。
- 现有 `_artifact_dir()` 的时间戳 + UUID 唯一目录机制保持不变，不额外引入或暴露 `run_id`。

---

## 4. 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `mcp_server/server.py` | 局部改造 | 拆 Tool、Pydantic 输入/输出 Schema、统一信封、ToolResult metadata、取消桥接、ToolError |
| `src/engineering_facts/core.py` | 小改 | 增加可选 `cancel_event` 和阶段检查点，不改事实算法 |
| `src/report_generation/core.py` | 小改 | 增加可选 `cancel_event` 和 prepare/finalize 检查点，不改报告算法 |
| `SKILL.md` | 编辑 | 更新 tool 声明、状态闭环表、description |
| `skill.yaml` | 编辑 | `tools` 列表更新 |
| `README.md` | 编辑 | 更新公开 Tool 清单和调用链 |
| 两个 UI 的 `ui/src/core/mcpApp.ts` | 小改 | 解包信封并合并 `_meta.ui_payload`；页面组件不改 |
| 两个 UI 的 `host/src/mockData.ts` 及必要协议类型 | 编辑 | 同步新 Tool 名称、统一信封和 UI payload |
| `tests/test_mcp_server_contract.py` | 编辑 | 更新 tools/list、UI 绑定、输入/输出 Schema 断言 |
| `tests/test_engineering_facts_tool.py` | 编辑 | 增加取消检查点与取消后不发布产物测试 |
| `tests/test_report_generation_tool.py` | 编辑 | 增加 prepare/finalize 取消与错误映射测试 |
| `src/report_generation/context_builder.py` | 不变 | — |
| `src/report_generation/deterministic_builders.py` | 不变 | — |
| `src/report_generation/template_loader.py` | 不变 | — |
| `src/report_generation/exporters.py` | 不变 | — |
| 两个 UI/Host 的 dist | 重新构建 | 同步实际被 Server/Host 使用的构建产物，不手工编辑 |

---

## 5. 兼容性说明

- **破坏性变更**：模型可见返回从顶层平铺结构变为 `{status, data, warnings}`，旧调用方需要适配
- **Tool 名称变更**：`report_generation` 不再存在，拆为 `report_prepare` + `report_finalize`
- **MCP 输入变更**：`report_finalize` 只公开可选 `work_results_uri`，不再公开 inline `work_results`
- **UI 兼容**：页面代码和展示不变；公共结果归一化层读取 `data` 和 `_meta.ui_payload`
- **业务层兼容**：内部仍使用 `operation=prepare|finalize`；`execute()` 只增加默认 `None` 的取消参数
- **产物兼容**：现有唯一目录和原子写入保留；不新增显式 `run_id`
- **Tool 数量边界**：不新增 `read_artifact`、`read_file` 或其他文件读取 Tool

---

## 6. 改造前后对比速查

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| Tool 数量 | 2 | 3 |
| 返回格式 | 字段平铺、output schema 宽松 | `{status, data, warnings}` + 明确 Pydantic output schema |
| 大结果返回 | 与模型结果混在一起 | 模型侧路径/摘要，UI 侧 `_meta.ui_payload` |
| 文件读取 Tool | 无 | 仍然无，不新增 `read_artifact` |
| run_id | 内部唯一目录隐含实现 | 保持现状，不新增字段 |
| 取消支持 | async task 取消后线程继续 | Event 逐层传入真实阶段检查 |
| 异常处理 | 业务失败与内部错误边界不清 | 业务失败信封；未预期异常 `ToolError` |
| 入参数量 | `report_generation` 6 个顶级参数 | 每个 Tool 2 个业务参数 |
| description | 缺"何时不调用" | 四要素齐全 |
| UI 绑定 | `engineering_facts` → engineering-confirmation-ui / `report_generation` → report-generation-ui | `engineering_facts` → engineering-confirmation-ui / `report_prepare` → 无 / `report_finalize` → report-generation-ui |

---

## 7. 验证标准

### 7.1 MCP 契约

- `tools/list` 只公开 `engineering_facts`、`report_prepare`、`report_finalize`。
- 不存在新增的 `read_artifact`、`read_file` 或其他 app-only 文件 Tool。
- 三个 Tool 的输入 Schema 准确表达 required、可选字段、枚举和 `additionalProperties`。
- 三个 Tool 的 output schema 包含明确的 `status`、`data`、`warnings` 和各自 data 字段，不再是任意 object。
- 三个 Tool 的成功/失败联合输出能拒绝 `completed/prepared + error`、`failed + 产物` 等无效组合。
- 每个 Tool description 包含职责、适用场景、返回关键字段、错误和不调用边界。
- 每个生产 Tool 入口继续调用 `make_host_client(ctx)`。

### 7.2 返回与错误

- 成功和可预期失败都通过对应 Pydantic 输出 Schema 校验。
- fatal diagnostic 进入 `data.error`，warning diagnostic 进入 `warnings`。
- 未预期异常表现为 MCP `ToolError`，不返回裸 traceback。
- `ToolError` 和失败信封均不泄露 traceback 或不必要的敏感绝对路径。
- `CancelledError` 设置 Event 后重新抛出，不产生第二次 MCP 响应。

### 7.3 取消

- 业务核心收到已设置 Event 时，在下一个检查点停止。
- prepare/finalize 的章节循环能够中途停止。
- 在各自产物提交点前取消时，不发布最终工程事实、工作包、Markdown 或 DOCX，也不遗留临时 `.tmp`；进入报告分组提交段后取消时，DOCX 与 Markdown 必须整组发布，不出现半组结果。
- MCP 集成取消后 session 不崩溃，后台线程在检查点退出。
- 测试等待后台线程退出后，再断言本次运行没有半成品和 `.tmp` 文件。

### 7.4 UI 与回归

- 工程事实页面继续显示采用方案、设备汇总和事实文件信息。
- 报告页面继续显示完整 Markdown、目录、DOCX/Markdown 路径和摘要。
- 页面组件无业务改动，只由归一化层适配信封和 UI payload。
- 真实 Host 能用返回的工作包 URI，通过已有文件系统能力完成 prepare → 编制 → finalize，不依赖任何新增读取 Tool。
- 代表性大数据下 `_meta.ui_payload` 完整到达 UI 且不进入模型上下文；若不满足，暂停本轮改造并单独评审，不临时改 Schema、不新增读取 Tool。
- 两个 UI 和两个 Host 构建通过，实际使用的 dist 已同步并完成生产构建产物运行时验证。
- 并发运行的内部目录和产物相互隔离，不覆盖、不串读；不增加公开 `run_id`。
- 现有工程事实、报告、Schema、Markdown、DOCX 测试继续通过。
- MCP 契约测试、编译检查、Skill 校验和 `git diff --check` 通过。
