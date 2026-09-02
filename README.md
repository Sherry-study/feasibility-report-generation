# feasibility-report-generation

标准 Agent Skill + MCP Tool 包，用于把上游专业结果整理为可确认的 Engineering Facts，并在确认后生成可研报告 DOCX/Markdown。

本仓库不再提供独立 Workflow Runner。执行流程由宿主 Agent 根据 `SKILL.md` 调用 MCP Tools 完成。

## 架构

```text
SKILL.md
references/
src/
internal/
schemas/
prompts/
scripts/
tests/
```

## MCP 工具

Tool 接口保持不变：

- `engineering_facts`
- `engineering_confirmation`
- `chapter_planning`
- `report_generation`

`src/` 是 MCP Tool 入口；`internal/` 是 Tool 执行实现；`schemas/` 是 Tool 之间的数据契约。

## 使用方式

宿主 Agent 应按 `SKILL.md` 的主流程推进：

```text
engineering_facts
→ engineering_confirmation
→ 用户确认
→ chapter_planning
→ Research / LLM 草稿（如需要）
→ report_generation
```

不要绕过 `engineering_confirmation` 的人工确认门。未确认的投资、收益、回收期、IRR、产能、能耗等数字不得写入正文。

## 参考规范

- `references/chapter_rules/`: 章节规则、允许标题、章节覆盖类别、Research 路由。
- `references/engineering_rules/`: 算法字段语义、字段映射、设备分类、能耗折标、计算口径。
- `references/report_rules/`: 报告正文写作边界、内部词屏蔽、生产安全约束。
- `references/runtime_policy.md`: worker、cache、运行时 artifact 等宿主运行策略。

## 辅助脚本

`scripts/` 只放确定性辅助动作，不接管四阶段业务编排：

- `scripts/validate_schema.py`: 校验 JSON/YAML 文件可解析，并在可用时用 `jsonschema` 校验实例。
- `scripts/validate_output.py`: 检查报告输出目录中的关键交付物和 JSON 文件。
- `scripts/package_result.py`: 将报告交付物和可复用证据文件打包为 zip。

## 验证

```bash
python -B -X utf8 -m unittest discover -s tests -v
python -B -X utf8 -m compileall -q .
python -X utf8 C:\Users\huangxiaoting\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
git diff --check
```
