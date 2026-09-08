# feasibility-report-generation

标准 Agent Skill + MCP Tool 包，用于把上游专业结果整理为 Engineering Facts，并通过报告工作包组织 Agent 研究、写作和最终 DOCX/Markdown 输出。

本仓库不再提供独立 Workflow Runner。执行流程由宿主 Agent 根据 `SKILL.md` 调用 MCP Tools 完成。

## 架构

```text
SKILL.md
references/
src/
schemas/
prompts/
scripts/
tests/
```

## MCP 工具

公开 MCP Tool 只有两个：

- `engineering_facts`
- `report_generation`

`src/engineering_facts/` 和 `src/report_generation/` 是正式业务核心；`schemas/`、`prompts/`、`references/` 为两 Tool 共享的契约与规则来源。

## 使用方式

宿主 Agent 应按 `SKILL.md` 的主流程推进：

```text
engineering_facts
→ report_generation(operation=prepare)
→ Agent Research / Writing / Synthesis
→ report_generation(operation=finalize)
```

`engineering_facts` 输入只接受 `source_location={provider:"local_directory", location:"..."}` 和可选 `construction_unit`。`report_generation` 通过 `operation=prepare|finalize` 分支接收 `engineering_facts_uri/report_context`，或必填的 `work_package_uri` 加可选的 Agent 工作结果（`work_results_uri` 文件或 `work_results` 对象，二选一）；不提供 Agent 结果时 finalize 仍会生成由模板 fallback 兜底的不完整初稿，并在 diagnostics 中提示。

未确认或未出现在 Engineering Facts / 工作包 / Agent 结果中的投资、收益、回收期、IRR、产能、能耗等数字不得写入正文。

## 参考规范

- `references/chapter_rules/`: 旧四阶段规则保留参考；正式两 Tool 运行优先使用 `src/report_generation/rules/`。
- `references/engineering_rules/`: 算法字段语义、字段映射、设备分类、能耗折标、计算口径。
- `references/report_rules/`: 报告正文写作边界、内部词屏蔽、生产安全约束。
- `references/runtime_policy.md`: worker、cache、运行时 artifact 等宿主运行策略。

## 辅助脚本

`scripts/` 只放确定性辅助动作，不接管 Agent 研究和写作：

- `scripts/run_engineering_facts.py`: 本地手动运行工程事实整理。
- `scripts/run_report_generation.py`: 本地手动运行 prepare/finalize 链路。
- `scripts/validate_schema.py`: 校验 JSON/YAML 文件可解析，并在可用时用 `jsonschema` 校验实例。
- `scripts/validate_output.py`: 检查报告输出目录中的关键交付物和 JSON 文件。
- `scripts/package_result.py`: 将报告交付物和可复用证据文件打包为 zip。

## 验证

```bash
python -B -X utf8 -m unittest discover -s tests -v
python -B -X utf8 -m compileall -q src mcp_server scripts tests
python -B -X utf8 -c "import asyncio; from mcp_server.server import mcp; print([tool.name for tool in asyncio.run(mcp.list_tools())])"
npm run build --prefix mcp_server/engineering-confirmation-ui/ui
npm run build --prefix mcp_server/engineering-confirmation-ui/host
npm run build --prefix mcp_server/report-generation-ui/ui
npm run build --prefix mcp_server/report-generation-ui/host
python -X utf8 C:\Users\huangxiaoting\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
git diff --check
```
