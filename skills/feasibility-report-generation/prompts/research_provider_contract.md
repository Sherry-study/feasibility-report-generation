# Research Provider Contract

宿主 Agent 只执行 `work_package.research_tasks` 中实际生成的 task，不得自行扩展到稳定规范、法律或标杆搜索。

要求：

- 每条 evidence 必须绑定 task_id 与来源，并只服务于 `work_package` 指定的目标章节；
- Search snippet 不能直接作为最终 evidence；
- Web 不得替代企业内部事实或 PA/EA 专业结果；
- 设计规范、编制标准、一般法律法规当前使用包内固定规范库，不重复联网；
- 10.5 仅对 `missing_energy_conversion_media` 补折标系数；
- 10.6 当前不进行行业能效标杆搜索；
- 资料不足时输出 partial/blocked，不得补造；
- 研究结果由宿主 Agent 转化为章节写作所需材料，并最终并入满足 `schemas/report_work_results.schema.json` 的 `work_results.json`；不得新增 Schema 未声明的顶层 `research_results`。


## 检索预算（首跑性能硬约束）

研究目标是取得“足以支撑章节”的最小充分证据，不是执行 Deep Research。

- `suggested_queries` 是候选检索词，不要求全部执行；优先使用 worker pack 中的 `primary_queries`。
- 一个来源可以同时回答多个 research question，不要求每个问题单独搜索。
- 市场研究：默认最多 2 轮搜索、最多打开 4 个有效来源；达到 `minimum_sources` 且核心判断已有支撑后立即停止。
- 其他研究：默认最多 1 轮搜索、最多打开 `minimum_sources + 1` 个有效来源（上限 3）；达到最低来源数且足以支撑章节后立即停止。
- 只有主检索证据不足或来源冲突时才使用 `fallback_queries`；禁止为了“更完整”扩展非关键检索。
- 搜索结果页摘要不能作为最终证据，必须打开可核验来源。
