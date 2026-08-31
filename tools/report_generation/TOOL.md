# Report Generation Tool（阶段④：报告生成）

## 用途
单一入口完成报告生成阶段：工艺拓扑分析 -> 报告表格 -> 报告模型 -> 一致性门槛 -> DOCX/Markdown 导出 -> 报告溯源。等价于 run_skill.py 阶段④（L143-144 前置 + L166-178）的进程内编排。

## 独立调用
```bash
python tools/report_generation/run.py \
  --facts <输出目录>/confirmed_project_facts.json \
  --profile <project_profile.yaml> \
  --chapter-plan <输出目录>/chapter_plan.json \
  --section-drafts <section_drafts.json> \
  --output-dir <输出目录>
```

## 参数
- `--facts`：必填，`confirmed_project_facts.json`；
- `--profile` / `--chapter-plan`：必填；
- `--output-dir`：必填；
- `--section-drafts`：可选，阶段③产物章节草稿；
- `--strict-consistency`：将上游高严重性事实冲突升级为阻断。

## 产物
`process_topology_analysis.json`、`report_tables.json`、`report_model.json`、`consistency_check.json`、`可行性研究报告_初稿.docx`、`可行性研究报告_初稿.md`、`report_trace.json`。

## 退出码
- `0`：generated；
- `3`：consistency_blocked，一致性检查 failed（summary 含 issues）。
