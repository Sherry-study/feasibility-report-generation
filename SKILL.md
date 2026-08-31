---
name: feasibility-report-generation
description: 可研交付：基于已确认工程事实和固定章节规则，生成设备级、装置级、系统级、全厂级改造项目可行性研究报告（DOCX/Markdown）。用户要求生成可研报告/可行性研究报告时调用。
---

# 可研报告交付 Skill

**版本：V0.14.9（瘦身性能版）**

目标：把上游专业结果整理为可确认的 Engineering Facts，按固定章节规则生成可研报告。**不替代专业计算，不自由改变报告结构，不创造工程数字。**

## 执行主链

```text
来源/算法输出
→ Engineering Facts
→ 关键事实确认
→ Chapter Plan + Gap
→ 必要的 Research
→ 必要的 LLM 章节
→ 确定性表格/拓扑/正文
→ 一致性检查
→ DOCX + Markdown
```

`run_skill.py` 是默认入口；四个阶段 Tool 仍可单独调用。

## 最小调用

```bash
python run_skill.py \
  --workspace /path/to/project \
  --project-level unit \
  --project-type capacity_expansion \
  --output-dir outputs/project
```

`--profile` 可显式覆盖自动识别的项目画像。调试时如需保留全部中间产物，加 `--keep-runtime-artifacts`；默认成功后自动清理运行时文件。

## 状态与接力

宿主只看 `run_summary.json.status` 和 `next_action`，**不要自行扫描输出目录或重新规划流程**。

| 状态/退出码 | 动作 |
|---|---|
| `needs_resolution` / 2 | 解决来源缺失或高置信版本冲突后重跑 |
| `needs_confirmation` / 12 | 用户确认关键事实；用 `--confirm-as-is` 或 `--confirmation-response` 重跑 |
| `needs_user_input` / 9 | 一次性补项目名称、建设单位、年运行时长等编制信息后重跑 |
| `needs_research` / 10 | **只执行 `research_fragments/HOST_WORKFLOW.md`** |
| `needs_llm` / 11 | **只执行 `draft_fragments/HOST_WORKFLOW.md`** |
| `consistency_blocked` / 3 | 按明确错误修正后重跑 |
| `generated` / 0 | 报告已生成 |

Research/Draft 两个 HOST_WORKFLOW 都要求：固定 worker、一次性并行派发、失败仅局部重试、完成后立即续跑 Skill；宿主不得在阶段之间总结或重新规划。

## 核心规则

1. **Engineering Facts 是正文事实底座。** 文件名、算法 JSON 结构和存储方式只属于 Resolver/Adapter；章节生成不直接读取原始算法文件。
2. **数字来源仅限**：上游专业结果、确定性 FA 计算、用户确认输入。LLM 不生成新数字。
3. `candidate recommendation ≠ adopted scheme`。正文只使用最终确认方案；算法试错、失败方案迭代和内部状态默认不写入报告。
4. Chapter Plan 决定章节适用性；`knowledge/chapter_rules/` 决定每章可消费事实、Research 路由和输出要求。disabled/not-applicable 章节不得触发 Research/LLM。
5. LLM 只承担真正需要归纳/论证的章节。可由事实、表格或固定工程语言表达的章节必须确定性渲染。
6. 正文禁止出现 Evidence ID、URL、JSON、Skill、Provider、PA/EA/FA、工程化拓扑等内部实现词。
7. 4.1.3 只写工程方案比较与推荐理由，不写候选评分、验证优先级、算法排序或内部拓扑推理。
8. 4.2.1 按物料流向和工艺段说明流程，不按设备节点机械串联。
9. 1.2 是决策摘要；26 章是全报告最终综合判断，二者不得大段重复。
10. 缺失工程条件保持缺口，不用公开资料替代企业实际数据；缺投资/财务输入时不生成推测金额或评价指标。

## 性能约束

- Research 只寻找“最小充分证据”：遵守 worker pack 的 `search_budget`，满足 `minimum_sources` 且足以支撑正文后立即停止。
- Draft 默认最多 3 个 worker；worker 只读自己的 context pack，禁止读取全量 `llm_jobs.json` / `project_facts.json`。
- 简单章节已下沉为确定性正文；LLM 章节包含 `length_budget_chars`，优先短、清楚、无重复。
- LLM 草稿只输出 `section_id / draft_status / subsections / source_evidence_ids`；不要求重复生成 claims/open_items。
- `llm_jobs.json` 仅保存草稿校验所需的最小契约，不再复制全量上下文。
- 同一输出目录存在有效 `research_evidence.json` 时允许自动复用；该缓存只用于重复运行提速，不作为首跑性能依赖。

## 关键工程口径

- 年运行时长优先取专业输出有效值；缺失时一次性询问用户。
- 4.2.4：`新增`=新增设备；`改造`=既有设备本体发生结构/规格/能力变化；`利旧`=既有设备改变用途/位置/工程角色。继续原用途且本体不变的设备不进入三张设备表。
- 能耗折标由确定性 Tool 执行，系数唯一来源为 `knowledge/energy_conversion_rules.json`；LLM 不生成或修改折标系数。
- 正式生产模式拒绝测试 Evidence、测试正文和测试标记。

## 最终输出

成功生成后默认只保留：

- `可行性研究报告_初稿.docx`
- `可行性研究报告_初稿.md`
- `confirmed_project_facts.json`
- `research_evidence.json`（存在联网证据时；同时可作为重复运行缓存）
- `run_summary.json`

Research、Draft、规划、校验、Report Model 等运行时文件仅在执行期间存在；成功后自动清理。需要排障时使用 `--keep-runtime-artifacts` 保留全部中间产物。
