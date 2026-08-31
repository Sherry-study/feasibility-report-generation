# V0.14.9 瘦身性能版

- SKILL.md 从约 22 KB 收敛到约 5 KB，只保留执行契约。
- 默认 LLM 章节由 17 节下沉到最多 10 节；原料路线已确认不变、已有实施进度时可进一步减少。
- 草稿仅输出正文 + section 级证据 ID，不再要求 claims/open_items；加入章节长度预算。
- `llm_jobs.json` 改为最小校验契约；Research/Draft 不再生成 digest/plan 冗余文件。
- 成功后默认只保留报告、confirmed_project_facts、research_evidence（如有）和 run_summary；`--keep-runtime-artifacts` 可保留调试产物。

# 可研报告交付 Skill V0.14

发布运行包。核心架构为：`Project Profile → Source Capability → Adapter → Project Facts → Chapter Plan → Key-Fact Confirmation → Confirmed Facts → Tools/Research/LLM → Report Model → Consistency Gate → DOCX + Markdown`。

## 安装

```bash
pip install -r requirements.txt
```

## 运行 Skill

```bash
python run_skill.py \
  --workspace /path/to/project \
  --project-level unit \
  --project-type capacity_expansion \
  --output-dir outputs/project
```

`--profile` 为可选显式覆盖参数；省略时自动从算法输出推断并写入 `_resolved/project_profile.yaml`。

`project_level` 支持 `equipment / unit / system / plant`。

## 单独运行 Tool

```bash
python tools/engineering_facts/run.py --workspace D:\可研报告\算法输出纯净版 --project-level unit --output-dir outputs/stage1
python tools/chapter_planning/run.py --facts confirmed_project_facts.json --profile project_profile.yaml --chapter-plan chapter_plan.json --output-dir outputs/stage3
python tools/report_generation/run.py --facts confirmed_project_facts.json --profile project_profile.yaml --chapter-plan chapter_plan.json --section-drafts section_drafts.json --output-dir outputs/stage4
```

每个 Tool 的输入、输出和边界见对应 `TOOL.md`。



## V0.14 小版本变更

### V0.14.8 首跑性能优化（不改主流程语义）

- Research 从“每任务一个宿主派发单元”收敛为最多 **2 个固定 worker**：`market_long_pole` 独占市场研究，其余企业/政策/工期/技术等轻任务进入 `general_research`，减少 Trae 的任务分配与冷启动决策。
- 每个研究 task 新增 `search_budget`：市场默认最多2轮搜索/4个有效来源，其他任务默认1轮搜索/最多 `minimum_sources+1` 个来源；达到最小充分证据后必须立即停止。
- 1.1.3 政策背景改用包内固定政策基线（国发〔2024〕7号、工信部联规〔2024〕53号），首跑不再为通用工业技术改造政策单独联网；企业基本情况、实施进度基准最低来源数收敛为1个可靠一手/权威来源；4.1.2 技术综述由3源收敛为2源。
- 取消 `4.1.3` 父章节的独立 LLM job 与 `R-TECH-0413-01` 外部技术基准研究；方案比选正文仍由 `4.1.3.2` LLM + Report Model 确定性结构共同生成。
- Draft worker 仍默认最多3个并沿用现有负载均衡；提交改为**逐节部分接受**：同一 worker 仅失败章节需要重写，已通过章节立即持久化，避免整批返工。
- 同一 `output-dir` 已存在且通过当前任务集校验的 `research_evidence.json` 时自动复用（仅用于重复测试/续跑，不计入首跑性能收益）。
- Research HOST_WORKFLOW 明确要求 collect 后连续续跑 needs_llm，不在研究与写作之间返回主会话重新规划。

### V0.14.7 重点章节工程表达优化（不增加运行流程）

- 不修改 Skill 架构，不新增 Tool/Agent/Reviewer，不增加 LLM 调用。
- 1.2 改为“决策摘要”写法，减少诊断、设备和缺口的全量复述，并裁剪章节上下文。
- 4.1.3 移除“初筛/验证优先级/工程拓扑”等内部研发语言，方案比较只保留有事实依据的工程维度；推荐方案统一使用业务化名称。
- 4.2.1 继续由现有 Report Model 确定性生成，但由“设备节点串联”改为“物料流向 + 工艺操作 + 改造变化”的分段叙述。
- 26 章减少与1.2及前文章节重复，将“闭合/对象一致性”等系统状态语改为正常工程实施条件表达，并裁剪章节上下文。


### V0.14.5 第10章节能措施研究路由收敛

- 10.4“项目节能分析与措施”保留 `llm_narrative` 写作，但不再触发外部节能技术先进性/适用性对标检索；正文仅基于项目事实、已确认方案、报告内已有能耗数据组织语言。
- 取消 `R-ENE-104-01` 研究任务生成路径，避免容量扩建项目因 `utility_demand_changed=True` 误进入节能技术搜索。10.5 缺失能耗折标系数的兜底检索策略不变。

### V0.14.6 章节研究边界与项目画像自动推断

- 第18.2章保留 `R-IMP-0182-01` 外部工期基准研究，并继续由LLM组织章节正文；企业正式实施计划和项目设备工程量优先于公开参考。
- 第19章投资估算、第21章财务分析保留LLM写作，但取消投资价格、贷款利率等外部联网检索；固定表格和缺口说明由Report Model确定性生成。
- 未提供外部 `project_profile.yaml` 时，Source Resolver/Adapter 根据算法输出自动生成带来源/置信度的 `_resolved/project_profile.yaml`；显式 profile 仍可覆盖，低置信字段不自动写入正式结论。

### V0.14.4 needs_llm worker 包

- `needs_llm` 退出时默认生成最多 3 个 `draft_fragments/worker_contexts/worker_XX.json` 自包含 worker 包（任务少于 3 时不生成空包），而不是要求宿主逐节读取/提交。
- `draft_fragments/batch_plan.json` 改为 worker manifest：按瘦身后上下文增量与估算输出量确定性负载均衡，并记录每个 worker 的章节、估算输出量和包体积。
- 每个 worker 一次写入 `batch_worker_XX.json` 并执行一次 `--submit`；旧单草稿对象、裸 drafts 数组和历史分片仍兼容，collect/validate 语义不变。

### V0.14.3 能耗折标双体系

- 新增确定性折标规则库 `knowledge/energy_conversion_rules.json`（v0.3，唯一系数来源，禁止 LLM 生成/猜测系数）：**折标准煤**（GB/T 2589，默认，零交互）/ **折标准油**（GB/T 50441-2016，仅显式触发）双体系；蒸汽折油标按实际表压区间匹配（表3.0.8，不做四舍五入；可信压力等级标签可直接匹配），煤标蒸汽 128.6 kgce/t 依据 GB/T 2589-2008。
- 口径选择优先级：用户输入 > project_profile（新增 `energy_accounting_standard`）> 默认折标准煤；同一能耗计算表不混用两体系。
- 阶段②新增折标 Tool：输出 `energy_conversion_result.json` 并回写 `facts['fa']['energy_conversion']`（原 `energy_standard_coal` 键废弃）；`facts['energy']` 结构升级（accounting_standard / conversion_rules 全量规则快照）。
- 缺失介质生成 R-ENE-105 联网检索任务（查询词按所选体系动态生成）；10.5 能耗计算表按体系动态渲染（表列、系数、引用文字）。
- 旧 `knowledge/energy_conversion_factors.yaml`（煤标单一口径）已由新规则库替代并删除。

### V0.14.1–0.14.2 章节规则化

- 新增章节规则层 `knowledge/chapter_rules/`（`fact_binding_spec.yaml` + 各章规则 YAML）：定义章节所需工程事实、事实组装要求、正文结构、研究路由与 LLM 生成约束。
- 章节适用性/深度由 `internal/planning/chapters.py` 决定；章节输入/输出契约由 `internal/planning/chapter_rules.py` 加载并校验 `knowledge/chapter_rules/`。历史 `rules/` 目录归档至技能包上级 `_archive_v0.12/rules/`，运行时不读取。
- Chapter Plan 成为后半链路（Research/LLM/一致性检查）章节适用性的权威来源。

## V0.13 小版本变更

- 新增**增量草稿提交**通道（`needs_llm` 阶段可选，规避宿主 LLM 单次超长写入被截断）：新模块 `internal/planning/draft_fragments.py`，提供确定性分批计划与分片收集器 CLI。
- 当时 exit 11 阶段③自动写出旧版 `draft_fragments/batch_plan.json`：按顶层章节聚组 + 贪心合并；当前默认已由 V0.14.4 的 worker manifest 替代。
- 收集器逐片校验（复用逐节校验规则）、跟踪覆盖、跨片查重；本地退出码：`0` 完整且合并文件通过全量校验（写出 `section_drafts.json`）/ `2` 分片硬错误（报错定位到文件+章节）/ `4` 目前合法但覆盖不全（附缺失清单）。非法时绝不写出合并文件。
- `internal/report/validate_drafts.py` 重构：抽出 `validate_draft_entries`（逐节校验）供全量校验与分片收集器共用，`validate` 对外签名与报错措辞不变。
- 全量文件通道 `--section-drafts` 仍为主接口，分片仅为可选组装辅助；不改动任何阶段逻辑、退出码契约与 schema。

## V0.12 小版本变更

- 交互时序重排为：事实整理（阶段①，纯事实，不提问、不排章节）-> 工程事实确认 Gate（阶段②，纯净、紧邻整理）-> 确认后章节规划 + 缺口分析 + 编制信息一次性提问（阶段③ `deferred_questions`）-> Research/LLM 路由 -> 报告生成（阶段④）。章节计划（`chapter_plan.json`）与缺口分析（`gap_analysis.json`）改由阶段③在确认后基于 Profile 与确认后事实生成，阶段①不再产出；`gap_analysis` 的 `preflight_questions` 改为 `deferred_questions`：项目名称/建设单位/年运行时长等编制信息提问统一延后到确认 Gate 之后一次性提出，不再于阶段①前置打断（典型退出码序列 `12 -> 9 -> 10 -> 11 -> 0`）。
- 新增 `plant_info` Source Adapter：优先读取 `plant_info.annual_operating_hours`。有效值存在时直接进入 Project Facts 并运行年化 Tool；仅缺失/为空/非法时询问用户。
- 4.1.3 固定使用首次推荐阶段的完整候选池；后续优化属于同一方案内部迭代，不新增正式候选方案身份。
- 最终采用方案的报告描述优先使用最终改造拓扑及最终设备事实；不再把验证失败、优化轮次或算法试错历史作为可研缺口/正文内容。
- 4.2.4 设备三分类收紧：新增=新增设备；改造=原设备本体发生硬件改造；利旧=既有设备承担新的用途/位置/角色。`other`、运行参数优化、并联/串联方案中的原设备继续原用途等均视为设备本体无变化，不进入三张正式设备表。
- 编制前资料确认仍展示全部已识别设备及状态；正式 4.2.4 仅展示存在设备工程动作的对象。
- 增加可研表达深度控制：完整专业事实保留在 Project Facts，正文不机械倾倒算法中间量、失败历史和无决策价值细节。
- 新增 `knowledge/field_mapping.yaml`（算法输出字段 -> 工程事实路径词典，v1.2）与 `knowledge/algorithm_output_semantics.md`（字段语义权威）；字段提取以两份文档为准，冲突时以语义文件为准。
- 反应器报告采用方案规则：优先取算法输出 `selected_combined_scheme`（用户最终选定，含 `scheme_id`）；缺失时回退 `combined_schemes[]` + 顶层 `conclusion` 的算法推荐，标记 `pending_user_confirmation` 并记入一致性检查（`REACTOR_SCHEME_NOT_USER_CONFIRMED`）；选定与推荐数值可能有细微差异（如 52.89 vs 52.80 m³），不得混用。4.1-2 表推荐结论按 `selection_basis_source` 显示“选定/推荐”，4.1.3.3 正文以选定方案口径编制。
- `project_name` 合并规则（`project_name_source` 标记）：正式来源（`--project-name` 显式传入，`explicit`）不被用户输入覆盖；目录名兜底（`workspace_dir`）、空缺（`default`）或占位“待确认项目”时，`user_inputs.yaml` 的 `project.project_name` 优先（`user_input`，不触发阶段③ deferred 提问）。报告封面、research/llm 任务一律以 facts 中合并后的 `project_name` 为准。
- 保留 V0.11.3 的同一 Report Model 双输出机制：DOCX + Markdown；保留 V0.11.2 确认 Gate、V0.11.1 关键设备方案比较和第18章规则。第19–21章的投资/财务计算逻辑仍未实现，但当前版本已明确其研究与写作路由。

## V0.11.3 小版本变更

- 报告导出新增 Markdown renderer。
- 最终生成阶段同时输出 `可行性研究报告_初稿.docx` 与 `可行性研究报告_初稿.md`。
- 两种格式严格消费同一个 `report_model.json`，不会由 LLM 分别撰写，避免格式间内容漂移。
- Markdown 用于 Agent/研发全文搜索、Diff、章节检查；DOCX 继续作为正式阅读与交付格式。
- 保留 `--output` 作为仅导出 DOCX 的兼容参数，并新增 `--docx-output` / `--markdown-output`。
- 本版本不修改可研业务规则、关键事实确认、18章、19-21章计算逻辑。

## V0.11.2 小版本变更

- 新增正式编制前 `needs_confirmation` 人工确认状态。
- 自动输出 `report_confirmation.json`，展示：推荐方案、经济性摘要、全部设备清单、关键改造点。
- 经济性摘要口径固定为：项目总投资、年均利润总额、静态投资回收期（含建设期）、税后 IRR；缺结果时必须显示“待计算”。
- 用户确认后冻结为 `confirmed_project_facts.json`，后续 Report/Research/LLM 使用确认后的基线。
- 支持 `--confirm-as-is` 或 `--confirmation-response` 两种续跑方式。
- 保留 V0.11.1 的关键设备方案比较与第18章七阶段实施计划。
- 本小版本仍不修改第19-21章投资/资金/财务计算逻辑。


## 编制前确认示例

首次运行到关键事实确认时返回 exit code 12。确认无修改后：

```bash
python run_skill.py ... --confirm-as-is
```

如用户有修正，按 `schemas/report_confirmation_response.schema.json` 形成响应后：

```bash
python run_skill.py ... --confirmation-response confirmation_response.json
```
