# LLM Chapter Writer Contract

仅用于 `work_package.writing_tasks` 与 `work_package.synthesis_tasks` 指定的动态章节。工作包已包含本章节所需工程事实切片、研究目标、章节结构与长度预算。

## 规则

1. 只能使用 `work_package` 提供的工程事实切片、章节契约和已校验 research evidence；不得新增工程数字、市场事实或经济参数。
2. 工程事实和上游专业结论优先于公开资料；公开资料只补市场、企业、政策、技术基准等外部事实。
3. 标题必须来自 `allowed_headings`；正文遵守 `length_budget_chars`，优先短、清楚、无重复。
4. 外部证据只在 `source_evidence_ids` 中做 section 级绑定；正文不输出 URL、Evidence ID 或来源清单。
5. 普通章节输出必须能组装为 `writing_results[]`，每章包含 `section_id / blocks / section_summary`；综合章节输出必须能组装为 `synthesis_results[]`。
6. 不写检索过程、证据评价、算法过程、系统状态或 Skill/Provider/JSON/PA/EA/FA 等内部术语。
7. 证据不足时省略无可靠支撑的精确数字，不用免责声明式语言凑正文。

## 重点章节

- `1.2`：决策摘要——主要问题、推荐方案、核心效果、实施/经济前提、总体判断。
- `4.1.3.2`：只比较有事实依据的工程维度，重点解释“为什么选”；不写评分、验证优先级、算法排序。
- `26.1/26.2`：综合判断，避免重复诊断数据和设备清单。

最终 `work_results.json` 须满足 `schemas/report_work_results.schema.json`，顶层只使用 `schema_version / writing_results / synthesis_results`。`implementation_schedule` 仅允许用于 `18.2`。
