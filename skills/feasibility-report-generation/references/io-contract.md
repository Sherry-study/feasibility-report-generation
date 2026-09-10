> 何时读本文件：`feasibility-report-generation` 进入执行前核对工程输入是否齐备；调用任一工具前核对入参；在阶段间传递 `artifact.path` 或保存章节工作结果前核对交接字段。

# 输入 / 输出契约：可研报告生成

版本：v1（新建，与 SKILL.md 工具层描述对齐）

## 输入契约（engineering_facts 入口）

入参为单个 `input` 对象：`input.provider`（`local_directory` / `host_directory`）、`input.root`（本地目录或宿主逻辑目录前缀）、`input.file_overrides`（非标准文件名覆盖）、`input.construction_unit`（建设单位，可选，不自动推断）。

| 输入文件（默认名） | 来源 | 必填性 | 缺失时行为 |
|---|---|---|---|
| `plant_reactor_result.json` | 反应器算法 | 可选 | 跳过；plant_level 含反应器时 warning `REACTOR_RESULT_MISSING` |
| `retrofit_tower_equipment.json` | 塔器算法 | 可选 | 跳过；plant_level 含塔时 warning `TOWER_RESULT_MISSING` |
| `scheme.json` | 装置级诊断/方案生成 | 可选 | 跳过，`scheme_analysis`/`adopted_scheme` 为空；有候选但缺 `user_selected` 时 warning `USER_SELECTED_MISSING` |
| `plant_info.json` | 装置信息输入 | 可选 | `unit` 段为空；年化派生事实无法计算 |
| `plant_diagnosis_report.md` | 装置级诊断 | 可选 | `diagnosis` 段为空 |
| `new_device_params.json` | 设备设计 | 可选 | 跳过；方案含新增设备时 warning `NEW_DEVICE_PARAMS_MISSING` |
| `retrofit_equipment.json` | 设备台账 | 可选 | `equipment.object_catalog` 为空 |
| `retrofit_topology.json` | 拓扑生成 | 可选 | 改造工况沿用 plant_level 自带拓扑 |
| `plant_level_result.json` | 装置级衡算 | 可选 | `process` 段为空 |

- 输入文件均为可选，但**至少须识别出一个来源**（按内容签名识别，不依赖文件名），否则 fatal `NO_RECOGNIZED_SOURCES`。
- 来源识别与字段语义见 `references/engineering_rules/algorithm_output_semantics.md`。

## 阶段间交接契约

| 交接 | 上一步返回 | 下一步入参 |
|---|---|---|
| engineering_facts → report_prepare | `data.artifact.path` | `input.engineering_facts_path` |
| report_prepare → 章节生成（宿主 Agent） | `data.artifact.path` 及 `chapter_contexts/*.json` | 章节 Agent 按 `writing_tasks[].context_path` 读取事实切片，产出并保存 `work_results.json` |
| report_finalize | — | `input.work_package_path` 必填；`input.work_results_path` 可选，不传时生成带 fallback 标记的未闭合草稿（warning `WORK_RESULTS_NOT_PROVIDED` / `SECTION_FALLBACK`） |

## 输出契约

| 产物 | 落点 | 结构要点 | 交接字段 |
|---|---|---|---|
| `engineering_facts.json` | `runs/engineering_facts/{stamp}/` | 11 个固定一级键：`meta`、`sources`、`basic_info`、`unit`、`component_catalog`、`process`、`diagnosis`、`scheme_analysis`、`adopted_scheme`、`equipment`、`derived_facts` | `data.artifact.path` → `report_prepare.input.engineering_facts_path` |
| `work_package.json` | `runs/report_prepare/{stamp}/` | `sections`、`research_tasks`、`writing_tasks`、`deterministic_summaries`、`synthesis_tasks` | `data.artifact.path` → `report_finalize.input.work_package_path` |
| `chapter_contexts/{section_id}.json` | `runs/report_prepare/{stamp}/chapter_contexts/` | `{section_id, fact_slice}` 章节事实切片 | `writing_tasks[].context_path`；供章节 Agent 消费 |
| `work_results.json` | 宿主保存（逻辑路径由 Agent 决定） | `schema_version`、`writing_results`、`synthesis_results`（blocks 三种类型、六字段 section_summary、18.2 专属 implementation_schedule） | 路径填入 `report_finalize.input.work_results_path` |
| `可行性研究报告_初稿.md` / `.docx` | `runs/report_finalize/{stamp}/` | 报告正文，DOCX 含 A4 版式与自动目录域 | `data.artifacts.markdown_path` / `docx_path`；终点交付 |
| `report_manifest.json` | `runs/report_finalize/{stamp}/` | markdown/docx 的路径、media_type、sha256、size 及 `fallback_section_count` | `data.manifest_path`；唯一有效提交标志 |

## 契约纪律

- 缺失必填输入（含至少一个可识别来源）时按表内行为处理，不猜测补全。
- `artifact.path` 均为宿主逻辑路径，不得填写本地文件系统路径。
- `work_results_path` 提供但内容非法时业务 failed（`REPORT_FINALIZE_WORK_RESULTS_SCHEMA_INVALID`），不静默降级；单个章节 blocks 不合格仅该章节降级为 fallback。
- 占位值或假设值必须随值标注来源，并在下游传递。
- 交接字段以工具返回 `data` 字段为准；本文件记录次之；示例仅作参考。
