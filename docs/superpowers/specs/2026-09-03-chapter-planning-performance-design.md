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

1. `4.1.2` 的规则把完整 `process` 绑定给 Worker，携带大量该节不消费的流股、设备参数和拓扑细节；
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
- `process.design.external_feeds`
- `process.design.product_streams`
- `process.design.topology`
- `process.retrofit.external_feeds`
- `process.retrofit.product_streams`
- `process.retrofit.topology`
- `process.retrofit.scheme_changes`
- `adopted_scheme`

外部工艺路线类型与工业化情况继续来自该节已校验 Research Evidence；项目事实只用于说明本项目既有路线、改造后路线和是否改变工艺路线。完整流股组成、设备校核参数、分离器明细不属于本节写作输入。

投影只改变宿主 Worker 可见上下文，不改变 `confirmed_project_facts.json`，也不改变其他章节的事实绑定。

### 4.2 使用可解释的 Worker 负载模型

保留当前最多 3 个非空 Worker 和一次性并行派发。把负载估算明确为：

```text
estimated_load = projected_pack_bytes
               + output_budget_chars * output_weight
               + section_complexity_weight
```

其中：

- `projected_pack_bytes` 使用最终实际写入 pack 的 JSON 字节数；
- `output_budget_chars` 沿用现有章节正文预算；
- `section_complexity_weight` 只由章节结构复杂度确定，例如 allowed headings、required structure 条目数量，不使用业务结果或主观判断；
- 分配目标为最小化最大 Worker 的 `estimated_load`；章节顺序保持稳定，可复现。

Worker manifest 需返回每个 Worker 的章节、pack bytes、正文预算、结构复杂度和 estimated load，便于诊断。不得为了负载平衡生成空 Worker或超过 3 个 Worker。

### 4.3 规划基础产物的安全增量恢复

首次进入 `chapter_planning` 时生成 `planning_snapshot.json`。快照记录：

- `confirmed_project_facts.json` 内容摘要；
- project profile 内容摘要；
- `references/chapter_rules/*.yaml` 规则集摘要；
- `ai_mode`、`run_mode`、`skip_user_inputs`；
- 已生成的 chapter plan、gap analysis、research tasks 路径和内容摘要。

后续携带 `research_evidence` 或 `section_drafts` 恢复时：

1. 所有输入摘要和模式一致，且基础产物存在并匹配摘要：复用基础产物；
2. 任一输入、规则、模式或基础产物不匹配：自动完整重算并覆盖快照；
3. 快照损坏或缺失：按 cache miss 处理，不阻断生产运行；
4. Evidence 和 Draft 每次仍按当前任务重新校验，不缓存放行结果；
5. LLM Jobs 仍根据当前 Evidence 构建，避免复用过期证据绑定。

此缓存只优化确定性重复工作，不绕过事实、规则、Evidence 或 Draft 校验。

### 4.4 性能摘要

`chapter_planning` 返回并落盘一份轻量性能摘要，至少包含：

- `planning_cache_hit`；
- `planning_elapsed_ms`；
- `worker_count`；
- `worker_total_pack_bytes`；
- `worker_max_pack_bytes`；
- 每个 Worker 的章节数、pack bytes、output budget 和 estimated load；
- `max_to_min_load_ratio`。

不把这些内部指标写入正式报告、Markdown 或 DOCX。

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

- 精确 context path 在当前项目事实中不存在时，沿用现有缺失值处理，不回退到完整 `process`；否则会重新引入性能问题并扩大事实暴露范围。
- 快照读取、JSON 解析或摘要校验失败时记录 cache miss，执行完整规划。
- Worker 分配必须对 0、1、2 个任务以及任务数大于 3 的情况保持兼容。
- 任一草稿 submit 失败时仍只重写 `retry_sections`；不得重新生成已接受章节。
- 性能摘要生成失败不得放行无效草稿，也不得改变业务状态码。

## 7. 验证方案

### 7.1 自动测试

新增或扩展测试，覆盖：

1. `4.1.2` pack 不再包含完整 `process`，仍包含路线判断、设计/改造摘要和采用方案；
2. 当前代表性输入下，最大 Worker pack 明显小于优化前约 155 KB；
3. 三个 Worker 的章节覆盖完整、无重复，顺序稳定；
4. `max_to_min_load_ratio` 比优化前下降；
5. 相同事实、Profile 和规则恢复时命中快照；
6. 修改事实、Profile、规则或模式时快照失效；
7. 损坏快照自动重算；
8. Evidence/Draft 在 cache hit 下仍执行校验；
9. 现有状态码、blocked sections、production guard 和报告生成测试全部通过。

运行：

```text
python -B -X utf8 -m unittest discover -s tests -v
python -B -X utf8 -m compileall -q .
python -X utf8 C:\Users\huangxiaoting\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
```

### 7.2 静态性能验收

对同一个当前代表性输入比较优化前后：

- 8 个 LLM 章节保持不变；
- 3 个 Worker 保持不变；
- 总正文字符预算不变；
- 最大 Worker pack 和负载不高于优化前；
- 规划恢复调用出现 `planning_cache_hit=true`；
- 草稿校验结果与章节覆盖保持有效。

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
- `internal/planning/draft_fragments.py`
- `internal/planning/stage.py`
- 必要时在 `internal/common.py` 增加通用内容摘要辅助函数
- `tests/test_chapter_rules_runtime.py`

明确不修改：

- `SKILL.md`
- `references/report_rules/writing_constraints.md`
- Schema 与状态码契约
- 最终报告章节结构和正文预算

