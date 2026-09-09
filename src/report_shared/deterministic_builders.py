"""仅由工程事实确定性构建报告块的模块。

本模块包含若干“确定性块构建器”（builder）。每个构建器从工程事实中读取
数据，直接产出报告章节中用到的段落、列表、表格等块，不经过 LLM，
保证在相同输入下输出完全一致、可复现。
"""

from __future__ import annotations

from typing import Any

from .context_builder import get_path


# 模板中 deterministic_table 块可引用的构建器名称集合。
# 新增构建器时需同步在此登记。
BUILDER_NAMES = {
    "project_identity",    # 项目概况表
    "scale_table",         # 生产规模表
    "scheme_comparison",   # 方案对比表
    "key_equipment_comparison",  # 关键设备方案比较表
    "material_balance",    # 物料平衡表
    "consumption",         # 消耗量表
    "new_equipment",       # 新增设备表
    "reused_equipment",    # 利旧设备表
    "modified_equipment",  # 改造设备表
    "utilities",           # 公用工程表
    "energy",              # 能耗表
    "investment_scope",    # 投资范围表
    "investment_summary",  # 投资汇总表
}


def build_blocks(
    section: dict[str, Any],
    facts: dict[str, Any],
    diagnostics: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """根据模板章节声明的 blocks，构建确定性内容块列表。

    支持四种块类型：static_text（静态文本）、standards_reference（标准引用）、
    fact_value（取事实值）、conditional_text（条件文本）、deterministic_table（确定性表格）。
    """
    blocks: list[dict[str, Any]] = []
    for spec in section.get("blocks", []):
        block_type = spec.get("type")
        if block_type == "static_text":
            text = spec.get("text", "")
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif block_type == "standards_reference":
            # 该块应已被 template_loader 展开为 items 列表
            if "items" not in spec:
                raise ValueError("standards_reference block was not expanded by template loader")
            items = [str(item) for item in spec.get("items", []) if str(item).strip()]
            if items:
                blocks.append({"type": "numbered_list", "items": items})
        elif block_type == "fact_value":
            value = get_path(facts, spec.get("path", ""))
            # 事实值非空时才输出“标签 + 格式化值”
            if value not in (None, "", [], {}):
                blocks.append({"type": "paragraph", "text": f"{spec.get('label', '')}{_fmt(value)}"})
        elif block_type == "conditional_text":
            value = get_path(facts, spec.get("path", ""))
            # 事实值非空时才输出这段文本（条件成立）
            if value not in (None, "", [], {}):
                blocks.append({"type": "paragraph", "text": spec.get("text", "")})
        elif block_type == "deterministic_table":
            table = _build_table(spec, section, facts, diagnostics)
            if table:
                blocks.append(table)
    return blocks


def _build_table(
    spec: dict[str, Any],
    section: dict[str, Any],
    facts: dict[str, Any],
    diagnostics: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """根据 builder 名称调用对应构建器，产出 table 块。

    每个构建器返回一个二维列表，首行为表头、其余为数据行；无数据时返回 None。
    表号/表题优先取块级覆盖（spec.table_id/spec.caption），否则回退到章节级。
    """
    builder = spec.get("builder", "")
    builders = {
        "project_identity": _project_identity,
        "scale_table": _scale_table,
        "scheme_comparison": _scheme_comparison,
        "key_equipment_comparison": lambda data: _key_equipment_comparison(data, diagnostics),
        "material_balance": _material_balance,
        "consumption": _consumption,
        "new_equipment": lambda data: _equipment_by_bucket(data, "new"),
        "reused_equipment": lambda data: _equipment_by_bucket(data, "reuse"),
        "modified_equipment": lambda data: _equipment_by_bucket(data, "modified"),
        "utilities": _utilities,
        "energy": _energy,
        "investment_scope": _investment_scope,
        "investment_summary": _investment_summary,
    }
    func = builders.get(builder)
    if not func:
        raise ValueError(f"unknown deterministic table builder: {builder}")
    rows = func(facts)
    if not rows:
        return None
    # 首行作为表头，其余作为数据行
    return {
        "type": "table",
        "table_id": spec.get("table_id") or section.get("table_id"),
        "caption": spec.get("caption") or section.get("caption"),
        "headers": rows[0],
        "rows": rows[1:],
    }


def _project_identity(facts: dict[str, Any]) -> list[list[str]]:
    """项目概况表：建设单位、装置名称、项目性质。"""
    return [
        ["项目", "内容"],
        ["建设单位", _text(get_path(facts, "basic_info.construction_unit"))],
        ["装置名称", _text(get_path(facts, "unit.name"))],
        ["项目性质", "暂按现有装置技术改造项目识别，项目性质以建设单位确认资料为准"],
    ]


def _scale_table(facts: dict[str, Any]) -> list[list[str]]:
    """生产规模表：设计/改造工况下的主产品年规模与小时产量对比。"""
    design = _main_product(get_path(facts, "unit.design_case.products") or [])
    retrofit = _main_product(get_path(facts, "unit.retrofit_case.products") or [])
    return [
        ["项目", "单位", "设计/基准", "改造后"],
        [
            f"{_text(retrofit.get('name') or design.get('name') or '产品')}年生产规模",
            _unit(retrofit.get("annual_capacity") or design.get("annual_capacity")),
            _value(design.get("annual_capacity")),
            _value(retrofit.get("annual_capacity")),
        ],
        [
            f"{_text(retrofit.get('name') or design.get('name') or '产品')}小时产量",
            _unit(retrofit.get("hourly_rate") or design.get("hourly_rate")),
            _value(design.get("hourly_rate")),
            _value(retrofit.get("hourly_rate")),
        ],
    ]


def _scheme_comparison(facts: dict[str, Any]) -> list[list[str]]:
    """方案对比表：列出每个候选方案及其可行性评价维度。"""
    base_headers = ["方案", "来源", "改造类型", "方案概要"]
    candidates = list(get_path(facts, "scheme_analysis.candidates") or [])
    candidates.extend(get_path(facts, "scheme_analysis.user_candidates") or [])
    dimensions: list[str] = []
    labels: dict[str, str] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        evaluation = candidate.get("evaluation") or {}
        if not isinstance(evaluation, dict):
            continue
        for key, value in evaluation.items():
            if key not in dimensions:
                dimensions.append(key)
                labels[key] = _dimension_label(key, value)
    rows = [base_headers + [labels[key] for key in dimensions]]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        evaluation = candidate.get("evaluation", {})
        rows.append(
            [
                _text(candidate.get("name")),
                _candidate_source_label(candidate.get("source_pool")),
                _text(candidate.get("retrofit_type_label") or candidate.get("retrofit_type")),
                _text(candidate.get("brief")),
            ]
            + [_evaluation_cell(evaluation, key) for key in dimensions]
        )
    return rows


def _key_equipment_comparison(
    facts: dict[str, Any],
    diagnostics: list[dict[str, str]] | None = None,
) -> list[list[str]]:
    """表4.2：关键设备候选路线比较表。

    当前只消费反应器专业的组合候选集；异常时不默认全部备选。
    """
    combined = get_path(facts, "equipment.reactor.combined_schemes") or []
    selected = get_path(facts, "equipment.reactor.selected_scheme") or {}
    if not isinstance(combined, list) or len(combined) < 2:
        return []
    if not isinstance(selected, dict):
        _append_warning(diagnostics, "KEY_EQUIPMENT_COMPARISON_SKIPPED", "reactor selected_scheme is missing or invalid.")
        return []

    selected_index = _selected_combined_scheme_index(combined, selected, diagnostics)
    if selected_index is None:
        return []

    rows = [["设备名称", "候选路线", "推荐结论"]]
    for index, scheme in enumerate(combined):
        if not isinstance(scheme, dict):
            continue
        rows.append(
            [
                "反应器组",
                _text(scheme.get("conclusion") or _combined_action_summary(scheme) or scheme.get("scheme_id")),
                "选定" if index == selected_index else "备选",
            ]
        )
    return rows if len(rows) > 1 else []


def _material_balance(facts: dict[str, Any]) -> list[list[str]]:
    """物料平衡表：对比设计/改造工况下各流股的流量。

    取设计、改造两套流股，按流股 id 排序合并输出。
    """
    design = _streams_by_id(get_path(facts, "process.design.streams") or [])
    retrofit = _streams_by_id(get_path(facts, "process.retrofit.streams") or [])
    stream_ids = sorted(set(design) | set(retrofit), key=_stream_sort_key)
    rows = [["流股名称", "方向", "单位", "改造前", "改造后", "备注"]]
    for stream_id in stream_ids[:18]:
        before = design.get(stream_id, {})
        after = retrofit.get(stream_id, {})
        stream = after or before
        rows.append(
            [
                _text(stream.get("name") or stream.get("raw_name")),
                _direction(stream),
                _unit(stream.get("flow")),
                _value(before.get("flow")),
                _value(after.get("flow")),
                "产品" if stream.get("target_product_stream") else "",
            ]
        )
    return rows


def _consumption(facts: dict[str, Any]) -> list[list[str]]:
    """消耗量表：对比设计/改造工况下的年化物料消耗量。

    派生事实里按 stream_id/stream_name 分组，并区分 design/retrofit 两个工况。
    """
    rows = [["项目", "单位", "改造前", "改造后", "变化量/变化率", "说明"]]
    items = get_path(facts, "derived_facts.annualized_material_consumption") or []
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("stream_id") or item.get("stream_name") or "")
        if not key:
            continue
        # 以 condition 字段区分同一物料的设计/改造两个值
        grouped.setdefault(key, {})[str(item.get("condition"))] = item
    for pair in grouped.values():
        design = pair.get("design", {})
        retrofit = pair.get("retrofit", {})
        name = _text(retrofit.get("stream_name") or design.get("stream_name"))
        rows.append(
            [
                name,
                _unit(retrofit.get("annual_consumption") or design.get("annual_consumption")),
                _value(design.get("annual_consumption")),
                _value(retrofit.get("annual_consumption")),
                _change(design.get("annual_consumption"), retrofit.get("annual_consumption")),
                "",
            ]
        )
    return rows


def _equipment_by_bucket(facts: dict[str, Any], bucket: str) -> list[list[str]]:
    """按设备“处理动作”分桶（新增/利旧/改造）生成设备表。

    只消费工程事实中最终选定的设备专业结构化结果，不通过文本关键词猜测分类。
    无变化设备不进入 4.2.4 的三个正式动作表。
    """
    equipment = _reportable_equipment_actions(facts)
    if bucket == "modified":
        rows = [["序号", "设备类别", "设备位号", "设备名称", "主要改造内容", "改造后主要规格/设计参数", "数量", "备注"]]
    else:
        rows = [["序号", "设备类别", "设备位号", "设备名称", "主要规格/设计参数", "数量", "备注"]]
    for item in equipment[bucket]:
        if bucket == "modified":
            rows.append(
                [
                    str(len(rows)),
                    item["category"],
                    item["tag"],
                    item["name"],
                    item["content"],
                    item["spec"],
                    item["quantity"],
                    item["remark"],
                ]
            )
        else:
            rows.append(
                [
                    str(len(rows)),
                    item["category"],
                    item["tag"],
                    item["name"],
                    item["spec"],
                    item["quantity"],
                    item["remark"],
                ]
            )
    return rows if len(rows) > 1 else []


def _utilities(facts: dict[str, Any]) -> list[list[str]]:
    """公用工程表：优先消费工程事实阶段整理出的公用工程汇总。"""
    rows = [["介质/负荷", "工况", "单位", "年需求量", "对象", "说明"]]
    summary = get_path(facts, "derived_facts.utility_consumption_summary") or {}
    for item in summary.get("items") or []:
        annual = item.get("annual_quantity") or {}
        rows.append(
            [
                _text(item.get("medium_name") or item.get("medium")),
                _condition_label(item.get("condition")),
                _unit(annual),
                _value(annual),
                _text(item.get("equipment_name") or item.get("equipment_id")),
                "",
            ]
        )
    if len(rows) == 1:
        rows.append(["未识别到可汇总公用工程", "待补充", "待补充", "待补充", "项目边界", "待补充"])
    return rows


def _energy(facts: dict[str, Any]) -> list[list[str]]:
    """能耗表：消费 energy_conversion 动态生成已折算项和未折算原因。"""
    conversion = get_path(facts, "derived_facts.energy_conversion") or {}
    result_key = (
        "standard_oil_toe"
        if conversion.get("conversion_type") == "standard_oil"
        else "standard_coal_tce"
    )
    result_header = "标准油toe/a" if result_key == "standard_oil_toe" else "标准煤tce/a"
    rows = [["项目", "工况", "单位", "全年消耗量", "折算系数/规则", result_header, "状态/原因"]]
    for item in conversion.get("items") or []:
        rows.append(
            [
                _text(item.get("medium_name") or item.get("medium")),
                _condition_label(item.get("condition")),
                _text(item.get("unit")),
                _value({"value": item.get("quantity")}),
                _coefficient_text(item),
                _value({"value": item.get(result_key)}),
                _energy_reason_text(item.get("reason") or item.get("status")),
            ]
        )
    total_key = "total_standard_oil_toe" if result_key == "standard_oil_toe" else "total_standard_coal_tce"
    for total in conversion.get("totals_by_condition") or []:
        rows.append(
            [
                "小计",
                _condition_label(total.get("condition")),
                "",
                "",
                "",
                _value({"value": total.get(total_key)}),
                "已折算",
            ]
        )
    if len(rows) == 1:
        rows.append(["未识别到可汇总公用工程/待补充", "待补充", "待补充", "待补充", "待补充", "待补充", "待补充"])
    return rows


def _investment_scope(facts: dict[str, Any]) -> list[list[str]]:
    """投资范围表：列出设备对象及其投资边界（最多 20 项）。"""
    rows = [["对象", "投资边界", "说明"]]
    for item in _equipment_items(facts)[:20]:
        rows.append(
            [
                _text(item.get("name") or item.get("equipment_name") or item.get("tag")),
                _text(item.get("action") or item.get("retrofit_type") or "待核实"),
                "设备专业结果待进一步核实",
            ]
        )
    if len(rows) == 1:
        rows.append(["设备及配套工程", "待补充", "待工程量和价格条件落实"])
    return rows


def _investment_summary(_: dict[str, Any]) -> list[list[str]]:
    """投资汇总表：固定费用科目结构，金额以待估算占位。"""
    return [
        ["费用项目", "金额（万元）", "编制/价格依据", "备注"],
        ["设备购置费", "待估算", "设备询价/价格基准", "按采用方案设备对象"],
        ["安装工程费", "待估算", "工程量及取费标准", ""],
        ["建筑/土建工程费", "待估算", "相关工程量及价格依据", "费用分类保留"],
        ["其他费用", "待估算", "取费规则", ""],
        ["预备费", "待估算", "取费规则", ""],
        ["建设期利息", "待估算", "融资参数", ""],
        ["流动资金", "待估算", "财务参数", ""],
        ["项目总投资", "待估算", "-", ""],
    ]


def _selected_combined_scheme_index(
    combined: list[Any],
    selected: dict[str, Any],
    diagnostics: list[dict[str, str]] | None,
) -> int | None:
    scheme_ids = [str(item.get("scheme_id")) for item in combined if isinstance(item, dict) and item.get("scheme_id")]
    if len(scheme_ids) != len(set(scheme_ids)):
        _append_warning(diagnostics, "KEY_EQUIPMENT_COMPARISON_SKIPPED", "reactor combined_schemes contains duplicate scheme_id.")
        return None

    selected_id = selected.get("scheme_id")
    if selected_id:
        matches = [index for index, item in enumerate(combined) if isinstance(item, dict) and item.get("scheme_id") == selected_id]
        if len(matches) == 1:
            return matches[0]
        _append_warning(
            diagnostics,
            "KEY_EQUIPMENT_COMPARISON_SKIPPED",
            f"reactor selected_scheme.scheme_id cannot be uniquely matched: {selected_id}",
        )
        return None

    selected_index = selected.get("scheme_index")
    matches = [index for index, item in enumerate(combined) if isinstance(item, dict) and item.get("scheme_index") == selected_index]
    if len(matches) == 1:
        return matches[0]
    _append_warning(
        diagnostics,
        "KEY_EQUIPMENT_COMPARISON_SKIPPED",
        f"reactor selected_scheme.scheme_index cannot be uniquely matched: {selected_index}",
    )
    return None


def _combined_action_summary(scheme: dict[str, Any]) -> str:
    parts = []
    for group, label in (("reuse", "利旧"), ("addition", "改造"), ("parallel", "并联"), ("heat_transfer", "换热改造")):
        names = [str(item.get("name") or item.get("id")) for item in scheme.get(group) or [] if isinstance(item, dict)]
        if names:
            parts.append(f"{label}：" + "、".join(names))
    return "；".join(parts)


def _main_product(products: list[dict[str, Any]]) -> dict[str, Any]:
    """从产品列表中选择主产品（is_main_product 为真），否则取第一个。"""
    for product in products:
        if product.get("is_main_product"):
            return product
    return products[0] if products else {}


def _streams_by_id(streams: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """把流股列表转换为 {stream_id: 流股} 映射，无 id 的流股被忽略。"""
    return {str(item.get("stream_id")): item for item in streams if item.get("stream_id")}


def _equipment_items(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """获取设备条目列表：优先取 equipment.object_catalog，否则扁平化 equipment。"""
    catalog = get_path(facts, "equipment.object_catalog")
    if isinstance(catalog, list):
        return [item for item in catalog if isinstance(item, dict)]
    return _flatten_dicts(get_path(facts, "equipment"))


def _reportable_equipment_actions(facts: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """解析 4.2.4 三张设备动作表的数据行。

    分类语义遵守 references/engineering_rules/equipment_rules.md：
    新增、利旧、改造进入正式表；无变化设备不列入。
    """
    object_map = {str(item.get("id")): item for item in _equipment_items(facts) if item.get("id") is not None}
    rows: dict[str, list[dict[str, str]]] = {"new": [], "reuse": [], "modified": []}
    _append_reactor_actions(rows, facts, object_map)
    _append_tower_actions(rows, facts, object_map)
    return rows


def _append_reactor_actions(
    rows: dict[str, list[dict[str, str]]],
    facts: dict[str, Any],
    object_map: dict[str, dict[str, Any]],
) -> None:
    selected = get_path(facts, "equipment.reactor.selected_scheme") or {}
    evaluations = get_path(facts, "equipment.reactor.evaluations") or []
    if not isinstance(selected, dict) or not isinstance(evaluations, list):
        return
    reactors = {str(item.get("id")): item for item in evaluations if isinstance(item, dict) and item.get("id") is not None}

    for item in selected.get("parallel") or []:
        rid = str(item.get("id"))
        reactor = reactors.get(rid, {})
        parameters = _matching_reactor_scheme(reactor, item.get("scheme_type"))
        quantity = max(int(_number(parameters.get("quantity")) or 2) - 1, 1)
        name = _report_name(f"{item.get('name') or reactor.get('name') or _object_name(object_map, rid)}_并联")
        rows["new"].append(
            {
                "category": "反应器类",
                "tag": "待定",
                "name": name,
                "spec": _reactor_spec(parameters, reactor.get("form")),
                "quantity": str(quantity),
                "content": "—",
                "remark": _text(parameters.get("description") or "新增并联设备"),
            }
        )

    for item in selected.get("reuse") or []:
        rid = str(item.get("id"))
        reactor = reactors.get(rid, {})
        parameters = _matching_reactor_scheme(reactor, item.get("scheme_type"))
        raw_name = str(item.get("name") or reactor.get("name") or _object_name(object_map, rid))
        name = f"{raw_name[1:]}（利旧设备）" if raw_name.startswith("新") else raw_name
        rows["reuse"].append(
            {
                "category": "反应器类",
                "tag": _tag(reactor, object_map, rid),
                "name": _report_name(name),
                "spec": _reactor_spec(parameters, reactor.get("form")),
                "quantity": str(int(_number(parameters.get("quantity")) or 1)),
                "content": "利用已有设备承担新的工程用途",
                "remark": _text(parameters.get("description") or "利旧用于新的工程用途/角色"),
            }
        )

    for item in selected.get("addition") or []:
        rid = str(item.get("id"))
        reactor = reactors.get(rid, {})
        parameters = _matching_reactor_scheme(reactor, item.get("scheme_type"))
        rows["modified"].append(
            {
                "category": "反应器类",
                "tag": _tag(reactor, object_map, rid),
                "name": _report_name(item.get("name") or reactor.get("name") or _object_name(object_map, rid)),
                "spec": _reactor_spec(parameters, reactor.get("form")),
                "quantity": "1",
                "content": _text(parameters.get("description") or "设备改造"),
                "remark": _text(reactor.get("conclusion") or parameters.get("description")),
            }
        )


def _append_tower_actions(
    rows: dict[str, list[dict[str, str]]],
    facts: dict[str, Any],
    object_map: dict[str, dict[str, Any]],
) -> None:
    combined = get_path(facts, "equipment.tower.combined_schemes") or {}
    evaluations = get_path(facts, "equipment.tower.evaluations") or []
    if not isinstance(combined, dict) or not isinstance(evaluations, list):
        return
    towers = {str(item.get("id")): item for item in evaluations if isinstance(item, dict) and item.get("id") is not None}
    for group, note_label in (("parallel", "并联"), ("series", "串联")):
        for pair in combined.get(group) or []:
            if not isinstance(pair, list) or len(pair) < 2:
                continue
            base, new = pair[0], pair[1]
            if not isinstance(base, dict) or not isinstance(new, dict) or not new.get("is_new"):
                continue
            base_id = str(base.get("id"))
            new_id = str(new.get("id"))
            new_detail = towers.get(new_id) or towers.get(base_id) or {}
            rows["new"].append(
                {
                    "category": "塔类",
                    "tag": "待定",
                    "name": _report_name(new.get("name") or _object_name(object_map, new_id)),
                    "spec": _tower_spec(new_detail, prefer_retrofit=True),
                    "quantity": "1",
                    "content": "—",
                    "remark": f"与{base.get('name') or base_id}{note_label}",
                }
            )


def _matching_reactor_scheme(reactor: dict[str, Any], scheme_type: Any) -> dict[str, Any]:
    for scheme in ((reactor.get("retrofit") or {}).get("schemes") or []):
        if scheme.get("scheme_type") == scheme_type:
            return scheme
    return {}


def _reactor_spec(parameters: dict[str, Any], form: Any = None) -> str:
    parts = []
    if form:
        parts.append(str(form))
    if parameters.get("diameter") is not None:
        parts.append(f"直径{_fmt_num(parameters['diameter'])} mm")
    if parameters.get("cylinder_height") is not None:
        parts.append(f"筒体高{_fmt_num(parameters['cylinder_height'])} mm")
    if parameters.get("total_volume") is not None:
        parts.append(f"总容积{float(parameters['total_volume']):.2f} m³")
    if parameters.get("catalyst_volume") is not None:
        parts.append(f"有效体积{float(parameters['catalyst_volume']):.2f} m³")
    if parameters.get("heat_exchange_area") is not None:
        parts.append(f"换热面积{_fmt_num(parameters['heat_exchange_area'])} m²")
    return "；".join(parts) or "设备主要规格待设备专业结果补充"


def _tower_spec(tower: dict[str, Any], prefer_retrofit: bool = False) -> str:
    plan = ((tower.get("retrofit") or {}).get("plan_detail") or {}) if isinstance(tower, dict) else {}
    parameters = plan.get("device_paras") if isinstance(plan, dict) else None
    parts = _tower_spec_from_device_parameters(parameters) if prefer_retrofit else []
    if not parts:
        detail = tower.get("detail") if isinstance(tower, dict) else {}
        parts = _tower_spec_from_separator_limit((detail or {}).get("separator_limit") if isinstance(detail, dict) else None)
    return "；".join(parts) or "设备主要规格待设备专业结果补充"


def _tower_spec_from_device_parameters(parameters: Any) -> list[str]:
    if not isinstance(parameters, dict):
        return []
    parts = []
    type_label = _tower_type_label(parameters.get("tray_type"))
    if type_label:
        parts.append(type_label)
    pairs = [item for item in parameters.get("struct_info") or [] if isinstance(item, list | tuple) and len(item) >= 2]
    diameters = [_fmt_num(item[0]) for item in pairs if item[0] is not None]
    heights = [_fmt_num(item[1]) for item in pairs if item[1] is not None]
    if diameters:
        parts.append("直径" + "/".join(diameters) + " mm")
    if heights:
        label = "填料高度" if str(parameters.get("tray_type") or "").upper() in {"BX", "PACKED", "PACKING"} else "板间距"
        parts.append(label + "/".join(heights) + " mm")
    if parameters.get("tray_num") is not None:
        parts.append(f"理论/计算级数{_fmt_num(parameters.get('tray_num'))}")
    return parts


def _tower_spec_from_separator_limit(limit: Any) -> list[str]:
    if not isinstance(limit, dict):
        return []
    parts = []
    sections = [item for item in limit.get("hydraulic_sections") or [] if isinstance(item, dict)]
    type_label = _tower_type_label((sections[0] or {}).get("type") if sections else None)
    if type_label:
        parts.append(type_label)
    diameters = [_fmt_num(float(item["D"]) * 1000) for item in sections if item.get("D") is not None]
    spacings = [_fmt_num(float(item["HT"]) * 1000) for item in sections if item.get("HT") is not None]
    if diameters:
        parts.append("直径" + "/".join(diameters) + " mm")
    if spacings:
        parts.append("板间距" + "/".join(spacings) + " mm")
    tray_count = limit.get("n_trays") if limit.get("n_trays") is not None else limit.get("N_trays")
    if tray_count is not None:
        parts.append(f"理论/计算级数{_fmt_num(tray_count)}")
    return parts


def _tower_type_label(raw_type: Any) -> str:
    value = str(raw_type or "").strip()
    upper = value.upper()
    if not value:
        return ""
    if upper == "F1":
        return "F1浮阀塔"
    if upper in {"BX", "PACKED", "PACKING"} or "填料" in value:
        return "填料塔"
    if "F1" in upper:
        return f"{value}浮阀塔"
    return f"{value}型塔"


def _fmt_num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _tag(source: dict[str, Any], object_map: dict[str, dict[str, Any]], equipment_id: str) -> str:
    obj = object_map.get(equipment_id, {})
    return _text(source.get("tag") or obj.get("tag") or "待定")


def _object_name(object_map: dict[str, dict[str, Any]], equipment_id: str) -> str:
    return _text((object_map.get(equipment_id) or {}).get("name") or equipment_id)


def _report_name(value: Any) -> str:
    name = str(_text(value))
    return (
        name.replace("_并联", "（并联新增）")
        .replace("_串联", "（串联新增）")
        .replace("_", "-")
    )


def _candidate_source_label(value: Any) -> str:
    labels = {"candidates": "算法候选", "user_candidates": "用户自定义"}
    return labels.get(str(value or ""), _text(value))


def _dimension_label(key: str, value: Any) -> str:
    known = {
        "technical_feasibility": "技术可行性",
        "implementation_complexity": "实施复杂度",
        "operational_risk": "运行风险",
    }
    if isinstance(value, dict):
        for field in ("label", "display_name", "name"):
            label = value.get(field)
            if isinstance(label, str) and label.strip():
                return label.strip()
    if key in known:
        return known[key]
    if any("\u4e00" <= char <= "\u9fff" for char in key):
        return key
    return key.replace("_", " ").strip().title() or key


def _evaluation_cell(evaluation: Any, key: str) -> str:
    if not isinstance(evaluation, dict) or key not in evaluation:
        return "待补充"
    value = evaluation.get(key)
    if isinstance(value, dict):
        for field in ("level", "value", "comment"):
            text = value.get(field)
            if text not in (None, "", [], {}):
                return _text(text)
        return "待补充"
    return _text(value)


def _append_warning(
    diagnostics: list[dict[str, str]] | None,
    code: str,
    message: str,
) -> None:
    if diagnostics is not None:
        diagnostics.append({"level": "warning", "code": code, "message": message})


def _flatten_dicts(value: Any) -> list[dict[str, Any]]:
    """递归扁平化嵌套 dict/list，收集所有“叶子级”的 dict 条目。

    当某 dict 含有非 dict/list 的标量值时，视其自身为一条叶子记录，
    同时继续往下扁平化其子节点。
    """
    items: list[dict[str, Any]] = []
    if isinstance(value, dict):
        # 含标量值 => 认为是叶子记录
        if any(not isinstance(v, (dict, list)) for v in value.values()):
            items.append(value)
        for child in value.values():
            items.extend(_flatten_dicts(child))
    elif isinstance(value, list):
        for child in value:
            items.extend(_flatten_dicts(child))
    return items


def _level(evaluation: dict[str, Any], key: str) -> str:
    """从评价对象中读取某指标的 level（等级）并转为文本。"""
    value = evaluation.get(key, {})
    return _text(value.get("level") if isinstance(value, dict) else value)


def _direction(stream: dict[str, Any]) -> str:
    """根据源/目标设备 id 是否为空判断流股方向：输入/输出/内部。"""
    if stream.get("source_equipment_id") == "":
        return "输入"
    if stream.get("target_equipment_id") == "":
        return "输出"
    return "内部"


def _change(before: Any, after: Any) -> str:
    """计算改造前后的变化率（百分比），无法计算时返回 "-"。"""
    before_value = _number(before)
    after_value = _number(after)
    if before_value is None or after_value is None or before_value == 0:
        return "-"
    return f"{(after_value - before_value) / before_value * 100:+.1f}%"


def _condition_label(value: Any) -> str:
    labels = {"design": "改造前", "retrofit": "改造后"}
    return labels.get(str(value or ""), _text(value))


def _coefficient_text(item: dict[str, Any]) -> str:
    coefficient = item.get("coefficient")
    unit = item.get("coefficient_unit")
    rule_id = item.get("rule_id")
    if coefficient in (None, ""):
        return _text(rule_id)
    text = f"{coefficient} {unit}" if unit else str(coefficient)
    return f"{text}（{rule_id}）" if rule_id else text


def _energy_reason_text(value: Any) -> str:
    labels = {
        "calculated": "已折算",
        "blocked": "未折算",
        "partial": "部分折算",
        "conversion_factor_missing": "缺少折标系数",
        "physical_quantity_missing": "缺少可折算实物量",
        "quantity_or_hours_missing": "缺少数量或年运行小时数",
        "steam_pressure_or_grade_missing": "缺少蒸汽压力或压力等级",
        "quantity_or_factor_missing": "缺少数量或折标系数",
        "unit_mismatch": "输入单位与规则单位不匹配",
    }
    return labels.get(str(value or ""), _text(value))


def _number(value: Any) -> float | None:
    """把可能为 {value: ...} 的字典值解析为浮点数。"""
    if isinstance(value, dict):
        value = value.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _value(value: Any) -> str:
    """把值格式化为显示字符串：数字带千分位，float 保留两位小数，其余转文本。"""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return _text(value)


def _unit(value: Any) -> str:
    """从可能是 {value, unit} 的值中提取单位，无单位返回空串。"""
    return _text(value.get("unit") if isinstance(value, dict) else "")


def _text(value: Any) -> str:
    """把任意值转为非空文本：空值统一显示为“待补充”。"""
    if value in (None, "", [], {}):
        return "待补充"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _fmt(value: Any) -> str:
    """把事实值格式化为可读字符串（逐字段递归，列表最多取 6 项）。"""
    if isinstance(value, dict):
        return "；".join(f"{key}={_fmt(child)}" for key, child in value.items())
    if isinstance(value, list):
        return "；".join(_fmt(item) for item in value[:6])
    return _text(value)


def _stream_sort_key(value: str) -> tuple[int, str]:
    """流股 id 排序键：优先按其中的数字排序，无数字的排到最后。"""
    digits = "".join(ch for ch in value if ch.isdigit())
    return (int(digits) if digits else 999999, value)
