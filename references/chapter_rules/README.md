# Chapter Rules

本目录是旧四阶段运行链路的归档参考，不再作为正式两 Tool 运行时入口。

正式运行使用 `src/report_generation/rules/chapter_rules.json`。如需调整当前报告章节、模板、动态槽位或章节工作包，应优先修改正式 `src/report_generation/rules/` 下的规则文件，并通过 `report_generation.prepare/finalize` 验证。

旧四阶段章节规则层曾定义：

- 章节需要的工程事实
- 工程事实到章节上下文的组装要求
- 正文结构
- LLM生成约束
- 质量检查要求

Chapter Rules 不定义算法字段语义，字段语义由 `references/engineering_rules/algorithm_output_semantics.md` 负责。

## coverage_class（章节覆盖类别）

| 取值 | 含义 | 是否进入 llm_jobs |
|---|---|---|
| `llm_narrative` | 需要结合项目事实/研究证据撰写的叙述性章节 | 是 |
| `template_rendered` | 规范引用型章节：正文以规范引用、依托既有条件与专业校核口径为主，由 Report Model 确定性模板文字渲染 | 否 |
| `deterministic_only` | 仅由确定性工具/表格渲染 | 否 |
| `plan_parent_or_range` | 章节计划中的父级或区间条目（如 `10.1-10.6`），本身无规则体 | 否 |

`template_rendered` 的判定标准：research 均为 none、required_structure 以规范引用/条件保留类表述为主、LLM 撰写不产生项目特异叙述价值。旧集合曾由四阶段实现维护；当前正式集合以 `src/report_generation/rules/chapter_rules.json` 为准。历史项目 `section_drafts.json` 中这些章节的既有草稿仅用于理解旧产物，不再进入正式运行链路。
