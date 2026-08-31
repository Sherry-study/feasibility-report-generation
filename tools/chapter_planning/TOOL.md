# Chapter Planning Tool（阶段③：章节级研究路由）

## 用途
单一入口完成章节级研究路由阶段：**基于确认后事实与 Profile 生成章节计划（确认 Gate 之后生成，即最终版本）** -> 缺口分析 -> **编制信息一次性提问（`deferred_questions`，写作之前一次性收集项目名称/建设单位/年运行时长）** -> 生成外部研究任务 -> 宿主代理路由（补研究证据 / 补章节草稿）。等价于 run_skill.py 阶段③（L141-164）的进程内编排。

## 独立调用
```bash
# 首轮：生成研究任务（exit 10 表示等待宿主补研究证据）
python tools/chapter_planning/run.py \
  --facts <输出目录>/confirmed_project_facts.json \
  --profile <project_profile.yaml> \
  --output-dir <输出目录>

# 提供 --research-evidence 后重跑（exit 11 表示等待宿主 LLM 草稿）
# 再提供 --section-drafts 后重跑 -> exit 0
```

## 参数
- `--facts`：必填，`confirmed_project_facts.json`；
- `--profile`：必填，章节计划由本阶段基于 Profile（层级/类型/变更边界）确定性生成；
- `--output-dir`：必填；
- `--research-evidence` / `--section-drafts`：宿主提供的外部证据与章节草稿；
- `--ai-mode`：`host_agent`（默认）/ `disabled`；disabled 时 `llm_jobs.json` 写空 jobs；
- `--run-mode`：`production`（默认）/ `test`（要求环境变量 `FEASIBILITY_SKILL_DEV_TEST=1`）；
- `--skip-user-inputs`：跳过编制信息一次性提问（`deferred_questions`），保留缺口继续；仅当用户明确无法提供时使用。

## 产物
`chapter_plan.json`、`gap_analysis.json`、`research_tasks.json`、精简版 `llm_jobs.json`；needs_research 时仅额外生成 `research_fragments/worker_contexts/` 与 `HOST_WORKFLOW.md`；needs_llm 时仅额外生成 `draft_fragments/worker_contexts/` 与 `HOST_WORKFLOW.md`。提供证据/草稿后生成相应校验结果；最终成功时运行时文件默认清理。

## 退出码
- `0`：planning_ready；
- `9`：needs_user_input，存在 `deferred_questions`（项目名称/建设单位/年运行时长等编制信息缺失）。宿主应把 user_questions 转达用户一次性收集，答案写入 `user_inputs.yaml` 后携带 `--user-inputs` 与原确认参数重跑；
- `10`：needs_research（存在研究任务但未提供 `--research-evidence`）；
- `11`：needs_llm（存在 LLM 任务但未提供 `--section-drafts`；宿主按 `draft_fragments/HOST_WORKFLOW.md` 使用 worker 包完成草稿并提交）。
