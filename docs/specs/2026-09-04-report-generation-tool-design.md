# 报告准备与定稿 Tool 设计规格

更新时间：2026-09-09

## 1. 目标

在正式 `src/report_prepare`、`src/report_finalize` 和 `src/report_shared` 内实现报告流程。Tool 只承担可由固定输入、
固定规则稳定复现的确定性工作；研究、资料判断、章节写作和综合写作由 Skill
编排的 Agent 完成。

MCP Server 分别公开 `report_prepare` 与 `report_finalize`，每个 Tool 对应独立核心入口；
共享报告算法位于 `src/report_shared`，不存在 `operation` 路由或旧核心兼容包。

## 2. 总体流程

```text
Skill 确认 engineering_facts.json
                |
                v
          report_prepare
                |
                v
          work_package.json
                |
                v
Agent 自主获取资料、并行写作、生成综合章节
                |
                v
          work_results.json
                |
                v
          report_finalize
                |
                v
       DOCX + Markdown + manifest
```

Tool 不依赖历史会话。工程事实确认是 Skill 行为，不是 Tool 状态。

## 3. Tool 职责

### 3.1 prepare

`prepare` 负责：

- 读取并校验 `engineering_facts.json`；
- 按固定优先级确定报告项目名称；
- 加载并校验结构化报告模板和章节契约；
- 按契约中的事实路径构造章节级事实切片；
- 生成固定文本、确定性事实段落和确定性表格；
- 根据模板中的槽位生成 Research、Writing 和 Synthesis 任务；
- 先完整生成并校验工作包，经 HostClient 保存全部章节 context，最后保存作为提交标志的 `work_package.json`。

Research 任务只描述研究目标。Tool 不判断是否联网，也不规定资料来源。

### 3.2 finalize

`finalize` 负责：

- 读取并校验 `work_package.json` 和 `work_results.json`；
- 校验章节 ID 和正文块结构；
- 忽略 Agent 返回的标题、表号和目录控制信息；
- 对缺失或非法的动态章节使用模板指定的确定性 fallback；
- 按模板顺序装配报告模型；
- 本地临时导出并校验 DOCX/Markdown，经 HostClient 保存、回读核验 hash/size，最后保存 manifest。

`finalize` 不做语义可信度判断，不生成修复任务，也没有第二轮 finalize。

新 Tool 不得直接调用旧四阶段报告模型构造正文。旧模型读取
`project/user/enterprise/energy/fa` 等旧事实根，与新版工程事实 Schema 不兼容。
导出器可以参考旧实现的版式，但新报告模型只能读取新版 Engineering Facts。

## 4. Agent 职责

Agent 负责：

- 判断已有资料是否足够；
- 自主选择本地资料、历史报告、既有证据或联网检索；
- 判断资料是否适用于当前项目；
- 生成普通章节正文和 `section_summary`；
- 仅根据 `section_summary` 生成综合章节；
- 提交前自行检查事实一致性、无依据数字和内部术语。

Tool 不搜索历史报告、不管理跨项目知识库或缓存。

## 5. 接口

### 5.1 report_prepare 输入

```json
{
  "engineering_facts_path": "runs/engineering_facts/.../engineering_facts.json",
  "project_name": "可选"
}
```

跨 Tool 产物均为 HostClient 逻辑 path。FastMCP 将 `project_name` 映射到核心内部的 `report_context`。

项目名优先级：

1. `report_context.project_name`；
2. 建设单位 + 装置名称 + `技术改造项目`；
3. 装置名称 + `技术改造项目`；
4. `工业装置技术改造项目`。

### 5.2 report_prepare 输出

```json
{
  "status": "prepared",
  "artifact": {"path": "runs/report_prepare/.../work_package.json"},
  "summary": {
    "research_task_count": 0,
    "writing_task_count": 0,
    "deterministic_summary_count": 0,
    "synthesis_task_count": 0
  },
  "diagnostics": []
}
```

### 5.3 report_finalize 输入

```json
{
  "work_package_path": "runs/report_prepare/.../work_package.json",
  "work_results_path": "workspace/work_results.json"
}
```

- `work_package_path` 必填，是 finalize 的唯一必填启动条件。
- `work_results_path` 可选；公开接口不接受 inline `work_results`。
- 未提供工作结果时，使用空结果继续生成报告，不视为失败；Agent 章节使用模板 fallback，并返回 `WORK_RESULTS_NOT_PROVIDED` warning。
- 传入路径但内容格式非法时直接失败，不静默降级为空结果。

### 5.4 report_finalize 输出

```json
{
  "status": "completed",
  "artifacts": {
    "docx_path": "runs/report_finalize/.../可行性研究报告_初稿.docx",
    "markdown_path": "runs/report_finalize/.../可行性研究报告_初稿.md"
  },
  "manifest_path": "runs/report_finalize/.../report_manifest.json",
  "summary": {
    "section_count": 0,
    "fallback_section_count": 0
  },
  "diagnostics": []
}
```

状态只使用 `prepared`、`completed`、`failed`。

prepare 中 `work_package.json` 是章节 context 组的提交标志。finalize 中 manifest 是唯一有效提交标志：两份报告文件必须位于同一内部唯一逻辑路径前缀，manifest 记录各自 path、SHA-256 和 size；保存 manifest 前必须通过 HostClient 回读复核。取消或异常可留下不可达孤儿文件，但不得返回完成状态。

## 6. 模板和章节契约

模板是章节 ID、标题、层级、顺序、表号、表名、动态槽位和 fallback 的唯一来源。
模板支持以下内容块：

- `static_text`；
- `standards_reference`；
- `conditional_text`；
- `fact_value`；
- `deterministic_table`；
- `llm_section`；
- `synthesis_section`。

模板不允许 Jinja、Python 表达式或任意执行逻辑。复杂事实转换由具名的确定性
Builder 完成。

章节契约只负责：章节目的、Agent 可见事实路径、研究目标、输出要求、禁止内容和
摘要字段。缺少优先事实不阻断生成，不使用 `required_facts -> ask_user` 状态。

普通 Writing 任务只能看到本章契约、本章事实切片和本章研究结果。Synthesis
任务只能看到最小报告元数据、指定章节的 `section_summary` 和自身契约，不读取
全部章节全文。

Tool 为综合任务引用的确定性章节生成同结构的 `deterministic_summaries`。综合来源
必须由普通 Writing 摘要或确定性摘要覆盖，且 Synthesis 任务不得互相引用，以支持
同一轮并行综合。

## 7. Agent 结果

Agent 正文块首版只支持：

- `paragraph`；
- `bullet_list`；
- `numbered_list`。

Agent 不生成标题、目录、表号或确定性表格。每个普通章节同时返回结构化
`section_summary`，至少允许以下数组字段：

- `conclusions`；
- `key_facts`；
- `conditions`；
- `risks`；
- `recommendations`；
- `unresolved_items`。

`section_summary` 只能归纳本章正文、本章事实切片和本章 Evidence，不得引入正文
中没有出现的新事实。该约束由 Skill 和 Agent 执行，Tool 不增加语义审核器。

## 8. 异常和 fallback

以下用户可修正的输入问题返回 `failed`：

- 用户提供的逻辑路径不存在；
- 工程事实、工作包或工作结果内容无法解析；
- 输入 Schema、章节 ID 或工作结果契约非法。

HostClient 鉴权、连接、超时、读写服务异常，以及模板/规则损坏、生成产物 Schema
失败、导出器异常等未分类内部错误必须抛出，由 MCP 转为 `ToolError`，不得伪装成业务 `failed`。

以下情况不阻断：

- 工程事实字段缺失；
- Agent 未取得研究资料；
- 单个章节缺失；
- 单个章节正文块非法。

单章缺失或非法时，Tool 使用模板 fallback，并在返回 `diagnostics` 中记录，不把
执行细节写入报告正文。

Fallback 只能说明当前可研阶段的资料边界和后续需核实事项，不得自行写入“满足
规范要求”“依托现有罐区/管网/公用工程”或其他实质工程判断。

## 9. 正式与内部产物

正式交付只包括：

- `可行性研究报告_初稿.docx`；
- `可行性研究报告_初稿.md`；
- `report_manifest.json`。

`work_package.json`、`work_results.json` 和报告模型是内部产物，可保留在
宿主逻辑路径用于调试，不作为正式报告内容。不公开 reader Tool，也不公开 `run_id` 或 `finalize_id`。

## 10. 验收标准

- 新实现全部位于正式 `src` 包；
- 相同输入产生相同工作包和报告主体；
- 不调用 LLM、网络或历史资料搜索；
- 不出现 `needs_confirmation`、`needs_research`、`needs_llm`、
  `repair_required` 或 `generated_with_blocked_sections`；
- 缺少章节时仍能生成 DOCX 和 Markdown；
- 模板控制全部标题和顺序；
- 模板中的 `table_id` 和 caption 原样进入 DOCX/Markdown，导出器不得重新编号；
- DOCX/Markdown 均完整支持 `paragraph/bullet_list/numbered_list`，不得静默丢块；
- 普通章节事实严格隔离；
- 综合任务只接收章节摘要；
- 单元测试和 Python 编译检查通过；
- 使用真实 `engineering_facts.json` 完成一次三 Tool 链路验证；真实 Host 的 `_meta` 可见性、不截断和 E2E 是发布门禁。

## 11. 方案比较与实施进度表增强（2026-09-07）

### 11.1 目标与边界

本次增强解决三个问题：候选方案比选表的评价维度被固定、关键设备候选路线缺少
独立比较表、18.2 只有叙述而没有结构化实施进度表。仍保持
`engineering_facts.json -> report_prepare -> Agent -> work_results.json -> report_finalize` 主流程，
报告 Tool 不直接读取 `scheme.json`，也不接管上游算法选择。

由于当前工程事实整理会把 `scheme.json` 的评价维度固定映射为三个字段，本次先做
“前置工程事实契约增强”：允许在 `src/engineering_facts` 内仅扩展候选方案
评价的归一化逻辑，不改变十一项顶层事实根、Tool 外部输入输出或其他算法结果。报告
Tool 只消费 `engineering_facts.scheme_analysis.*.evaluation` 中已经归一化的动态维度，
不读取 `scheme.json`，也不自行修复、补造或推断评价维度。

### 11.2 动态方案评价维度

工程事实整理应遍历每个候选方案 `evaluation` 中实际存在的维度，按原始出现顺序
保留全部维度，而不是只提取技术可行性、实施复杂度和运行风险。维度键统一归一化为
snake_case，维度内容保留 `level`、`comment`、`key_basis`、`key_metrics` 和
`pending_items` 等已有信息；未知内容字段按通用 camelCase -> snake_case 方式保留。
若两个原始维度键或内容键归一化后得到同一个键，工程事实 Tool 应返回 fatal
`SCHEME_EVALUATION_KEY_COLLISION`，不得静默覆盖、合并或追加不透明后缀。中文维度键
保留原文字面值；空键或仅含符号的键视为非法。原始出现顺序必须稳定保留。

报告 Tool 构造候选方案比选表时：

- 固定基础列为“方案、来源、改造类型、方案概要”；
- 动态评价列取算法候选和用户自定义候选 `evaluation` 键的有序并集；
- 列顺序采用候选池中的首次出现顺序；
- 单元格优先显示该维度的 `level`，无 `level` 时依次使用 `value`、`comment`，仍无值时
  显示“待补充”；
- 列名优先使用维度自身的 `label`、`display_name` 或 `name`；当前已知键使用正式中文
  名称，未知键使用稳定的人类可读名称，不因缺少预置映射而丢弃整列；
- 评价维度为空时仍输出四个基础列，不虚构评价结果。

### 11.3 关键设备方案比较表

在 4.1.3.2 中，候选工艺技术方案比选表之后新增确定性“表4.2 关键设备方案比较表”，
列名固定为“设备名称、候选路线、推荐结论”。

表格只读取工程事实中的结构化设备候选集，不从描述关键词推断：

- 优先按设备组列出存在两个及以上候选路线的组合方案；
- 当前反应器专业使用 `equipment.reactor.combined_schemes`，并以
  `equipment.reactor.selected_scheme.scheme_id` 判断“选定”或“备选”；若没有可用
  `scheme_id`，再使用一基序号 `scheme_index` 唯一匹配；
- 设备名称使用候选集代表的专业对象，如“反应器组”；
- 候选路线使用组合方案的 `conclusion`，必要时再使用稳定的方案名称或动作摘要；
- 只有一个最终配置、没有多个候选路线的设备专业不强行进入比较表；
- `combined_schemes` 中候选 ID 重复、选中方案不能唯一匹配或选中方案不在候选集时，
  整张表不生成并记录 warning，不得把全部路线默认成“备选”；
- 缺少结构化候选路线时整张表不生成，正文说明以已取得的设备专业结果为边界。

“推荐结论”是用户暂定列名，本表中的值仅表示设备专业候选集内的“选定/备选”，不
表示 `scheme_analysis.recommendation`，也不替代 4.1.3.3 的算法推荐和用户采用结论。

### 11.4 18.2 实施进度计划表

18.2 改为受控结构化表格“表18.1 项目实施进度计划表”，固定列为“阶段、主要内容、
预计时长、前置条件”。阶段及顺序由 Tool 固定：

1. 项目前期（各报告编制及审批）
2. 基础设计
3. 施工图设计
4. 设备采购
5. 土建施工
6. 安装工程
7. 试生产

Agent 根据 `adopted_scheme`、改造后流程和已选设备专业结果生成每个阶段的“主要内容”
与“前置条件”，并估算“X～Y个月”的时长区间。该区间属于可研阶段估算，不得写成
承诺工期；设备采购、土建和设计可存在合理交叉，阶段时长不得直接相加为确定总工期。

为避免开放任意 Agent 表格权限，`work_results` 只为 18.2 增加专用字段，并与本章
`blocks`、`section_summary` 并存：

```json
{
  "section_id": "18.2",
  "blocks": [{"type": "paragraph", "text": "进度估算口径说明。"}],
  "implementation_schedule": {
    "rows": [
      {
        "stage": "项目前期（各报告编制及审批）",
        "main_content": "结合本项目编制并审批相关报告。",
        "duration_range": "2～3个月",
        "prerequisites": "改造范围和基础资料明确"
      }
    ]
  },
  "section_summary": {}
}
```

`report_work_results.schema.json` 声明上述对象形状和七行数量，并通过条件约束禁止
非 18.2 章节携带 `implementation_schedule`。阶段枚举、顺序、唯一性、区间文本和
时间单位由 Tool 在章节级校验中复核。Tool 按以下规则装配：

- 七个阶段必须完整、唯一并按固定顺序返回；
- 时长必须为非空区间文本且带时间单位；
- Agent 不能修改表号、表名、列名或增加阶段；
- `blocks + implementation_schedule + section_summary` 视为 18.2 的完整结果；任一部分
  缺失或非法时整章使用 fallback，记录 `SECTION_FALLBACK`，不保留合法段落，也不输出
  半成品进度表；
- 18.2 的 `section_summary` 仍供综合章节使用，但不得把估算区间表述为批准计划。

### 11.5 验证标准

- 使用包含新增评价维度的 fixture，证明工程事实不会丢维度；
- 使用不同候选维度组合，证明表头来自有序并集且缺失值稳定显示；
- 当前工程事实生成“反应器组”两条候选路线，并正确区分“选定/备选”；
- 18.2 只接受七个固定阶段，拒绝缺失、重复、乱序、额外阶段和非法时长；
- 非 18.2 章节携带 `implementation_schedule` 时，Schema 校验和 Tool 运行时校验均拒绝；
- 18.2 进度字段非法时 `fallback_section_count` 增加、diagnostics 出现
  `SECTION_FALLBACK`，Markdown/DOCX 不输出半成品进度表；
- Markdown 与 DOCX 中均出现表4.2和表18.1，列名、行数、内容一致；
- 完整 unittest、compileall、三份 JSON Schema 校验通过；
- 最终 DOCX 重新渲染并逐页检查，无表格裁切、乱码或异常分页。
