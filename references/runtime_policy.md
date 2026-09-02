# 运行时策略

本文记录宿主运行时策略。这些内容不放入 `SKILL.md` 主体，避免把 Skill 重新变成 Workflow Runner。

## Research 运行策略

- 固定最多 2 个 research worker。
- 存在市场研究任务时，将其分配给 `market_long_pole`。
- 企业、政策、实施进度、技术、能耗因子等其他任务分配给 `general_research`。
- 只生成非空 worker pack。
- 每个 worker 只读取自己的 `research_fragments/worker_contexts/research_worker_XX.json`。
- research worker 不读取 `research_tasks.json`、其他 worker pack 或 Engineering Facts 全量文件。
- 严格遵守每个任务的 `search_budget`。
- 达到 `minimum_sources` 且证据足以支撑目标章节后立即停止。
- 每个任务写入一个 `evidence_<task_id>.json`，并通过内部辅助 CLI submit。
- submit 失败时只重试该任务。
- 全部任务 submit 成功后，只运行一次 collect，生成 `research_evidence.json`。
- collect 成功后，立即再次调用 `chapter_planning` Tool，并将 `research_evidence` 设置为 collect 产物。
- 如果 `chapter_planning` 返回 `needs_llm`，直接进入 Draft HOST_WORKFLOW，不做额外总结或重新规划。

## Research 缓存

- 只有同一输出目录内的重复运行，才允许复用已通过校验的 `research_evidence.json`。
- 缓存复用只是重复运行时的加速手段，不是首次运行依赖。
- 无效、过期或任务不匹配的缓存文件必须忽略，不得转化为硬失败。

## Draft 运行策略

- 固定最多 3 个非空 draft worker。
- 每个 worker 只读取自己的 `draft_fragments/worker_contexts/worker_XX.json`。
- worker 不读取完整 `llm_jobs.json`、`project_facts.json`、其他 worker pack 或无关运行时 artifact。
- 每个 worker 生成一个 `batch_worker_XX.json`，包含分配给它的章节。
- 遵守每个章节的 `length_budget_chars`。
- 除非 Schema 或具体任务要求，不输出 `claims` 或 `open_items`。
- 每个 worker batch 通过内部辅助 CLI submit 一次。
- submit 按章节校验；同一 worker batch 中某章节失败时，已通过章节仍可入库。
- submit 部分失败时，只重写 `retry_sections`；已接受章节不得重新生成。
- 所有必需章节被接受后，只运行一次 collect，生成 `section_drafts.json`。
- collect 成功后，立即再次调用 `chapter_planning` Tool，并将 `section_drafts` 设置为 collect 产物。

## Artifact 保留

- 建议最终保留：报告 DOCX、报告 Markdown、确认后事实、Research Evidence（如存在），以及 Tool 返回给宿主的摘要路径。
- research fragments、draft fragments、planning files、validation files、report model、report trace、consistency checks 等运行时 artifact 属于执行细节。
- 宿主应用可为调试保留运行时 artifact。
- 生产交付不得把 worker pack、缓存细节、内部校验文件或中间规划文件暴露到报告正文。
