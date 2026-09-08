# 运行时策略

本文记录宿主运行时策略。这些内容不放入 `SKILL.md` 主体，避免把 Skill 重新变成 Workflow Runner。

## 正式两 Tool 主流程

```text
engineering_facts
→ report_generation.prepare
→ Agent Research / Writing / Synthesis
→ report_generation.finalize
```

- `engineering_facts` 只负责把本地专业结果整理为结构化 `engineering_facts.json`。
- `report_generation.prepare` 只负责校验工程事实、生成章节级工作包并落盘 `work_package.json`。
- 宿主 Agent 读取 `work_package.json`，自行完成资料判断、外部研究、章节写作和综合章节整理。
- 宿主 Agent 将结果组装为满足 `schemas/report_work_results.schema.json` 的 `work_results.json`。
- `report_generation.finalize` 只负责校验 `work_package.json` / `work_results.json`，并导出 DOCX 与 Markdown。
- 两个 Tool 不承担长期会话状态、跨项目缓存、联网检索、LLM 写作或用户确认流程。

## Research 运行策略

- Research 任务来自 `work_package.research_tasks`，不得自行扩展为稳定规范、法律或标杆搜索。
- 研究目标是取得足以支撑目标章节的最小充分证据，不是执行 Deep Research。
- 市场研究默认最多 2 轮搜索、最多打开 4 个有效来源；达到最低来源数且核心判断已有支撑后立即停止。
- 其他研究默认最多 1 轮搜索、最多打开 `minimum_sources + 1` 个有效来源（上限 3）；达到最低来源数且足以支撑章节后立即停止。
- 搜索结果页摘要不能作为最终证据，必须打开可核验来源。
- 公开资料不得替代企业内部事实、Engineering Facts 或专业计算结果。
- 资料不足时输出 partial/blocked，并在对应章节保持缺口，不得补造事实。

## Writing / Synthesis 运行策略

- Writing 任务来自 `work_package.writing_tasks`；每个章节只使用本章事实切片、本章研究证据和本章契约。
- Synthesis 任务来自 `work_package.synthesis_tasks`；综合章节只使用指定章节的 `section_summary` 和确定性摘要。
- Agent 不生成标题、目录、表号或确定性表格；这些由 `report_generation.finalize` 按模板控制。
- Agent 输出应组装为 `work_results.json`，顶层只使用 `schema_version`、`writing_results`、`synthesis_results`。
- 普通章节结果必须包含 `section_id`、`blocks`、`section_summary`。
- `implementation_schedule` 仅允许用于 `18.2`，并必须满足七个固定阶段、顺序和字段约束。
- 提交 `finalize` 前，宿主 Agent 应检查事实一致性、无依据数字、内部术语、测试内容和缺失经济指标表达。

## Artifact 保留

- 正式交付物：`可行性研究报告_初稿.docx`、`可行性研究报告_初稿.md`。
- 建议调试保留：`engineering_facts.json`、`work_package.json`、`work_results.json`。
- research 过程记录、draft 临时片段、校验临时文件、缓存和报告模型属于执行细节。
- 生产交付不得把 worker pack、缓存细节、内部校验文件、中间规划文件或 Tool 调用细节暴露到报告正文。
