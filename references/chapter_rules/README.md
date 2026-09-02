# Chapter Rules

章节规则层定义：

- 章节需要的工程事实
- 工程事实到章节上下文的组装要求
- 正文结构
- LLM生成约束
- 质量检查要求

Chapter Rules 不定义算法字段语义，字段语义由 references/engineering_rules/algorithm_output_semantics.md 负责。

## coverage_class（章节覆盖类别）

| 取值 | 含义 | 是否进入 llm_jobs |
|---|---|---|
| `llm_narrative` | 需要结合项目事实/研究证据撰写的叙述性章节 | 是 |
| `template_rendered` | 规范引用型章节：正文以规范引用、依托既有条件与专业校核口径为主，由 Report Model 确定性模板文字渲染 | 否 |
| `deterministic_only` | 仅由确定性工具/表格渲染 | 否 |
| `plan_parent_or_range` | 章节计划中的父级或区间条目（如 `10.1-10.6`），本身无规则体 | 否 |

`template_rendered` 的判定标准：research 均为 none、required_structure 以规范引用/条件保留类表述为主、LLM 撰写不产生项目特异叙述价值。当前集合见 `internal/planning/chapter_rules.py` 的 `TEMPLATE_RENDERED_SECTIONS`（4.4、7.1、7.2、7.3、10.3、10.6）。历史项目 `section_drafts.json` 中这些章节的既有草稿会被草稿校验器忽略（不报错、也不再必需）。
