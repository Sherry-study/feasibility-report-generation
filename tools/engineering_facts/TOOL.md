# Engineering Facts Tool（阶段①：工程事实）

## 用途
单一入口完成工程事实阶段：来源登记（Source Registry）-> 项目 Profile 解析 -> 已解析输入清单 -> 用户输入解析 -> 事实构建（legacy 全栈 / generic 部分）。等价于 run_skill.py 阶段①（L91-108）的进程内编排。本阶段不向用户提问、不排章节、不做章节级缺口分析：确认 Gate（阶段②）紧邻事实整理，章节规划与编制信息一次性提问（`deferred_questions`）统一在阶段③确认通过后执行。

## 独立调用
```bash
python tools/engineering_facts/run.py \
  --workspace <workspace目录> \
  --project-level unit --project-type capacity_expansion \
  --construction-unit <建设单位> \
  --output-dir <输出目录>
```

## 参数
- `--workspace` / `--source-manifest`：二选一必填；
- `--profile`：可选显式覆盖；其次取 source_manifest 内嵌 project_profile 或 workspace 中唯一的显式 profile；未提供时由 Source Resolver/Adapter 从算法输出自动推断，无法可靠推断的字段才落 conservative 兜底；
- `--project-name/--project-id/--project-level/--project-type`：来源登记元信息；
- `--user-inputs/--construction-unit/--annual-operating-hours/--project-location`：用户输入与 CLI 覆盖（写入 `_resolved/user_inputs.yaml`）；
- `--skip-user-inputs`：兼容保留（本阶段已无提问门槛；编制信息提问门槛在阶段③ `chapter_planning`）；
- `--output-dir`：必填，产物写入该目录。

## 产物
`_resolved/source_registry.json`、`_resolved/project_profile.yaml`（自动推断或显式覆盖后的内部画像）、`_resolved/resolved_input_manifest.yaml`、`_resolved/user_inputs.yaml`（如适用）、`project_facts.json`。`chapter_plan.json` 与 `gap_analysis.json` 由阶段③（确认后）产出，本阶段不再生成。

## 退出码
- `0`：facts_ready，阶段完成；
- `2`：needs_resolution，来源登记未就绪。

编制信息缺失（项目名称/建设单位/年运行时长）不在本阶段打断：先进入阶段②确认 Gate；仍未补齐的在阶段③确认后以 `deferred_questions` 一次性提出（exit 9）。

stdout 输出 JSON 摘要（status、产物路径等），不落盘 run_summary。
