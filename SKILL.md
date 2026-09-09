---
name: feasibility-report-generation
description: 可研交付：用于首次生成设备级、装置级、系统级、全厂级工业改造可研，或从 engineering_facts/work_package/work_results 等中间产物恢复交付 DOCX/Markdown；仅编排 engineering_facts、report_prepare、report_finalize 三个 MCP Tools，不替代专业算法计算，不把单纯审阅既有报告误触发为生成流程。
---

# 可研报告交付 Skill

目标：编排宿主 Agent 调用 MCP Tools，把上游专业结果整理为 Engineering Facts，再通过报告工作包完成 Research、Writing、Synthesis 和最终报告输出。全流程只通过三个 MCP Tools 推进，不调用独立流程入口，不绕过 Tool、Schema、MCP 或内部算法。

## 核心边界

1. 只编排三个 MCP Tools：`engineering_facts`、`report_prepare`、`report_finalize`。
2. Engineering Facts 是正文事实底座；章节正文不得直接读取原始算法文件。
3. 写作或审查章节草稿前，读取 `references/report_rules/writing_constraints.md` 并按其执行。
4. 不自行计算、改写或补齐工程事实；数字与经济指标必须受 Engineering Facts、工作包和 `writing_constraints.md` 约束。
5. `work_package.json` 决定章节结构、确定性内容、Research/Writing/Synthesis 任务和 fallback；Agent 不自行改变章节标题、顺序、表号或确定性块。
6. 本 MCP Server 不公开 `read_file` / `read_artifact` Tool；宿主 Agent 通过已有宿主能力按逻辑 path 读取 `work_package.json`、保存 `work_results.json`，再把逻辑 path 传给后续 Tool。跨 Tool 产物不得退回本地临时路径交换。

## 状态闭环

按 Tool 返回信封 `{status, data, warnings}` 推进；完成条件必须可检查。不要假设所有状态都有 `next_action`。

| 状态 | Agent 动作 | 完成条件 |
| --- | --- | --- |
| `engineering_facts.status=failed` | 展示 `data.error` 和 `warnings`，要求修正来源目录或损坏 JSON 后重新调用 `engineering_facts`。 | `engineering_facts` 返回 `completed`。 |
| `engineering_facts.status=completed` | 核对 `data.artifact.path`、`data.summary`（关键设备边界、采用方案计数等）。 | 可进入 `report_prepare`。 |
| `report_prepare.status=failed` | 展示 `data.error` 和 `warnings`，修正工程事实 path 或 `project_name` 后重试。 | `report_prepare` 返回 `prepared`。 |
| `report_prepare.status=prepared` | 宿主 Agent 按逻辑路径读取 `work_package.json`（`data.artifact.path`），按其中 `research_tasks`、`writing_tasks`、`synthesis_tasks` 完成研究和写作，并将符合 Schema 的 `work_results.json` 保存到宿主逻辑路径。 | `work_results.json` 满足 `schemas/report_work_results.schema.json`。 |
| `report_finalize.status=failed` | 展示 `data.error` 和 `warnings`，修正工作包或工作结果后重试。 | `report_finalize` 返回 `completed`。 |
| `report_finalize.status=completed` 且 `data.summary.fallback_section_count=0` | 核对 `data.artifacts.docx_path`、`data.artifacts.markdown_path`、`data.manifest_path` 和 `data.summary` 并作为完整报告交付。 | manifest 通过 Schema、路径关联、hash/size 校验且无 fallback。 |
| `report_finalize.status=completed` 且 `data.summary.fallback_section_count>0` | 通过 `warnings`（如 `SECTION_FALLBACK`）明确标注为“未闭合草稿”，交付草稿并继续补充工作结果后重新 finalize。 | manifest 有效，fallback 数量已如实披露。 |

`report_finalize.status=completed` 只表示本次导出动作完成；只要存在 fallback，就必须披露 fallback 和告警，不得称为完全数据闭合报告。

## 调用顺序与错误处理

1. `engineering_facts(source_location, construction_unit?)`：`construction_unit` 未提供时按空值处理，不会自动推断。
2. `report_prepare(engineering_facts_path, project_name?)`：入参为上一步 `data.artifact.path`。
3. 宿主 Agent 通过宿主能力读取 `work_package.json` 并完成编制任务，将 `work_results.json` 保存到宿主逻辑路径。
4. `report_finalize(work_package_path, work_results_path?)`：导出 DOCX/Markdown，并在回读校验两份文件后最后提交 manifest。

错误处理约定：

- 可预期业务失败返回 `status=failed` 信封，`data.error` 携带稳定错误代码和 `retryable` 标记；可修正时修正输入后重试。
- 未预期内部错误表现为 MCP 协议错误（ToolError），不携带堆栈；此时不要重试相同输入，应排查服务端日志。
- 取消是协作式的：取消后不发布半成品产物，重试需重新调用对应 Tool。

## finalize 输入约定

- `work_package_path` 是 `report_finalize` 的唯一必填启动条件。
- `work_results_path`（宿主逻辑路径）是可选的 Agent 增强输入；MCP 层和业务核心都不接受 inline `work_results` 对象。
- 不提供 Agent 结果时，finalize 不会失败：使用空工作结果继续生成报告，Agent 章节走模板 fallback 兜底，并返回 `WORK_RESULTS_NOT_PROVIDED` warning。
- 此时应向用户明确说明：未提供 Agent 工作结果，本次报告为未闭合草稿。
- 传入的 `work_results_path` 内容非法时直接失败，不会静默降级为空结果。

## 参考规范边界

- Agent 必须读取：写作或审查章节草稿前读取 `references/report_rules/writing_constraints.md`；涉及外部资料或历史案例复用时读取 `references/report_rules/content_origin_policy.md`。
- 条件读取：仅在执行工作包中的 Research/Writing/Synthesis 或诊断运行策略时读取 `references/runtime_policy.md`；实际执行入口始终以 `work_package.json` 为准。
- Tool 内部规则：`src/engineering_facts/rules/`、`src/report_shared/rules/` 与 `src/report_shared/templates/` 由 Tool/内部实现消费；宿主 Agent 不自行据此计算、改写事实或绕过 Tool。若 work package 已携带所需规则，不要额外加载全部规则。

## 最终交付

成功后向用户交付：

- `可行性研究报告_初稿.docx`
- `可行性研究报告_初稿.md`
- Tool 返回的关键逻辑路径，包括 `engineering_facts.json`、`work_package.json`、`work_results.json`、`report_manifest.json`、诊断和报告产物路径。

交付说明必须区分完整生成、fallback 兜底章节和仍需用户/专业补充的资料缺口。
