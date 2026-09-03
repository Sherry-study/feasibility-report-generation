# Chapter Planning 章节草稿性能优化设计

## 1. 目标

在不减少章节、不缩短正文目标、不降低确认门、Evidence 校验、草稿校验和最终一致性门槛的前提下，降低 `chapter_planning -> needs_llm` 后的章节草稿生成时间。

本次以当前代表性案例为基准：8 个 LLM 章节、3 个并行 draft worker。Trae 端到端目标为 15 分钟以内；只有真实 Trae 同输入 A/B 运行达到该目标，才能声明性能目标完成。

## 2. 当前证据与根因

当前代表性运行中：

- `worker_01`：仅 `4.1.2`，pack 约 155 KB；
- `worker_02`：`1.1.3`、`1.2`、`2`、`26.1`，pack 约 62 KB；
- `worker_03`：`18.2`、`26.2`、`4.1.3.2`，pack 约 63 KB；
- 三个 pack 合计约 280 KB；最大 pack 是另外两个的约 2.5 倍；
- 草稿阶段约 8 分 20 秒，是当前可见流水线的最长阶段。

根因不是本地 DOCX 导出，也不是 MCP HTTP 传输本身，而是：

1. `4.1.2` 的规则把完整 `process` 绑定给 Worker，携带大量该节不消费的流股、设备参数和完整拓扑细节；
2. Worker 分配虽按 pack 字节和目标输出长度贪心，但单个超大章节无法再拆，形成输入负载长尾；
3. Evidence/Draft 返回后，`chapter_planning` 会再次生成章节计划、缺口和任务，缺少可验证的增量恢复；
4. 当前没有稳定的性能摘要，无法自动比较每次运行的 pack 规模、负载分布和缓存命中情况。

## 3. 范围与非目标

### 3.1 本次范围

- 精确收窄 `4.1.2` 的工程事实上下文；
- 改进 Worker 负载估算与分配摘要；
- 为确定性的规划基础产物增加安全的增量恢复；
- 增加可复现的静态性能指标和回归测试；
- 保持 3 个并行 Worker 上限，不增加批次和逐片校验次数。

### 3.2 非目标

- 不把章节改为更短的摘要；
- 不减少任何适用章节；
- 不把 LLM 章节改成模板拼接或确定性正文；
- 不修改 `needs_confirmation`、`needs_research`、`needs_llm`、`planning_ready` 状态语义；
- 不移除 Evidence、allowed headings、生产安全或最终一致性校验；
- 不增加第四个 Worker，不把任务拆成第二轮执行；
- 不修改当前工作区已有的未提交 `SKILL.md` 和 `references/report_rules/writing_constraints.md`。

## 4. 设计方案

### 4.1 精确投影 `4.1.2` 上下文

将 `references/chapter_rules/chapter_4.yaml` 中 `4.1.2` 的 `context_paths` 从完整 `process` 改为正文实际需要的稳定字段：

- `project.changes`
- `project.process_route_changed`
- `process.design.external_feeds`
- `process.design.product_streams`
- `process.retrofit.external_feeds`
- `process.retrofit.product_streams`
- `process.retrofit.scheme_changes`
- `adopted_scheme.scheme_name`
- `adopted_scheme.scheme_description`
- `adopted_scheme.status`

不绑定 `process` 根节点，也不绑定完整 `topology.nodes/edges`。外部工艺路线类型与工业化情况只在 `4.1.2` 规划状态为 `full` 且存在该节已校验 Research Evidence 时展开；项目事实用于说明本项目既有路线、改造后路线和是否改变工艺路线。完整流股组成、设备校核参数、分离器明细不属于本节写作输入。

`4.1.2` 的两种状态需要明确区分：

- `full`：保留当前 `R-TECH-0412-01` Research 路由，正文可基于已校验证据概述国内外路线类型、成熟度和工业化情况，再落到本项目路线；
- `concise`：不新增 Research，只基于确认后的 `project.process_route_changed`、进出料/产品摘要和采用方案说明沿用或局部调整情况；不得在没有 Evidence 时生成国内外路线、成熟度或商业化应用事实。

实现时在 `chapter_4.yaml` 的 `output` 下增加可选的 `required_structure_by_plan_status`，分别定义 `full` 与 `concise` 的要求；`internal/planning/llm_jobs.py` 从 `select_active_rules()` 已提供的 `plan_status` 选择对应结构，没有匹配项时回退现有 `required_structure`。`plan_status` 同时写入 job 和 worker pack，供宿主明确执行，不让 Worker 自行猜测状态。`concise` 结构不再隐含要求无证据的外部技术概况。此调整不改变章节存在性和正文预算，只改变可写事实范围。

投影只改变宿主 Worker 可见上下文，不改变 `confirmed_project_facts.json`，也不改变其他章节的事实绑定。

### 4.2 使用可解释的 Worker 负载模型

保留当前最多 3 个非空 Worker 和一次性并行派发。把负载估算明确为：

```text
estimated_load = projected_pack_bytes
               + output_budget_chars * 6
               + section_complexity_weight

section_complexity_weight = allowed_heading_count * 2048
                          + requirement_count * 512
```

其中：

- `projected_pack_bytes` 使用最终实际写入 pack 的 JSON 字节数；
- `output_budget_chars` 沿用现有章节正文预算；系数 `6` 是固定的调度权重，不改变正文预算；
- `allowed_heading_count` 为该章节 allowed headings 数量；
- `requirement_count` 为该章节全部 required structure 中 requirements 条目总数；
- 分配继续使用确定性 greedy heuristic，以降低最大 Worker 的 `estimated_load`，不宣称得到全局最优解；章节顺序和相同分数下的 Worker index 作为稳定 tie-break，结果必须可复现。

Worker manifest 需返回每个 Worker 的章节、pack bytes、正文预算、结构复杂度和 estimated load，便于诊断。不得为了负载平衡生成空 Worker或超过 3 个 Worker。

### 4.3 规划基础产物的安全增量恢复

首次进入 `chapter_planning` 时生成 `planning_snapshot.json`。快照记录：

- `confirmed_project_facts.json` 内容摘要；
- project profile 内容摘要；
- `references/chapter_rules/*.yaml` 规则集摘要；
- 确定性规划实现摘要，至少覆盖 `internal/planning/chapters.py`、`gaps.py`、`research_tasks.py`、`chapter_rules.py`；
- `ai_mode`、`run_mode`、`skip_user_inputs`；
- 已生成的 chapter plan、gap analysis、research tasks 路径和内容摘要。

后续携带 `research_evidence` 或 `section_drafts` 恢复时：

1. 所有输入摘要、规则摘要、确定性规划实现摘要和模式一致，且基础产物存在并匹配摘要：复用基础产物；
2. 任一输入、规则、模式或基础产物不匹配：自动完整重算并覆盖快照；
3. 快照损坏或缺失：按 cache miss 处理，不阻断生产运行；
4. Evidence 和 Draft 每次仍按当前任务重新校验，不缓存放行结果；
5. LLM Jobs 仍根据当前 Evidence 构建，避免复用过期证据绑定。

此缓存只优化确定性重复工作，不绕过事实、规则、Evidence 或 Draft 校验。

### 4.4 性能摘要

`chapter_planning` 返回并尽力落盘一份轻量性能摘要。所有状态均包含：

- `planning_cache_hit`；
- `planning_elapsed_ms`；

并增加 `draft_worker_metrics_available`。只有已构建 LLM Jobs 和 draft worker packs 的 `needs_llm` 或 Draft 校验恢复路径，才令其为 `true` 并包含：

- `worker_count`；
- `worker_total_pack_bytes`；
- `worker_max_pack_bytes`；
- 每个 Worker 的章节数、pack bytes、output budget 和 estimated load；
- `max_to_min_load_ratio`。

在 `needs_research` 等尚未生成 draft worker 的状态，`draft_worker_metrics_available=false`，上述 Worker 字段省略或为 `null`，不得构造虚假指标。

性能摘要属于 best-effort diagnostic，不参与业务放行：落盘失败时在 Tool summary 中增加 `performance_summary_error`，但不改变 `needs_research`、`needs_llm` 或 `planning_ready` 状态码。Evidence/Draft 校验结果仍具有更高优先级；性能摘要失败不得掩盖或放行无效草稿。不把这些内部指标写入正式报告、Markdown 或 DOCX。

## 5. 数据流

```text
confirmed facts + profile + chapter rules
                |
                v
        fingerprint validation
          | cache miss     | cache hit
          v                v
 plan + gaps + tasks     reuse verified base
          \                /
           v              v
        Evidence validation
                |
                v
       LLM jobs + precise contexts
                |
                v
     load-balanced 3 worker packs
                |
                v
     existing submit + collect gates
                |
                v
         Draft validation unchanged
```

## 6. 错误处理

- 精确 context path 在当前项目事实中不存在时，沿用现有缺失值处理，不回退到完整 `process` 或完整 topology；否则会重新引入性能问题并扩大事实暴露范围。
- 快照读取、JSON 解析或摘要校验失败时记录 cache miss，执行完整规划。
- Worker 分配必须对 0、1、2 个任务以及任务数大于 3 的情况保持兼容。
- 任一草稿 submit 失败时仍只重写 `retry_sections`；不得重新生成已接受章节。
- 性能摘要生成失败不得放行无效草稿，也不得改变业务状态码。

## 7. 验证方案

### 7.1 自动测试

新增或扩展测试，覆盖：

1. `4.1.2` pack 不再包含完整 `process` 或 `topology.nodes/edges`，仍包含 `project.process_route_changed`、设计/改造摘要和采用方案；
2. 覆盖 `4.1.2=full` 和 `4.1.2=concise`：两种状态的 worker pack 包含不同的 `required_structure` 和明确 `plan_status`；`full` 使用已校验 Research Evidence，`concise` 不包含无 Evidence 外部技术概况要求；
3. 当前代表性输入下，`4.1.2` pack 不超过 80 KB，任一 Worker pack 不超过 100 KB，三个 Worker pack 合计不超过 220 KB；
4. 三个 Worker 的章节覆盖完整、无重复，顺序稳定；
5. 负载公式字段、固定系数、复杂度计数和 tie-break 结果符合设计，当前代表性输入的 `max_to_min_load_ratio <= 1.6`；
6. 覆盖 0、1、2、3 个及大于 3 个 job，不生成空 Worker，实际 Worker 数不超过 3；
7. 相同事实、Profile、规则和规划实现恢复时命中快照；
8. 修改事实、Profile、规则、规划依赖文件摘要或模式时快照失效；
9. 损坏快照自动重算；
10. Evidence/Draft 在 cache hit 下仍执行校验；
11. `needs_research` 时 `draft_worker_metrics_available=false` 且没有伪造 Worker 指标；`needs_llm` 时为 `true` 并提供完整指标；
12. 性能摘要落盘失败时出现 `performance_summary_error`，业务状态码不变且无效草稿仍被拒绝；
13. 现有状态码、blocked sections、production guard 和报告生成测试全部通过。

运行：

```text
python -B -X utf8 -m unittest discover -s tests -v
python -B -X utf8 -m compileall -q .
python -X utf8 C:\Users\huangxiaoting\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
```

### 7.2 静态性能验收

测试不得依赖当前未跟踪的 `_args_*.json` 或 `report_output/`。在 `tests/test_chapter_rules_runtime.py` 中提供确定性的代表性 fixture builder：固定生成 8 个当前章节 job、3 个 Worker，包含与当前装置级案例同量级的 design/retrofit 流股数组、完整 topology、设备/采用方案冗余字段和 Research Evidence。builder 固定数组数量、字符串内容和字段顺序，禁止随机数和当前时间；宽字段必须存在于源 fixture 中，以证明精确 context paths 确实排除了它们。

由该 builder 生成输入并比较优化前基线常量与优化后输出：

- 8 个 LLM 章节保持不变；
- 3 个 Worker 保持不变；
- 总正文字符预算不变；
- `4.1.2` pack `<= 80 KB`；
- `worker_max_pack_bytes <= 100 KB`；
- `worker_total_pack_bytes <= 220 KB`；
- `max_to_min_load_ratio <= 1.6`；
- 规划恢复调用出现 `planning_cache_hit=true`；
- 草稿校验结果与章节覆盖保持有效。

上述字节与负载阈值只作为当前代表性回归 fixture 的静态门槛，不作为所有规模项目的生产阻断条件。其他项目只记录指标，由真实 Trae A/B 判断端到端性能。

### 7.3 真实 Trae A/B

静态指标通过后，由 Trae 使用同一输入、同一模型和同一宿主环境运行完整链路。记录：

- Skill 开始时间；
- 首次 MCP 调用时间；
- Research 完成时间；
- 三个 draft worker 完成时间；
- collect 完成时间；
- 最终 DOCX/Markdown 完成时间。

目标：完整链路不超过 15 分钟，并且报告章节、字数目标、校验结果和关键结论没有退化。若未达到，只报告真实结果，不继续通过增加批次或逐片校验补救。

## 8. 预期改动文件

- `references/chapter_rules/chapter_4.yaml`
- `internal/planning/llm_jobs.py`
- `internal/planning/draft_fragments.py`
- `internal/planning/stage.py`
- 必要时在 `internal/common.py` 增加通用内容摘要辅助函数
- `tests/test_chapter_rules_runtime.py`

明确不修改：

- `SKILL.md`
- `references/report_rules/writing_constraints.md`
- Schema 与状态码契约
- 最终报告章节结构和正文预算

实施开始前记录 `SKILL.md` 与 `references/report_rules/writing_constraints.md` 的 SHA-256；完成后再次计算并确认完全一致。若实施前相对 HEAD 已存在 diff，不要求该 diff 为空；只要求实施前后 SHA-256 完全相同，并确认这两个文件没有被 stage 或 commit。不得将两者纳入格式化、批量替换或提交范围。

## 9. 冷启动关键路径实验（第二轮）

本节是用户完成第一次 Trae 实测后的增量决策；凡与第 3.2 节和第 8 节中“不得修改 Skill / 状态推进方式”的第一轮约束冲突之处，以本节为准。第一轮上下文投影、负载均衡和规划快照设计继续有效。

### 9.1 决策与目标

第一次优化已经降低 draft worker pack 体积和 Draft 阶段时长，但真实 Trae 冷启动总时长没有下降。第二轮以用户定义的系统耗时为准：

```text
Engineering Facts 输出耗时
+ 用户明确确认后至 DOCX/Markdown 完成耗时
```

用户阅读和确认材料的等待时间不计入。冷启动验收不得依赖其他输出目录、历史 Research Evidence 或历史章节草稿。

本轮选择“同轮重叠 + 减少宿主往返”，不采用三个串行 Draft 波次。原因是 Trae 的 Agent 派发和 Tool 接力本身有明显成本；增加 LLM 批次可能抵消并行收益。

### 9.2 `needs_research` 同轮重叠

首次 `chapter_planning` 判断需要 Research 时，仍返回 `needs_research`，但 `host_workflow` 同时提供：

- 现有最多 2 个 research worker；
- 最多 1 个 early-draft worker。

early-draft 只包含同时满足以下条件的章节：

1. 当前完整规则与确认事实能够构建 LLM job；
2. `research_task_ids` 为空；
3. 不是研究结论或综合结论章节。固定排除 `1.2`，并排除 `plan_status=summary_gate` 的章节；
4. 不属于 blocked、disabled、not-applicable 或 deterministic-only。

当前代表性案例预期 early-draft 为 `1.1.3`、`4.1.3.2`。该集合来自规则计算与上述过滤条件，不由宿主自行挑选。early-draft 使用空 Evidence 契约，只允许 `source_evidence_ids=[]`，生成后通过现有 draft submit 校验写入当前输出目录的 `draft_fragments/validated/`。它不是跨任务缓存。

合并的 `host_workflow` 必须要求一次性并行派发所有 Research 与 early-draft worker，总并发任务数不得超过 3；全部完成后只 collect Research 一次，再把 `research_evidence.json` 传回 `chapter_planning`。early-draft 不等待 Research，也不触发单独的 `chapter_planning` 调用。

### 9.3 Evidence 返回后的增量 Draft

`chapter_planning` 校验 Research Evidence 并构建当前完整 LLM jobs 后，检查 `draft_fragments/validated/` 中的 early-draft：

1. 只接受 project_id、当前 job、allowed headings 和 Evidence 绑定均通过当前校验的章节；
2. 当前 planning fingerprint、job 或文件不匹配时忽略，不阻断正常 Draft；
3. 从本轮 worker packs 中排除已接受章节，但最终 `llm_jobs.json` 仍保留完整章节集合；
4. 其余章节继续一次性生成最多 3 个非空 worker，不新增第三个串行 Draft 波次；
5. 最终 collect 仍按完整 `llm_jobs.json` 校验章节覆盖，因此 early-draft 与后续草稿缺一不可。

这意味着汇总章节 `1.2`、`26.1`、`26.2` 仍在 Research 完成后的正常 Draft 批次生成；本轮不为了理论依赖再增加单独汇总波次。

### 9.4 Draft 完成后直接生成报告

现有 Draft `HOST_WORKFLOW.md` 在 collect 成功后不再要求再次调用 `chapter_planning`，而是直接调用 `report_generation`，传入确认事实、Profile、Chapter Plan、输出目录和 `section_drafts.json`。

为保持门禁等价，`report_generation` 在构建报告模型前必须从同一 `output_dir` 读取：

- `research_tasks.json`；
- `research_evidence.json`（存在 Research 任务时必需）；
- `llm_jobs.json`；
- 调用参数中的 `section_drafts.json`。

按顺序重新执行当前 Research Evidence 校验和完整 Draft 校验，校验通过后才生成报告。缺文件、指纹/项目不一致、Evidence 无效或 Draft 无效时不得生成 DOCX/Markdown；沿用 exit code 3 和 `consistency_blocked`，并返回 `validation_stage` 与 `issues`。四个 MCP Tool 的名称、参数和成功状态不变。

`chapter_planning(section_drafts=...) -> planning_ready` 兼容路径继续保留，供旧宿主、诊断和重试使用；新的 Skill 和 HOST_WORKFLOW 走直接报告路径。

### 9.5 Skill 与运行策略

`SKILL.md` 只增加两条可执行状态规则：

- `needs_research` 必须执行 Tool 返回的合并 `host_workflow`，不得把 early-draft 另行串行化；
- `needs_llm` collect 成功后直接调用 `report_generation`，由该 Tool 完成最终 Evidence/Draft 门禁。

详细并发、复用和回退说明继续放在 `references/runtime_policy.md`，避免把 Skill 主体膨胀成 Workflow Runner。保留用户在 `SKILL.md` 中已有的未提交修改，只做上述定点增量。

### 9.6 诊断指标与验收

`chapter_planning` 摘要增加 best-effort 字段：

- `overlap_enabled`；
- `early_draft_section_ids`；
- `early_draft_worker_count`；
- `accepted_early_draft_section_ids`；
- `remaining_draft_section_ids`。

自动测试至少覆盖：

1. `needs_research` 同时产出 Research 与 early-draft pack，合计 worker 不超过 3；
2. `1.2` 和 `summary_gate` 章节不进入 early-draft；
3. early-draft 通过 submit 后，Evidence 恢复只为剩余章节生成 pack；
4. 无效、陈旧或其他项目 early-draft 不被接受，并回到完整 Draft；
5. 最终 collect 必须覆盖完整 jobs；
6. `report_generation` 直接路径重新校验 Evidence 和 Draft；无效输入不生成报告；
7. 旧的 `chapter_planning(section_drafts=...)` 兼容路径继续通过；
8. Skill 快速校验、全部 unittest、compileall 通过。

真实成功标准仍只有 Trae 冷启动 A/B：同任务、同模型、同宿主环境且无历史输出复用时，按 9.1 的计时口径总时长下降，报告章节覆盖、字数约束、Evidence 和一致性门禁无退化。未实测前只称“实验版”，不声明性能完成。

### 9.7 第二轮预期改动文件

- `SKILL.md`（定点增量，保留现有未提交内容）
- `references/runtime_policy.md`
- `internal/planning/stage.py`
- `internal/planning/draft_fragments.py`
- `internal/planning/research_fragments.py` 或等价的轻量合并 workflow 生成位置
- `internal/report/stage.py`
- 必要的 Tool/Workflow docstring（不得改变参数）
- `tests/test_chapter_rules_runtime.py`

第二轮不得修改 `references/report_rules/writing_constraints.md`、Schema、事实确认门、章节结构、正文预算和 Research 搜索预算。实施前后分别记录目标文件 SHA-256 和 diff；第二轮变更必须能与第一轮 pack/快照优化分开识别，便于 Trae 实测失败后按用户确认的范围回退。
