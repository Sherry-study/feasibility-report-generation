---
# 算法输出字段语义权威词典（authority）

> **本文件是算法输出字段语义的唯一权威。** 提取、解读、映射任何算法输出字段时，以本文件为准。
> 结构化字段路径词典（供 mapping_coverage 检测与机器消费）见同目录 field_mapping.yaml；两者语义必须保持一致，冲突时以本文件为准。

---

# 算法输出字段与工程事实映射表

> 依据：`反应器/plant_reactor_result_annotated.jsonc`、`塔/example_result_all_v7 annotated.jsonc`（含 `装置级/example_result_all_v3(1).json` 差异字段）、`装置级/` 目录（plant_info.json、plant_level_result_v3.json、retrofit_equipment.json、retrofit_topology(1).json、scheme.json、new_device_params.json）。
> 说明：`[]` 表示数组元素；"空/null" 语义已在取值说明中标注。

---

## 一、反应器算法输出（plant_reactor_result）

### 1.1 顶层字段

| 字段 | 工程事实含义 |
|---|---|
| `reactors[]` | 逐反应器能力评估与改造结果列表 |
| `retrofit_required` | 装置是否需要改造（布尔） |
| `combined_schemes[]` | 组合改造方案列表（按新增体积升序取前2个；算法候选方案） |
| `selected_combined_scheme` | 用户最终选定的组合改造方案（对象，字段结构同 combined_schemes[] 元素，另含 `scheme_id`） |
| `conclusion` | 装置级汇总结论（文字；描述的是**算法推荐方案**，不是用户选定方案） |

### 1.2 反应器标识与属性 reactors[]

| 字段 | 工程事实含义 |
|---|---|
| `id` | 反应器唯一编号 |
| `tag` | 反应器位号（如 R5103；空=新反应器无位号） |
| `name` | 反应器名称 |
| `type` | 设备类型（reactor=反应器） |
| `form` | 反应器形式（固定床 / 列管式） |
| `is_new_reactor` | 是否为新增反应器 |
| `has_heat_transfer` | 是否有换热 |
| `utility_medium` | 公用工程介质（HO=热油，CW=冷却水；空=无换热） |
| `flow_pattern` | 换热流动方式（countercurrent=逆流；空=无换热） |
| `operating_conditions.design` | 设计工况操作条件（temperature/pressure/volumetric_flow 各含 value+unit） |
| `operating_conditions.retrofit` | 改造工况操作条件（同上结构） |
| `operating_conditions.run` | 运行工况操作条件（同上结构） |

### 1.3 能力评估 reactors[].evaluation

| 字段 | 工程事实含义 |
|---|---|
| `evaluation.space_velocity.available` | 空速指标是否可用 |
| `evaluation.space_velocity.status` | 空速评估状态（pass=通过，fail=超标） |
| `evaluation.space_velocity.exceeded` | 新工况空速是否超出参考空速 |
| `evaluation.space_velocity.reference_LHSV_h1` | 参考液时空速（h⁻¹） |
| `evaluation.space_velocity.new_LHSV_h1` | 新工况液时空速（h⁻¹） |
| `evaluation.space_velocity.LHSV_ratio` | 新工况/参考空速比值 |
| `evaluation.space_velocity.conclusion` | 空速评估结论（文字） |
| `evaluation.heat_load.available` | 热负荷指标是否可用 |
| `evaluation.heat_load.status` | 热负荷评估状态（pass/fail） |
| `evaluation.heat_load.exceeded` | 热负荷是否超出换热能力 |
| `evaluation.heat_load.has_heat_transfer` | 是否有换热 |
| `evaluation.heat_load.new_heat_load_abs_W` | 新工况热负荷绝对值（W） |
| `evaluation.heat_load.Q_max_W` | 设备最大换热能力（W） |
| `evaluation.heat_load.heat_load_ratio` | 热负荷/换热能力比值 |
| `evaluation.heat_load.heat_capacity_sufficient` | 换热能力是否充足 |
| `evaluation.heat_load.conclusion` | 热负荷评估结论（文字） |
| `evaluation.adiabatic_temperature.available` | 绝热温度指标是否可用 |
| `evaluation.adiabatic_temperature.status` | 绝热温度评估状态（pass/fail） |
| `evaluation.adiabatic_temperature.exceeded` | 是否超温 |
| `evaluation.adiabatic_temperature.bottleneck_type` | 温度瓶颈类型（none_identified=无明显瓶颈） |
| `evaluation.adiabatic_temperature.known_target_outlet_temperature_c` | 已知目标出口温度（℃） |
| `evaluation.adiabatic_temperature.equipment_process_design_temperature_c` | 设备工艺设计温度（℃） |
| `evaluation.adiabatic_temperature.equipment_temperature_sufficient` | 设计温度是否满足新工况 |
| `evaluation.adiabatic_temperature.process_temperature_limits_c.min/max` | 工艺温度下限/上限（℃；null=未提供） |
| `evaluation.adiabatic_temperature.process_temperature_sufficient` | 工艺温度是否满足（null=无法判断） |
| `evaluation.adiabatic_temperature.adiabatic_temperature_change_C` | 绝热温度变化（℃） |
| `evaluation.adiabatic_temperature.adiabatic_temperature_rise_C` | 绝热温升（℃） |
| `evaluation.adiabatic_temperature.adiabatic_temperature_drop_C` | 绝热温降（℃） |
| `evaluation.adiabatic_temperature.adiabatic_metric_used_as_outlet_temperature` | 绝热指标是否被用作出口温度 |
| `evaluation.adiabatic_temperature.conclusion` | 绝热温度评估结论（文字） |

### 1.4 评估结论与改造需求

| 字段 | 工程事实含义 |
|---|---|
| `evaluation_status` | 该反应器评估总状态（pass=通过，fail=不通过） |
| `retrofit_required` | 该反应器是否需要改造 |
| `volumetric_flow_m3_h` | 新增反应器入口体积流量（m³/h） |
| `target_LHSV_h1` | 新增反应器目标液时空速（h⁻¹） |
| `required_volume_m3` | 新增反应器所需有效体积（m³） |
| `conclusion` | 该反应器评估汇总结论（文字） |
| `validation` | 改造工况出口组成验证是否通过（新算出口组成与装置级参考出口组成对比，组分差异>1%的数量为0时为 true） |

### 1.5 改造方案 reactors[].retrofit

| 字段 | 工程事实含义 |
|---|---|
| `retrofit.required_effective_volume_m3` | 满足新工况所需反应器有效体积（m³） |
| `retrofit.current_effective_volume_per_unit_m3` | 单台当前有效体积（m³） |
| `retrofit.current_operating_quantity` | 当前运行台数（台） |
| `retrofit.current_operating_total_effective_volume_m3` | 当前总有效体积（m³） |
| `retrofit.volume_increase_m3` | 需增加的有效体积（m³） |
| `retrofit.schemes[]` | 单设备改造方案列表 |

### 1.6 单台改造方案参数 retrofit.schemes[]

| 字段 | 工程事实含义 |
|---|---|
| `scheme_type` | 方案类型（add_parallel=并联，increase_volume=扩容更换大规格，reuse=利旧旧设备，new_reactor=新增设备） |
| `scheme_index` | 方案序号 |
| `description` | 方案描述（文字） |
| `reused_from` | 利旧来源反应器ID |
| `total_volume` | 单台设备总容积（m³；null=沿用原规格或新增未定规格） |
| `diameter` | 设备直径（mm） |
| `cylinder_height` | 筒体高度（mm） |
| `catalyst_volume` | 催化剂装填（有效）体积（m³） |
| `heat_exchange_area` | 换热面积（m²；null=无换热） |
| `tube_count` | 列管数（根；null=无换热） |
| `quantity` | 设备数量（台） |
| `total_effective_volume_m3` | 方案总有效体积（m³） |
| `total_weight` | 设备总重量（kg；null=未定规格） |
| `operating_conditions.inlet_temperature_c` | 入口温度（℃） |
| `operating_conditions.inlet_pressure_mpag` | 入口压力（MPaG） |
| `operating_conditions.mass_flow_kg_h` | 入口质量流量（kg/h） |
| `operating_conditions.utility.medium` | 公用工程介质 |
| `operating_conditions.utility.inlet_temperature_c` | 公用工程入口温度（℃） |
| `operating_conditions.utility.outlet_temperature_c` | 公用工程出口温度（℃） |
| `operating_conditions.utility.mass_flow_kg_h` | 公用工程质量流量（kg/h） |

### 1.7 组合方案 combined_schemes[]（算法候选）

| 字段 | 工程事实含义 |
|---|---|
| `total_new_equipment_volume_m3` | 组合方案新增有效体积合计（m³） |
| `reuse[]` | 利旧方案设备列表（id/name/scheme_type/reused_from） |
| `addition[]` | 新增/扩容方案设备列表 |
| `parallel[]` | 并联方案设备列表 |
| `heat_transfer[]` | 换热改造设备列表（空=无） |
| `no_retrofit[]` | 无需改造设备列表（空=无） |
| `conclusion` | 组合方案结论（文字） |
| `scheme_index` | 组合方案序号 |
| `scheme_id` | 组合方案唯一标识（combined_N） |

### 1.8 用户选定方案 selected_combined_scheme

用户最终选定的组合改造方案（对象），字段结构同 combined_schemes[] 元素（total_new_equipment_volume_m3/reuse[]/addition[]/parallel[]/heat_transfer[]/no_retrofit[]/conclusion/scheme_index），另含 `scheme_id`（如 combined_1），用于与 combined_schemes[] 候选对齐。**报告正文采用的方案以本字段为准**；缺失时回退使用 combined_schemes[] + conclusion 的算法推荐方案，并交由用户确认（见使用注意事项 9）。

---

## 二、塔器算法输出（example_result_all_v7，含 v3 差异字段）

### 2.1 顶层字段

| 字段 | 工程事实含义 |
|---|---|
| `separator[]` | 逐塔能力核算与改造结果列表 |
| `retrofit_required` | 装置是否需要改造（布尔） |
| `combined_schemes.optimal/parallel/series/other` | 组合方案：操作优化/并联/串联/其他（改造失败或萃取塔）的塔列表（v3 中 parallel、series 为 [原塔, 新塔] 成对数组） |
| `conclusion` | 装置级塔器汇总结论（文字） |

### 2.2 塔标识与核算结果 separator[]

| 字段 | 工程事实含义 |
|---|---|
| `id` | 塔唯一编号 |
| `name` | 塔名称 |
| `type` | 塔类型（separator=精馏塔，extractor=萃取（水洗）塔，reactor_separator=反应精馏塔/催化蒸馏塔） |
| `detail.success` | 塔核算是否成功 |
| `detail.optimal.require` | 分离要求校核结果（布尔） |
| `detail.optimal.hydraulic` | 水力学校核结果（布尔） |

萃取塔（extractor）专用字段：

| 字段 | 工程事实含义 |
|---|---|
| `detail.state` | 超限方向（upper=超上限） |
| `detail.current_value` | 当前水力学指标值 |
| `detail.upper_limit` / `detail.lower_limit` | 水力学指标上限/下限 |
| `detail.hydro_limit.{stream_id}` | 对应流股的水力学极限流量（kg/h） |

### 2.3 最优操作参数 detail.optimal

| 字段 | 工程事实含义 |
|---|---|
| `N_trays` | 塔板数（块） |
| `feed_tray` | 进料板位置（第几块板） |
| `reflux_ratio` | 回流比 |
| `P_top` | 塔顶压力 |
| `P_bottom` | 塔釜压力（MPa） |
| `QB_kW` | 塔釜再沸器热负荷（kW） |
| `T_top_C` | 塔顶温度（℃） |
| `impurity_info[]` | 杂质信息列表 |

v3 补充字段（plan_detail.optimal_ori 内）：

| 字段 | 工程事实含义 |
|---|---|
| `top_feed_direction` / `bottom_feed_direction` | 上/下进料方向标志 |
| `require[].position` / `_site` | 分离要求位置（塔顶/塔釜/侧线） |
| `require[].tray_id` | 侧线要求对应塔板号（null=非侧线） |
| `require[].index` | 指标类型（质量分数/回收率） |
| `require[].target` | 目标值 |
| `require[].requirement` | 要求方向（高于/低于） |
| `require[].stream` | 要求对应流股 |
| `require[].current_value` | 当前实际值 |
| `require[].reforce` / `_pass` | 是否强制要求 / 是否达标 |
| `output.top.temperature` / `output.top.pressure` | 塔顶温度（℃）/压力（v3） |

### 2.4 塔产品流股 detail.output

| 字段 | 工程事实含义 |
|---|---|
| `output.top.mass_flow` | 塔顶产品质量流量（kg/h） |
| `output.top.mass_fraction.{组分}` | 塔顶产品中各组分质量分数 |
| `output.bottom.mass_flow` | 塔釜产品质量流量（kg/h） |
| `output.bottom.mass_fraction.{组分}` | 塔釜产品中各组分质量分数 |
| `output.middle[].mass_flow` / `mass_fraction` | 侧线产品质量流量（kg/h）及组成 |

### 2.5 关键组分分离指标 detail.separator_limit

| 字段 | 工程事实含义 |
|---|---|
| `separator_limit.top.{组分}.recovery_rate` | 该组分塔顶回收率 |
| `separator_limit.top.{组分}.mass_fraction` | 该组分塔顶质量分数 |
| `separator_limit.bottom.{组分}.recovery_rate` | 该组分塔釜回收率 |
| `separator_limit.bottom.{组分}.mass_fraction` | 该组分塔釜质量分数 |
| `separator_limit._best_score` | 最优分离综合得分 |

### 2.6 改造方案 retrofit

| 字段 | 工程事实含义 |
|---|---|
| `retrofit.route` | 改造方式（optimal=操作优化，side=侧线抽出，structure=内构件优化，parallel=并联，series=串联） |
| `retrofit.success` | 改造方案求解是否成功 |
| `retrofit.plan_detail.optimal_ori` | 原塔（改造前）操作参数（字段同 2.3，另含 `feed` 进料量 kg/h） |
| `retrofit.plan_detail.optimal_retrofit` | 新塔（改造后）操作参数（字段同上；`feed_ratio`=并联时新塔分得的进料比例；空对象=操作优化无需改参数） |

### 2.7 塔体设备参数 retrofit.plan_detail.device_paras

| 字段 | 工程事实含义 |
|---|---|
| `struct_info[]` | 塔体分段结构，成对为 [塔径 mm, 板间距 mm（板式塔）] 或 [塔径 mm, 填料高度 mm（填料塔）] |
| `tray_num` | 塔板数（块） |
| `feed_tray` | 进料板（嵌套数组） |
| `tray_type` | 塔内件类型（F1=F1浮阀塔板，seieve=筛板塔板，BX 等型号=规整填料） |
| `P_design` | 设计压力（MPa） |
| `plate_paras[].lw` | 堰长（m） |
| `plate_paras[].hw` | 堰高（m） |
| `plate_paras[].h0` | 降液管底隙高度（m） |
| `plate_paras[].Wd` | 弓形降液管宽度（m） |
| `plate_paras[].n` | 浮阀/开孔数量（个） |
| `plate_paras[].An` | 单孔面积（m²） |
| `plate_paras[].Af_AT` | 降液管面积/塔截面积比 |
| `plate_paras[].fluent_shape` | 液流型式（单流型/双流型；空对象=填料段无塔板参数） |

v7 头注补充：侧线方案 `side_draws` 字段（tray=侧线采出塔板号，component=目标组分名称，site=采出位置 top/bottom/side，value=组分浓度，requirement=原始分离要求）。

---

## 三、装置级算法输出

### 3.1 plant_info.json（装置基础信息）

| 字段 | 工程事实含义 |
|---|---|
| `plant_info.plant_name` | 装置名称 |
| `plant_info.plant_type` | 装置类型 |
| `plant_info.annual_operating_hours` | 年运行时长（小时） |
| `plant_info.unit` | 产能单位（吨/年） |
| `plant_info.stream_mapping_notes` | 流股匹配说明（算法内部实现说明，不进正文） |
| `design_case` / `retrofit_case` | 设计工况 / 改造工况 |
| `{case}.case_name` | 工况名称（设计工况/改造工况） |
| `{case}.capacity_ratio` | 处理能力倍数（设计=1.0，改造=2.0 即扩产倍数） |
| `{case}.feeds[].name` | 进料名称（如粗异戊烯进料、甲醇进料） |
| `{case}.feeds[].stream_id` | 进料对应流股编号 |
| `{case}.feeds[].is_main_feed` | 是否主进料 |
| `{case}.feeds[].annual_capacity.value/unit` | 年进料量（吨/年） |
| `{case}.feeds[].hourly_rate.value/unit` | 小时进料量（kg/h） |
| `{case}.feeds[].flow_rate.value/unit` | 流股质量流量（kg/h） |
| `{case}.products[].name` | 产品名称 |
| `{case}.products[].is_main_product` | 是否主产品 |
| `{case}.products[].stream_id` | 产品流股编号 |
| `{case}.products[].annual_capacity.value/unit` | 产品年产能（吨/年） |
| `{case}.products[].hourly_rate.value/unit` | 产品小时产量（kg/h） |

### 3.2 plant_info.json 流股通用字段（stream 对象，plant_level_result_v3.streams 同构）

| 字段 | 工程事实含义 |
|---|---|
| `id` / `name` | 流股编号 / 流股名称 |
| `source` / `target` | 流股起点/终点设备编号（空字符串=装置边界外，即原料入界或产品/排出物出界） |
| `flow_rate.value/unit` | 流股质量流量（kg/h） |
| `composition[].component` | 组分代码（对应 components.formula） |
| `composition[].mole_fraction` | 组分摩尔分数 |
| `composition[].mass_fraction` | 组分质量分数 |
| `temperature.value/unit` | 流股温度（℃） |
| `pressure.value/unit` | 流股压力（MPa） |
| `phase` | 相态（Liq=液相，Vap=汽相） |
| `target_product_stream` | 是否目标产品流股 |
| `is_top` / `is_middle` / `is_bottom` | 是否塔顶/侧线/塔釜出料 |
| `position` | 图面位置坐标（算法内部，不进正文） |

### 3.3 plant_info.json 改造目标对齐 retrofit_case.target_alignment

| 字段 | 工程事实含义 |
|---|---|
| `mode` | 对齐模式（product_target=按目标产品产能定标） |
| `target_product_annual_capacity` / `target_product_annual_unit` | 目标产品年产能（吨/年） |
| `target_product_flow` / `target_product_flow_unit` | 目标产品流量（kg/h） |
| `yield_used` / `yield_applied` | 采用的收率 / 收率是否已应用 |
| `operating_hours` | 年运行小时数（h） |
| `design_main_feed_flow` | 设计主进料流量（kg/h） |
| `design_main_product_flow` | 设计主产品流量（kg/h） |
| `design_main_product_annual_capacity` | 设计主产品年产能（吨/年） |
| `target_main_feed_flow` | 目标（改造后）主进料流量（kg/h） |
| `product_scale_factor` / `annual_scale_factor` / `scale_factor` | 扩产倍数 |
| `total_reactant_flow` / `total_feed_flow` | 总反应物/总进料流量（kg/h） |
| `retrofit_feed_matched_stream_ids` / `unmatched_stream_ids` | 已匹配/未匹配的改造进料流股编号 |
| `main_feed_stream_id` / `main_product_stream_id` | 主进料/主产品流股编号 |

### 3.4 plant_level_result_v3.json（装置级衡算与配置，改造后物料平衡表=retrofit_case.streams）

| 字段 | 工程事实含义 |
|---|---|
| `design_case` / `retrofit_case` | 设计工况 / 改造工况计算结果 |
| `{case}.separator_config[]` | 塔器配置列表 |
| `{case}.separator_config[].condenser` / `reboiler` | 是否设冷凝器 / 再沸器 |
| `{case}.separator_config[].inlet[]` | 塔进料流股编号 |
| `{case}.separator_config[].outlet.top/bottom` | 塔顶/塔釜出料流股编号 |
| `{case}.separator_config[].require[]` | 分离要求（同 2.3 v3 require 字段语义） |
| `{case}.separator_config[].operating_conditions.temperature.top/bottom.value` | 塔顶/塔釜温度（℃） |
| `{case}.separator_config[].operating_conditions.pressure.top/bottom.value` | 塔顶/塔釜压力（MPa） |
| `{case}.separator_config[].operating_conditions.reflux.value/unit` | 回流量（kg/h） |
| `{case}.streams[]` | 全装置流股（物料平衡表；字段同 3.2） |
| `{case}.topology.nodes[]` | 设备节点（id/name/type/form/condenser/reboiler/isNew/retrofit_role/original_tower） |
| `{case}.topology.edges[]` | 流股连接（id/name/source/target/modified/originalTarget/isNew） |
| `{case}.reactor_config[]` | 反应器配置列表 |
| `components[]` | 组分字典（name 组分中文名，formula 组分代码，molecular_weight 分子量 g/mol，unit） |

### 3.5 plant_level_result_v3.json 反应器配置 reactor_config[].reactions[]

| 字段 | 工程事实含义 |
|---|---|
| `id` | 反应编号（reaction1…） |
| `equation` | 反应方程式 |
| `reactants[].component` / `stoichiometric_coefficient` | 反应物组分 / 化学计量系数 |
| `products[].component` / `stoichiometric_coefficient` | 产物组分 / 化学计量系数 |
| `catalyst` | 催化剂 |
| `conditions.temperature` / `pressure` | 反应条件（温度/压力，空=未提供） |
| `type` | 反应类型（etherification=醚化；空=未分类） |
| `main_reaction` | 是否主反应 |
| `design_condition.conversion` | 设计转化率 |
| `design_condition.limiting_reactant` | 设计限制性反应物（组分代码） |
| `run_condition.conversion` | 实际运行转化率（null=无数据） |
| `run_condition.limiting_reactant` | 运行限制性反应物 |

### 3.6 retrofit_equipment.json（设备台账）

| 字段 | 工程事实含义 |
|---|---|
| `equipment[].id` / `name` | 设备编号 / 设备名称 |
| `equipment[].type` | 设备类别（reactor=反应器，separator=精馏塔，extractor=萃取塔，reactor_separator=反应精馏塔，preprocess=预处理单元，tee=分离器，mixer=混合器） |
| `equipment[].form` | 设备形式（fixed_bed=固定床，tubular=列管式，tray=板式塔，packed=填料塔） |
| `equipment[].condenser` / `reboiler` | 是否设冷凝器 / 再沸器 |
| `equipment[].specifications` | 规格说明键值对（类型=进出料结构型式，功能，反应类型；特点=进出料衡算关系） |
| `equipment[].is_new` | 是否新增设备 |

### 3.7 retrofit_topology(1).json（改造后拓扑）

| 字段 | 工程事实含义 |
|---|---|
| `nodes[].id` / `name` / `type` | 设备节点编号/名称/类别 |
| `nodes[].isNew` | 是否新增设备 |
| `nodes[].retrofit_role` | 改造角色（series=作为串联新塔） |
| `nodes[].original_tower` | 新塔对应的原塔编号 |
| `edges[].id` / `name` / `source` / `target` | 流股编号/名称/起点/终点 |
| `edges[].modified` / `originalTarget` | 流股是否改接 / 原去向设备编号 |
| `edges[].isNew` | 是否新增流股 |
| `edges[].description` | 新增流股说明 |
| `edges[].temperature/pressure/phase` | 流股温度（℃）/压力（MPa）/相态 |
| `schemeInfo[]` | 改造方案信息列表 |
| `schemeInfo[].name` / `description` | 方案名称 / 方案描述（含位置、单元作用、机理） |
| `schemeInfo[].rank` | 方案排序 |
| `schemeInfo[].newEquipment` | 新增设备（id/name/type/position 安装位置描述） |
| `schemeInfo[].modifiedEquipment` | 改造设备 |
| `schemeInfo[].modifiedEdges` / `newEdges` | 改接流股 / 新增流股编号列表 |
| `schemeInfo[].retrofitType(Label)` | 改造类型（series=串联改造） |
| `schemeInfo[].newEquipment_list[].newEquipment` / `type` | 新增设备名称 / 类别 |
| `schemeInfo[].newEquipment_list[].upEquipment(_id)` / `downEquipment(_id)` | 新增设备的上游 / 下游设备 |
| `schemeInfo[].newStream_list[].stream` / `from` / `to` | 新增流股名称 / 来源 / 去向 |
| `schemeInfo[].newStream_list[].sourceOutletPosition` | 流股引出位置（top/bottom/unknown） |
| `schemeInfo[].newStream_list[].targetInletPosition` | 流股接入位置（feed） |
| `schemeInfo[].newStream_list[].role(Label)` | 新增流股工程角色（如补充甲醇、预反应后进料、塔底TAME循环） |
| `schemeInfo[].newStream_list[].basis` | 新增流股依据 |
| `schemeInfo[].modifiedStream_list[].changeType` | 流股变更类型（connection=改接） |
| `schemeInfo[].modifiedStream_list[].oldfrom/oldto/newfrom/newto(_id)` | 流股改接前/后的起点终点 |
| `schemeInfo[].removedStream_list` / `removedEquipment_list` | 取消流股 / 移除设备 |
| `schemeInfo[].idMapping` | 新旧设备编号映射 |
| `schemeInfo[].sourceType` | 方案来源（separator_feedback=塔器算法串联反馈） |

### 3.8 scheme.json（诊断驱动拓扑方案，topology_retrofit_v1）

| 字段 | 工程事实含义 |
|---|---|
| `schemaVersion` / `schemeKind` | 方案文件版本 / 方案来源类型（diagnosis=诊断） |
| `issues[].issueId` | 诊断问题编号 |
| `issues[].diagnosis.constraintComponent` | 约束组分 |
| `issues[].diagnosis.associatedComponent` | 关联组分 |
| `issues[].diagnosis.boilingPointDiff` | 关键组分沸点差（大/小） |
| `issues[].diagnosis.bottleneckEquipment` | 瓶颈设备名称 |
| `issues[].diagnosis.currentTopologyPattern(Label)` | 当前流程拓扑模式（如单塔反应-分离耦合） |
| `issues[].diagnosis.bottleneckType(Label)` | 瓶颈类型（equilibrium_or_conversion_limit=反应平衡/转化率限制） |
| `issues[].diagnosis.recommendedRetrofitPattern(Label)` | 推荐改造模式（如强化反应-分离耦合） |
| `issues[].diagnosis.basis` | 诊断依据 |
| `issues[].diagnosis.assumptions` | 诊断假设 |
| `issues[].diagnosis.risks` | 改造风险 |
| `candidates[].name` | 候选方案名称（方案A/B/C/D） |
| `candidates[].brief` | 方案概要 |
| `candidates[].retrofitType(Label)` | 改造类型（流程微调/设备改造/新增设备） |
| `candidates[].description` | 方案描述 |
| `candidates[].expectedEffect` | 预期效果（转化率、纯度、收率改善预期） |
| `candidates[].outletDisposition[]` | 出口去向调整说明 |
| `candidates[].resultingTopologyPattern(Label)` | 改造后拓扑模式 |
| `candidates[].schemeDetails` | 方案明细（新增设备/新增流股/改接流股汇总文字） |
| `candidates[].sourceType` | 方案来源（diagnosis） |
| `candidates[].evaluation.technicalFeasibility.level/comment/keyBasis/keyMetrics/pendingItems` | 技术可行性：等级（高/中/低）/评语/关键依据/关键指标/待落实项 |
| `candidates[].evaluation.implementationComplexity.…` | 实施复杂度：同上结构 |
| `candidates[].evaluation.operationalRisk.…` | 运行风险：同上结构 |
| `candidates[].score` | 方案综合评分 |
| `recommendation.combination[]` | 推荐方案组合 |
| `recommendation.reason` | 推荐理由 |

（candidates 的 newEquipment_list/newStream_list/modifiedStream_list 字段语义同 3.7 schemeInfo 对应字段。）

### 3.9 new_device_params.json（新增设备工艺参数）

| 字段 | 工程事实含义 |
|---|---|
| `reactors.{设备ID}[]` | 新增反应器的反应参数列表 |
| `reactors.{id}[].equation` / `reactants` / `products` / `catalyst` / `conditions` / `type` / `main_reaction` | 同 3.5 反应字段语义 |
| `reactors.{id}[].conversion` | 该反应转化率 |
| `reactors.{id}[].limiting_reactant` | 限制性反应物（组分代码） |
| `separators.{设备ID}.{流股ID}.{组分}` | 新增塔各流股中关键组分的回收率目标值 |

### 3.10 报告类文件

| 文件 | 内容 |
|---|---|
| `plant_diagnosis_report.md` | 装置级工艺诊断报告（设计/运行总收率、各反应器设计/运行转化率对比、关键瓶颈设备与建议措施） |
| `scheme_report.md` | 拓扑改造方案报告（瓶颈诊断、候选方案A~D、出口去向与回收判断、拓扑变更、假设与风险、推荐组合方案） |

---

## 四、枚举值代码->工程含义速查

| 代码 | 工程含义 |
|---|---|
| `add_parallel` | 并联增设（反应器） |
| `increase_volume` | 扩容（更换更大规格反应器） |
| `reuse` | 利旧（复用旧设备） |
| `new_reactor` | 新增设备 |
| `optimal` | 操作优化（塔） |
| `side` | 侧线抽出（塔） |
| `structure` | 内构件优化（塔） |
| `series` | 串联（塔） |
| `F1` | F1 浮阀塔板 |
| `seieve` | 筛板塔板 |
| `BX` 等 | 规整填料型号 |
| `HO` | 热油（公用工程介质） |
| `CW` | 冷却水（公用工程介质） |
| `countercurrent` | 逆流换热 |
| `fixed_bed` / `tubular` | 固定床 / 列管式 |
| `tray` / `packed` | 板式塔 / 填料塔 |
| `preprocess` / `tee` / `mixer` | 预处理单元 / 分离器 / 混合器 |
| `Liq` / `Vap` | 液相 / 汽相 |
| `etherification` | 醚化反应 |
| `equilibrium_or_conversion_limit` | 反应平衡/转化率限制 |
| `diagnosis` / `separator_feedback` | 方案来源：装置诊断 / 塔器算法反馈 |
| `高于` / `低于` | 分离要求方向 |
| `质量分数` / `回收率` | 分离要求指标类型 |

---

## 五、使用注意事项

1. **P_top 单位不一致**：塔文件 `detail.optimal.P_top`=0.4（MPa）与 `plan_detail.optimal_ori.P_top`=4.0（疑似 bar）量级不同，引用前需核对单位口径。
2. **QB_kW**：注释为"塔釜再沸器流量"，实际语义为再沸器热负荷（kW）。
3. **null/空值语义**：反应器方案中 `total_volume/diameter/total_weight=null` 表示"沿用原规格"或"新增方案未定规格"；`heat_exchange_area/tube_count=null` 表示无换热。
4. **物料平衡**：`plant_level_result_v3.json` 的 `retrofit_case.streams` 即改造后物料平衡表；设计工况对应 `design_case.streams`。
5. **边界流股识别**：`source` 或 `target` 为空字符串的流股为装置边界流股（原料入界/产品及排出物出界）。
6. **组分对照**：流股与反应方程使用组分代码（formula，如 MOH、TAME、2M2B=），中文名以 `plant_level_result_v3.json > components` 字典为准（塔结果 mass_fraction 的键直接为中文名）。
7. **版本差异**：v7 与 v3 塔结果结构基本一致；v3 额外含 `require` 明细（target/requirement/current_value/_pass）、进料方向、出料温度压力字段，且包含串联新塔（id=18）核算结果；组合方案结构 v3 为成对数组、v7 为分组列表。
8. **报告类输出**（plant_diagnosis_report.md、scheme_report.md）为成文报告，其中数值（设计/运行总收率、转化率对比、瓶颈描述、方案评分）可直接作为工程事实引用。
9. **报告采用方案以用户选定为准**：反应器结果顶层 `conclusion` 描述的是**算法推荐方案**；报告正文/采用方案应以 `selected_combined_scheme`（及其 `scheme_id`）为准。`selected_combined_scheme` 缺失时可回退用 `combined_schemes[]` + `conclusion` 的推荐方案并交由用户确认。注意两者数值可能有细微差异（如算法推荐新增有效体积 52.89 m³ vs 用户选定 52.80 m³），引用时不得混用。
