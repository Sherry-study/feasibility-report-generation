---
name: feasibility-report-generation
description: 可研交付：用于首次生成设备级、装置级、系统级、全厂级工业改造可研，或从 engineering_facts/work_package/work_results 等中间产物恢复交付 DOCX/Markdown；仅编排 engineering_facts 与 report_generation 两个 MCP Tools，不替代专业算法计算，不把单纯审阅既有报告误触发为生成流程。
---

# 可研报告交付 Skill

目标：编排宿主 Agent 调用 MCP Tools，把上游专业结果整理为 Engineering Facts，再通过报告工作包完成 Research、Writing、Synthesis 和最终报告输出。全流程只通过两个 MCP Tools 推进，不调用独立流程入口，不绕过 Tool、Schema、MCP 或内部算法。

## 核心边界

1. 只编排两个 MCP Tools：`engineering_facts`、`report_generation`。
2. Engineering Facts 是正文事实底座；章节正文不得直接读取原始算法文件。
3. 写作或审查章节草稿前，读取 `references/report_rules/writing_constraints.md` 并按其执行。
4. 不自行计算、改写或补齐工程事实；数字与经济指标必须受 Engineering Facts、工作包和 `writing_constraints.md` 约束。
5. `work_package.json` 决定章节结构、确定性内容、Research/Writing/Synthesis 任务和 fallback；Agent 不自行改变章节标题、顺序、表号或确定性块。

## 状态闭环

按 Tool 返回状态推进；完成条件必须可检查。不要假设所有状态都有 `next_action`。

| 状态 | Agent 动作 | 完成条件 |
| --- | --- | --- |
| `engineering_facts.status=failed` | 展示 `diagnostics`，要求修正来源目录或损坏 JSON 后重新调用 `engineering_facts`。 | `engineering_facts` 返回 `completed`。 |
| `engineering_facts.status=completed` | 核对 `artifact.uri`、`engineering_facts.adopted_scheme`、关键设备边界和致命诊断。 | 可进入 `report_generation.prepare`。 |
| `report_generation.status=prepared` | 读取 `work_package.json`，按其中 `research_tasks`、`writing_tasks`、`synthesis_tasks` 完成 Agent 研究和写作，持续保存 `work_results.json`。 | `work_results.json` 满足 `schemas/report_work_results.schema.json`。 |
| `report_generation.status=failed` | 展示 `diagnostics`，修正工程事实、工作包或工作结果后重试对应 operation。 | 对应 operation 返回 `prepared` 或 `completed`。 |
| `report_generation.status=completed` | 核对 `artifacts.docx`、`artifacts.markdown`、`summary` 和 `diagnostics` 并交付。 | Markdown、DOCX 文件路径存在且完成状态被如实说明。 |

当 `completed` 返回 `summary.fallback_section_count>0` 或 warning 诊断时，必须披露 fallback 和告警；这是含资料缺口兜底的初稿，不得称为完全数据闭合报告。

## finalize 输入约定

- `work_package_uri` 是 finalize 的唯一必填启动条件。
- `work_results_uri`（Agent 工作结果 JSON 文件 URI）与 `work_results`（inline Agent 工作结果对象）是 Agent 增强输入，表示同一类数据，只能二选一；同时传入会返回参数冲突错误。
- 不提供 Agent 结果时，finalize 不会失败：使用空工作结果继续生成报告，Agent 章节走模板 fallback 兜底，并返回 `WORK_RESULTS_NOT_PROVIDED` warning 诊断。
- 此时应向用户明确说明：未提供 Agent 工作结果，本次报告为不完整初稿。
- 传入的 `work_results` / `work_results_uri` 内容非法时直接失败，不会静默降级为空结果。

## 参考规范边界

- Agent 必须读取：写作或审查章节草稿前读取 `references/report_rules/writing_constraints.md`；涉及外部资料或历史案例复用时读取 `references/report_rules/content_origin_policy.md`。
- 条件读取：仅在执行工作包中的 Research/Writing/Synthesis 或诊断运行策略时读取 `references/runtime_policy.md`；实际执行入口始终以 `work_package.json` 为准。
- Tool 内部规则：`src/engineering_facts/rules/`、`src/report_generation/rules/` 与 `src/report_generation/templates/` 由 Tool/内部实现消费；宿主 Agent 不自行据此计算、改写事实或绕过 Tool。若 work package 已携带所需规则，不要额外加载全部规则。

## 最终交付

成功后向用户交付：

- `可行性研究报告_初稿.docx`
- `可行性研究报告_初稿.md`
- Tool 返回的关键 JSON 路径，包括 `engineering_facts.json`、`work_package.json`、`work_results.json`、诊断和报告产物路径。

交付说明必须区分完整生成、fallback 兜底章节和仍需用户/专业补充的资料缺口。
