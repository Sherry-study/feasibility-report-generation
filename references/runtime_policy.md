# 运行时策略

本文记录宿主运行时策略。这些内容不放入 `SKILL.md` 主体，避免把 Skill 重新变成 Workflow Runner。

## Research 运行策略

- 固定最多 2 个 research worker。
- `needs_research` 可同时包含最多 1 个 early-draft worker；Research worker + early-draft worker 总数不得超过 3。
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

## Early Draft 重叠策略

- early-draft 只处理已能构建 LLM job、`research_task_ids=[]`、非 `1.2`、非 `plan_status=summary_gate` 且未被 blocked/disabled/not-applicable/deterministic-only 排除的章节。
- early 输入固定写入 `draft_fragments/early/early_llm_jobs.json`、`early_empty_research_evidence.json` 和 `early_manifest.json`。
- early worker 的原始 batch、submit 状态和 `validated/` 均保留在 `draft_fragments/early/`，不得写入最终 `draft_fragments/*.json` 或根 `draft_fragments/validated/`。
- early submit 必须显式传入 early 专用 `--jobs`、`--evidence` 和 `--fragments-dir`，不得通过 `--output-dir` 自动定位正式产物。
- Research Evidence 返回后，只有 `planning_fingerprint_sha256`、early job digest 和当前 Draft 校验全部匹配的章节，才由确定性代码提升到根 `draft_fragments/validated/`。
- 无 manifest、陈旧 fingerprint、job digest 不一致、project 不一致或 Draft 校验失败时，early 草稿只被忽略，不删除，并让对应章节回到正常 Draft worker。

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
- collect 成功后，直接调用 `report_generation` Tool，传入确认后事实、Profile、Chapter Plan、输出目录和 `section_drafts.json`；旧的 `chapter_planning(section_drafts=...) -> planning_ready` 路径仅作为兼容入口保留。

## Planning Delivery Manifest

- 完整 jobs 与已校验 Evidence 就绪后，`chapter_planning` 写入 `planning_delivery_manifest.json`。
- manifest 记录当前 planning fingerprint，以及 confirmed facts、Profile、Chapter Plan、Research Tasks、Research Evidence 和完整 `llm_jobs.json` 的稳定内容摘要。
- `report_generation` 生成报告前必须读取同目录 manifest，校验 artifact 摘要，再重新执行 Evidence 与完整 Draft 校验。
- manifest 缺失、摘要不一致、Evidence 无效或 Draft 无效时，`report_generation` 返回 `consistency_blocked`，不得生成 DOCX/Markdown。

## Artifact 保留

- 建议最终保留：报告 DOCX、报告 Markdown、确认后事实、Research Evidence（如存在），以及 Tool 返回给宿主的摘要路径。
- research fragments、draft fragments、planning files、validation files、report model、report trace、consistency checks 等运行时 artifact 属于执行细节。
- 宿主应用可为调试保留运行时 artifact。
- 生产交付不得把 worker pack、缓存细节、内部校验文件或中间规划文件暴露到报告正文。
