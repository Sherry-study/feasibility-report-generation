# feasibility-report-generation

标准 Agent Skill + MCP Tool 包，用于把上游专业结果整理为 Engineering Facts，并通过报告工作包组织 Agent 研究、写作和最终 DOCX/Markdown 输出。

本仓库不再提供独立 Workflow Runner。执行流程由宿主 Agent 根据 `SKILL.md` 调用 MCP Tools 完成。

## 架构

```text
skills/feasibility-report-generation/
  SKILL.md
  references/
  prompts/
  scripts/
  assets/
src/
schemas/
tests/
```

## MCP 工具

公开 MCP Tool 有三个，返回统一 `{status, data, warnings}` 信封。顶层 `status`
固定使用三态：`success` 表示正常完成，`failed` 表示可预期业务失败，
`error` 表示未捕获异常或程序错误；取消不进入 return，由异步取消机制处理。
原业务阶段状态保存在 `data.business_status`。

- `engineering_facts`：工程事实整理（绑定 engineering-confirmation-ui）
- `report_prepare`：基于工程事实生成章节工作包（不绑定 UI，对话流文本卡片展示摘要）
- `report_finalize`：合并 Agent 工作结果并导出 DOCX/Markdown（绑定 report-generation-ui）

三个公开 Tool 分别由 `src/engineering_facts/`、`src/report_prepare/`、`src/report_finalize/` 提供独立核心入口；报告共用算法、规则和模板位于 `src/report_shared/`。`schemas/`、`skills/feasibility-report-generation/prompts/`、`skills/feasibility-report-generation/references/` 是共享契约与规则来源。本 Server 不公开 `read_file` / `read_artifact` Tool；跨 Tool 产物均通过宿主存储中的逻辑 `*_path` 交换。

## 使用方式

宿主 Agent 应按 `SKILL.md` 的主流程推进：

```text
engineering_facts
→ report_prepare
→ Agent Research / Writing / Synthesis（通过宿主能力读取工作包、保存 work_results.json）
→ report_finalize
```

`engineering_facts` 公开输入只接受一个顶层 `input` 对象，避免来源文件参数散落在顶层。`input.root`（必填）为宿主文件服务中的来源逻辑目录前缀，核心通过 `HostClient.get_file()` 在该目录下按默认文件名尝试读取 `plant_reactor_result.json`、`retrofit_tower_equipment.json`、`scheme.json`、`plant_info.json`、`plant_diagnosis_report.md`、`new_device_params.json`、`retrofit_equipment.json`、`retrofit_topology.json`、`plant_level_result.json`。非标准文件名放在 `input.file_overrides` 中按角色覆盖（值为相对 root 的宿主逻辑路径）。可选 `input.construction_unit` 未提供时按空值处理，不自动推断。产出的 Engineering Facts 始终保存到宿主逻辑路径。`report_prepare` 接收 `input.engineering_facts_path` 和可选 `input.report_context.project_name`，其中 `engineering_facts_path` 必须是 `engineering_facts` 上一步返回的 `artifact.path` 宿主逻辑路径；核心先生成并完整校验工作包，再保存全部章节 context，最后保存 `work_package.json` 作为提交标志。`report_finalize` 接收必填 `input.work_package_path` 和可选 `input.work_results_path`，其中 `work_package_path` 必须是 `report_prepare` 上一步返回的 `artifact.path` 宿主逻辑路径，`work_results_path` 是宿主 Agent 保存章节结果后的逻辑路径；不接受 inline `work_results`，未提供工作结果时仍生成由模板 fallback 兜底的未闭合草稿。本地测试时由 `LocalHostClient` 把这些逻辑路径映射到本地目录，来源目录文件先预置到模拟文件服务的 `sources/` 前缀下再执行。

`report_finalize` 先在本地临时目录生成并校验 Markdown/DOCX，再保存到宿主逻辑路径并通过 HostClient 回读核对哈希和大小，最后保存通过 Schema 校验的 `report_manifest.json`。manifest 是有效交付的提交标志；没有 manifest 不得把本次调用视为 `success`。

模型可见的 `structuredContent` 只包含产物引用、摘要、warnings 和 error；完整工程事实与完整 Markdown 通过 progress 通知的 `uiEvent.final_result` 传给宿主 UI，不进入 Agent 模型上下文。

未确认或未出现在 Engineering Facts / 工作包 / Agent 结果中的投资、收益、回收期、IRR、产能、能耗等数字不得写入正文。

## 参考规范

- `skills/feasibility-report-generation/references/engineering_rules/`: 算法字段语义、设备分类、能耗折标、计算口径。
- `skills/feasibility-report-generation/references/report_rules/`: 报告正文写作边界、内部词屏蔽、生产安全约束。
- `skills/feasibility-report-generation/references/runtime_policy.md`: worker、cache、运行时 artifact 等宿主运行策略。

## 辅助脚本

`skills/feasibility-report-generation/scripts/` 放 skill 交付相关的确定性动作：

- `skills/feasibility-report-generation/scripts/validate_output.py`: 检查报告输出目录中的关键交付物和 manifest 校验。
- `skills/feasibility-report-generation/scripts/package_result.py`: 将报告交付物和可复用证据文件打包为 zip。

仓库根 `scripts/` 放本地开发与 CI 工具，不接管 Agent 研究和写作：

- `scripts/run_engineering_facts.py`: 本地手动运行工程事实整理。
- `scripts/run_report_generation.py`: 本地手动运行 prepare/finalize 链路。
- `scripts/validate_schema.py`: 校验 JSON/YAML 文件可解析，并在可用时用 `jsonschema` 校验实例。
- `scripts/local_adapters.py`: CLI 用本地 `Content` 与 `HostClient` adapter。

## 验证

```bash
python -B -X utf8 -m unittest discover -s tests -v
python -B -X utf8 -m compileall -q src mcp_server scripts tests
python -B -X utf8 -c "import asyncio; from mcp_server.server import mcp; print([tool.name for tool in asyncio.run(mcp.list_tools())])"
npm run build --prefix mcp_server/engineering-confirmation-ui/ui
npm run build --prefix mcp_server/engineering-confirmation-ui/host
npm run build --prefix mcp_server/report-generation-ui/ui
npm run build --prefix mcp_server/report-generation-ui/host
python -X utf8 C:\Users\huangxiaoting\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
git diff --check
```
