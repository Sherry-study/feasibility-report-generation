#!/usr/bin/env python3
"""事实阶段确定性计算器：年化计算与标准煤/标准油双体系能耗折标。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.common import ENGINEERING_RULES_ROOT, load_data
from internal.domain import (
    annual_tonnes, first_product_stream, normalize_media_name, normalize_conversion_type,
    energy_rule_index, energy_steam_block, steam_alias_names, match_steam_rule, harmonize_unit,
)


def calculate_from_facts(facts, hours=None):
    """从 facts 中读取产物流股和年运行时长，计算年产能与年物耗。"""
    if hours is None:
        hours=(facts.get('user') or {}).get('annual_operating_hours')
    if hours in (None,''):
        return {'status':'blocked','reason':'annual_operating_hours_missing','annual_operating_hours':None}
    hours=float(hours)
    if hours.is_integer(): hours=int(hours)
    design=first_product_stream(facts,'design',{}); retrofit=first_product_stream(facts,'retrofit',{})
    materials=[]
    for s in (((facts.get('process') or {}).get('retrofit') or {}).get('external_feeds') or []):
        materials.append({
            'stream_id':s.get('stream_id'),'name':s.get('name'),'hourly_kg_h':s.get('flow_kg_h'),
            'annual_t_a':annual_tonnes(s.get('flow_kg_h'),hours,strict=True)
        })
    return {
        'status':'calculated','annual_operating_hours':hours,
        'annual_capacity':{
            'design_t_a':annual_tonnes(design.get('flow_kg_h'),hours,strict=True),
            'retrofit_t_a':annual_tonnes(retrofit.get('flow_kg_h'),hours,strict=True),
            'formula':'hourly_kg_h × annual_operating_hours / 1000'
        },
        'annual_material_consumption':{
            'items':materials,'formula':'hourly_kg_h × annual_operating_hours / 1000'
        }
    }


def calculate_standalone(flow_kg_h, hours):
    """单独计算某一小时流量的年化吨/年结果。"""
    return {'status':'calculated','annual_operating_hours':hours,'annual_t_a':annual_tonnes(flow_kg_h,hours,strict=True),'formula':'hourly_kg_h × annual_operating_hours / 1000'}


ENERGY_RULES_PATH=ENGINEERING_RULES_ROOT/'energy_conversion_rules.json'


def load_energy_rules(path=None):
    """加载能耗折标规则；默认读取 references/engineering_rules。"""
    return load_data(Path(path) if path else ENERGY_RULES_PATH)


def _item_quantity(x):
    """兼容不同上游字段名，读取能耗介质实物量。"""
    for k in ('quantity','annual_quantity','amount','value'):
        if x.get(k) is not None:
            return x.get(k)
    return None


def _item_unit(x):
    """兼容不同上游字段名，读取能耗介质单位。"""
    for k in ('unit','annual_unit','units'):
        if x.get(k):
            return x.get(k)
    return None


def _item_pressure(x):
    """读取蒸汽压力或等级匹配所需压力字段。"""
    for k in ('pressure_mpa','pressure','gauge_pressure','表压','蒸汽表压'):
        if x.get(k) not in (None,''):
            return x.get(k)
    return None


def convert(items, rules, conversion_type='standard_coal'):
    """在单一核算体系下执行确定性折标计算。

    `standard_coal` 对应 GB/T 2589（tce），`standard_oil` 对应
    GB/T 50441（toe）。标准油体系下蒸汽按可信压力等级标签或表压范围匹配；
    不四舍五入系数，不让 LLM 猜测。
    """
    ctype=normalize_conversion_type(conversion_type) or 'standard_coal'
    block=(rules or {}).get(ctype) or {}
    idx=energy_rule_index(rules,ctype)
    steam=energy_steam_block(rules,ctype)
    per_key='tce_per_input_unit' if ctype=='standard_coal' else 'toe_per_input_unit'
    res_key='standard_coal_tce' if ctype=='standard_coal' else 'standard_oil_toe'
    total_key='total_standard_coal_tce' if ctype=='standard_coal' else 'total_standard_oil_toe'
    rule_version=(rules.get('metadata') or {}).get('version')
    converted=[]; missing=[]; total=0.0

    def _source(rule, fallback=None):
        # 蒸汽 range 规则的 source（标准/条款）定义在 steam 块层级，逐规则回退
        src=rule.get('source') or {}
        fb=(fallback or {}).get('source') or {}
        return {
            'standard':src.get('standard') or fb.get('standard') or block.get('standard'),
            'clause':src.get('clause') or fb.get('clause'),
            'note':src.get('note') or fb.get('note'),
            'rule_version':rule_version,
        }

    for x in items:
        name=x.get('medium') or x.get('name') or x.get('energy_type')
        qty=_item_quantity(x); unit=_item_unit(x)
        rule=None; steam_grade=None
        norm=normalize_media_name(name)
        steam_aliases=steam_alias_names(steam) if steam else set()
        is_steam=bool(steam) and (norm in steam_aliases or any(a in norm for a in steam_aliases if a))
        src_fb=steam if is_steam else None

        if is_steam:
            rule=match_steam_rule(steam,name,_item_pressure(x))
            if rule is None:
                converted.append({'medium':name,'status':'blocked','reason':'steam_pressure_or_grade_missing',
                                  'hint':'提供可信压力等级标签（如“3.5MPa级蒸汽”）或蒸汽实际表压MPa',
                                  'source':_source(steam)})
                continue
            steam_grade=rule.get('pressure_grade') or rule.get('label')
        else:
            rule=idx.get(norm)
            if rule is None and not steam and any(a in norm for a in ('蒸汽','steam')):
                # 折标准煤：蒸汽为单一系数，介质名含压力等级（如“3.5MPa蒸汽”）时同样适用
                rule=idx.get('蒸汽')
            if rule is None:
                missing.append({'medium':name,'quantity':qty,'unit':unit}); continue

        if rule.get('match_type')=='formula' and rule.get(per_key) is None:
            formula_input=x.get('formula_input') or x.get('equivalent_coefficient')
            cond=rule.get('condition') or {}
            if formula_input is None and isinstance(cond,dict):
                formula_input=x.get(cond.get('variable'))
            if formula_input in (None,''):
                converted.append({'medium':name,'rule_id':rule.get('rule_id'),'status':'blocked','reason':'formula_input_missing',
                                  'required_input':cond.get('variable') if isinstance(cond,dict) else None,
                                  'source':_source(rule,src_fb)})
                continue
            per_unit=float(formula_input)/1000.0
            coefficient=float(formula_input)
        else:
            per_unit=rule.get(per_key); coefficient=rule.get('coefficient')

        if qty is None or per_unit is None:
            converted.append({'medium':name,'rule_id':rule.get('rule_id'),'status':'blocked','reason':'quantity_or_factor_missing','source':_source(rule,src_fb)}); continue

        rule_unit=rule.get('input_unit')
        if rule_unit in (None,'') and is_steam and steam:
            # 蒸汽 range 规则的输入单位定义在 steam 块层级（input_unit: t）
            rule_unit=steam.get('input_unit')
        if unit in (None,''):
            scale=1.0; canonical=rule_unit
        else:
            scale,canonical=harmonize_unit(unit,rule_unit)
            if scale is None:
                converted.append({'medium':name,'rule_id':rule.get('rule_id'),'status':'blocked','reason':'unit_mismatch',
                                  'input_unit':unit,'expected_unit':rule_unit,'source':_source(rule,src_fb)}); continue

        value=float(qty)*scale*float(per_unit); total+=value
        converted.append({
            'medium':name,'rule_id':rule.get('rule_id'),'energy_name':rule.get('energy_name'),
            'quantity':qty,'unit':canonical,
            'coefficient':coefficient,'coefficient_unit':rule.get('coefficient_unit'),
            'steam_grade':steam_grade,
            res_key:round(value,6),
            'status':'calculated','source':_source(rule,src_fb),
        })

    ok=all(x.get('status')=='calculated' for x in converted)
    return {
        'status':'calculated' if ok and converted and not missing else ('partial' if converted or missing else 'blocked'),
        'conversion_type':ctype,
        'standard':block.get('standard'),
        'total_unit':block.get('total_unit'),
        'items':converted,'missing_media':missing,
        total_key:round(total,6),
    }


def energy_conversion_from_facts(facts, rules=None):
    """按已解析核算体系，对 facts['energy']['consumption'] 执行 FA 能耗折标。"""
    rules=rules or load_energy_rules()
    energy=facts.get('energy') or {}
    items=energy.get('consumption') or []
    if not items:
        return {'status':'blocked','reason':'energy_consumption_missing',
                'conversion_type':energy.get('accounting_standard') or 'standard_coal',
                'note':'未提供纳入核算的能源介质实物量；按 missing_energy_conversion_factor / energy_consumption 缺口处理'}
    result=convert(items,rules,energy.get('accounting_standard') or 'standard_coal')
    result['conversion_type_source']=energy.get('accounting_standard_source') or 'default'
    return result
