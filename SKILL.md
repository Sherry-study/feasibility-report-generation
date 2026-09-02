---
name: feasibility-report-generation
description: 可研交付：基于确认后的 Engineering Facts 和固定章节规则，指导宿主 Agent 通过 MCP Tools 生成设备级、装置级、系统级、全厂级改造项目可行性研究报告（DOCX/Markdown）。用户要求生成可研报告/可行性研究报告时调用。
---

# 可研报告交付 Skill

目标：把上游专业结果整理为可确认的 Engineering Facts，并在确认后按固定章节规则生成可研报告。不要替代专业计算，不要自由改变报告结构，不要创造工程数字。

## Agent 主流程

按顺序调用宿主提供的 MCP Tools：

```text
来源/算法输出
→ engineering_facts
→ engineering_confirmation
→ 用户确认关键事实
→ chapter_planning
→ 必要 Research
→ 必要章节草稿
→ report_generation
→ DOCX + Markdown
```

四个 Tool 的名称、输入字段和输出结构由 MCP Server 与 schemas 定义；Skill 只指导 Agent 如何调用和约束报告行为。

## 必须遵守

1. Engineering Facts 是正文事实底座。章节生成不得直接读取原始算法文件。
2. 正文数字只能来自上游专业结果、确定性计算或用户确认输入。LLM 不生成新数字。
3. `candidate recommendation` 不等于 `adopted scheme`。正文只使用最终确认方案。
4. Chapter Plan 决定章节适用性；`references/chapter_rules/` 决定每章可消费事实、Research 路由和输出要求。
5. disabled / not-applicable 章节不得触发 Research 或 LLM 章节草稿。
6. LLM 只处理真正需要归纳、论证或摘要的章节；可由事实、表格或固定工程语言表达的内容应由确定性逻辑生成。
7. 报告正文不得出现 Evidence ID、URL、JSON、Skill、Provider、PA、EA、FA、worker、cache、内部拓扑等实现词。
8. 缺失工程条件保持缺口，不得用公开资料替代企业实际数据。
9. 缺投资、财务、价格、收益、回收期、IRR 等输入时，不生成推测金额或评价指标。
10. 正式生产模式拒绝测试 Evidence、测试正文和测试标记。

## 参考规范选择

只在需要对应规则时读取 reference：

- 章节规则、允许标题、章节覆盖类别、Research 路由：`references/chapter_rules/`
- 算法输出字段语义与字段映射：`references/engineering_rules/algorithm_output_semantics.md`、`references/engineering_rules/field_mapping.yaml`
- 设备新增/改造/利旧/无变化分类：`references/engineering_rules/equipment_rules.md`
- 年化、能耗折标、财务缺口等计算口径：`references/engineering_rules/calculation_rules.md`、`references/engineering_rules/energy_rules.md`
- 内容来源边界、历史案例复用与项目事实污染防护：`references/report_rules/content_origin_policy.md`
- 报告正文写作边界、内部词屏蔽、重点章节表达约束：`references/report_rules/writing_constraints.md`
- 运行时 worker、cache、artifact 保留策略：`references/runtime_policy.md`

## 确认门

`engineering_confirmation` 生成关键事实确认材料后，必须让用户明确确认或修正。确认前不得进入章节正文生成。

确认后，后续 `chapter_planning`、Research、章节草稿和 `report_generation` 只消费确认后的事实基线。

## 最终输出

成功后向用户交付：

- `可行性研究报告_初稿.docx`
- `可行性研究报告_初稿.md`
- 关键事实与证据相关 JSON（按 Tool 返回路径）

如果 Tool 返回 `needs_confirmation`、`needs_user_input`、`needs_research`、`needs_llm` 或 `consistency_blocked`，按 `next_action` 继续推进，不自行改写流程或绕过门禁。
