#!/usr/bin/env python3
"""工程事实构建器。

本模块把上游 PA/EA/FA、用户输入和候选方案资料规整为统一 Project Facts。
它只做字段搬运、单位/状态归一和确定性派生，不替代专业算法，也不为报告
正文创造未经确认的工程数字。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
import yaml

BASE=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))
from internal.facts.sources import adapt_candidate_scheme
from internal.common import ENGINEERING_RULES_ROOT, load_data, dump_json
from internal.domain import annual_tonnes, fa_utility_margin_blocked, reactor_action_category, resolve_annual_hours, resolve_conversion_type, GAP_AFFECTED_SECTIONS


# ===== 事实构建主实现区（原 runtime/fact_builder_impl.py） =====

def load_text(path: Path):
    """读取 UTF-8 文本文件，供 Markdown/JSON 派生解析使用。"""
    return path.read_text(encoding='utf-8')


def _coalesce(*vals):
    """返回第一个非空值，用于兼容多版本上游字段。"""
    for v in vals:
        if v is not None and v != '':
            return v
    return None


def _route_changed(changes, key, project_type):
    """原料/工艺路线变更标志：算法输出明确给出时尊重原值；扩能改造项目在无变更证据时按未变更（False）处理。"""
    v=(changes or {}).get(key)
    if v is not None: return v
    if (project_type or '')=='capacity_expansion': return False
    return None



def _plant_info_hours(payload):
    if not isinstance(payload, dict):
        return None
    info=payload.get('plant_info')
    if not isinstance(info, dict):
        return None
    value=info.get('annual_operating_hours')
    if value in (None, ''):
        return None
    try:
        value=float(value)
        if value <= 0:
            return None
        return int(value) if value.is_integer() else value
    except Exception:
        return None


# 企业主体键名提示：用于从算法输出（plant_info/plant_level 等）自动提取建设单位，
# 能取到就不必再向用户提问。优先级：用户输入 > manifest > 算法输出。
_ENTERPRISE_KEY_HINTS=('construction_unit','owner','enterprise','company','organization','operator','建设单位','企业名称','单位名称','主办单位','运营单位','所属企业')
_ENTERPRISE_KEY_CN=('建设单位','企业名称','单位名称','主办单位','运营单位','所属企业')
_ENTERPRISE_KEY_TOKENS={h for h in _ENTERPRISE_KEY_HINTS if h.isascii()}


def _is_enterprise_key(lower_key, raw_key):
    tokens=set(lower_key.replace('-','_').split('_'))
    if tokens & _ENTERPRISE_KEY_TOKENS:
        return True
    return any(h in raw_key for h in _ENTERPRISE_KEY_CN)


def _enterprise_from_sources(*payloads):
    """在算法输出中递归查找建设单位/企业名称（深度<=3）。

    仅当键名明确指向企业主体且值为 2~60 字符的非空文本时采用，
    避免把数值、位号或人名误当企业名。
    """
    for payload in payloads:
        stack=[(payload,0)]
        while stack:
            cur,depth=stack.pop()
            if depth>3:
                continue
            if isinstance(cur,dict):
                for k,v in cur.items():
                    if _is_enterprise_key(str(k).strip().lower(),str(k)) and isinstance(v,str):
                        name=v.strip()
                        if 2<=len(name)<=60:
                            return name
                    if isinstance(v,(dict,list)):
                        stack.append((v,depth+1))
            elif isinstance(cur,list):
                for item in cur:
                    if isinstance(item,(dict,list)):
                        stack.append((item,depth+1))
    return None


def source_meta(manifest):
    """从输入 manifest 生成 Project Facts 中的来源元信息。"""
    out = []
    for key, s in manifest['sources'].items():
        out.append({
            'source_id': key,
            'source_type': s['role'],
            'file': s['file'],
            'version': s.get('version'),
            'baseline_status': manifest.get('baseline_status'),
            'description': s.get('description'),
            'approval_status': ('candidate' if key=='candidate_scheme' else ('frozen' if s['role'] in ('PA','EA') else 'reference'))
        })
    return out


def comp_maps(components):
    """生成组分 formula/name 的双向查找表。"""
    code_to_name = {c.get('formula'): c.get('name') for c in components if c.get('formula')}
    name_to_code = {c.get('name'): c.get('formula') for c in components if c.get('name')}
    return code_to_name, name_to_code


def normalize_streams(streams, semantic_names, code_to_name, condition, source_file):
    """把 PA 流股表规整为 Project Facts 统一流股结构。"""
    out = []
    for s in streams:
        comps = []
        for c in s.get('composition', []):
            code = c.get('component')
            comps.append({
                'component_code': code,
                'component_name': code_to_name.get(code, code),
                'mass_fraction': c.get('mass_fraction'),
                'mole_fraction': c.get('mole_fraction')
            })
        out.append({
            'stream_id': s.get('id'),
            'name': semantic_names.get(s.get('id')) or s.get('name'),
            'raw_name': s.get('name'),
            'source_equipment_id': s.get('source'),
            'target_equipment_id': s.get('target'),
            'flow_kg_h': (s.get('flow_rate') or {}).get('value'),
            'flow_unit': (s.get('flow_rate') or {}).get('unit'),
            'temperature_c': (s.get('temperature') or {}).get('value'),
            'pressure_mpa': (s.get('pressure') or {}).get('value'),
            'phase': s.get('phase'),
            'target_product_stream': bool(s.get('target_product_stream')),
            'composition': comps,
            '_meta': {
                'source_type': 'PA',
                'source_file': source_file,
                'source_path': f'{condition}_case.streams[id={s.get("id")}]',
                'condition': condition,
                'approval_status': 'frozen'
            }
        })
    return out


def parse_diagnosis(md):
    """从诊断 Markdown 中提取收率、反应器和瓶颈摘要。"""
    result = {'raw_markdown': md}
    m = re.search(r'\|\s*设计总收率\s*\|\s*([0-9.]+)%', md)
    if m:
        result.setdefault('yield_summary', {})['design_total_yield_pct'] = float(m.group(1))
    m = re.search(r'\|\s*运行总收率\s*\|\s*([0-9.]+)%', md)
    if m:
        result.setdefault('yield_summary', {})['operating_total_yield_pct'] = float(m.group(1))
    reactor_rows = []
    for m in re.finditer(r'\|\s*([^|\n]+?)\s*\|\s*([0-9.]+)%\s*\|\s*([0-9.]+)%\s*\|\s*([^|\n]+?)\s*\|', md):
        name=m.group(1).strip()
        if name != '设备名称':
            reactor_rows.append({'name':name,'design_conversion_pct':float(m.group(2)),'operating_conversion_pct':float(m.group(3)),'status':m.group(4).strip()})
    result['reactor_summary'] = reactor_rows
    bottlenecks=[]
    for m in re.finditer(r'\|\s*([0-9]+)\s*\|\s*([^|\n]+?)\s*\|\s*([^|\n]+?)\s*\|', md):
        if m.group(1) != '设备编号':
            bottlenecks.append({'equipment_id':m.group(1),'description':m.group(2).strip(),'recommendation':m.group(3).strip()})
    result['bottlenecks'] = bottlenecks
    return result


def find_by_id(items, id_value, id_key='id'):
    """按 id 字段查找列表元素，找不到返回 None。"""
    for x in items:
        if str(x.get(id_key)) == str(id_value):
            return x
    return None


def _reactor_recommended_scheme_index(reactor_result):
    """解析 EA 明确推荐的组合反应器方案序号。

    不仅凭数值排序推断推荐方案。结论中写明“推荐方案N”时采用 N；
    否则尊重 recommended/is_recommended 标记。旧 EA payload 没有明确标记时，
    为兼容 V0.10/V0.11 契约，回退采用第一个 combined scheme。
    """
    text=str(reactor_result.get('conclusion') or '')
    m=re.search(r'推荐方案\s*([0-9]+)', text)
    if m:
        return int(m.group(1))
    for sc in reactor_result.get('combined_schemes') or []:
        if sc.get('recommended') is True or sc.get('is_recommended') is True:
            try: return int(sc.get('scheme_index'))
            except Exception: return sc.get('scheme_index')
    arr=reactor_result.get('combined_schemes') or []
    return (arr[0] or {}).get('scheme_index') if arr else None


def _reactor_selected_scheme(reactor_result):
    """解析报告应采用的组合反应器方案。

    优先级遵守字段映射表使用注意：用户最终选择 `selected_combined_scheme`
    优先；否则回退到算法推荐方案，并标记 basis 为 `algorithm_recommendation`，
    便于后续确认门提示用户确认。无 combined_schemes 的旧数据返回空。
    """
    sel=reactor_result.get('selected_combined_scheme')
    if isinstance(sel,dict) and sel:
        return sel,'user_selected'
    combined=reactor_result.get('combined_schemes') or []
    idx=_reactor_recommended_scheme_index(reactor_result)
    sc=next((x for x in combined if x.get('scheme_index')==idx), combined[0] if combined else None)
    if sc:
        return sc,'algorithm_recommendation'
    return None,None


def _scheme_matches(sc, selected):
    """判断候选组合方案是否与选中方案指向同一对象。"""
    if not isinstance(sc,dict) or not isinstance(selected,dict):
        return False
    if sc.get('scheme_id') and selected.get('scheme_id'):
        return sc.get('scheme_id')==selected.get('scheme_id')
    return sc.get('scheme_index')==selected.get('scheme_index')


def reactor_solution_sets(reactor_result, source_file='reactor_result'):
    """Preserve EA candidate routes instead of flattening them to the selected action only.

    Output is report-neutral Project Facts: combined reactor-group alternatives plus any
    individual reactor that itself has multiple retrofit routes. Economic/period judgements
    are intentionally not synthesized here; those remain downstream gaps until supported.
    """
    selected_scheme, selection_basis=_reactor_selected_scheme(reactor_result)
    sets=[]
    combined=[]
    reactors=reactor_result.get('reactors') or []
    id_to_reactor={str(x.get('id')):x for x in reactors}
    for sc in reactor_result.get('combined_schemes') or []:
        idx=sc.get('scheme_index')
        is_selected=_scheme_matches(sc, selected_scheme)
        actions=[]
        for group in ('reuse','addition','parallel','heat_transfer','no_retrofit'):
            for item in sc.get(group) or []:
                rid=str(item.get('id'))
                robj=id_to_reactor.get(rid) or {}
                actions.append({
                    'equipment_id':rid,
                    'tag':robj.get('tag'),
                    'name':item.get('name') or robj.get('name'),
                    'action_group':group,
                    'scheme_type':item.get('scheme_type')
                })
        combined.append({
            'solution_id':f'reactor_combined_{idx}',
            'scheme_index':idx,
            'scheme_id':sc.get('scheme_id'),
            'name':f'反应器综合方案{idx}',
            'route_summary':sc.get('conclusion'),
            'total_new_equipment_volume_m3':sc.get('total_new_equipment_volume_m3'),
            'actions':actions,
            'selection_status':'recommended' if is_selected else 'alternative',
            'selection_basis_source':selection_basis,
            '_meta':{'source_type':'EA','source_file':source_file,'source_path':f'combined_schemes[scheme_index={idx}]','condition':'retrofit'}
        })
    if len(combined) > 1:
        tags=[str(r.get('tag') or r.get('name') or r.get('id')) for r in reactors if r.get('retrofit_required')]
        sets.append({
            'solution_set_id':'reactor_group_combined',
            'scope_level':'equipment_group',
            'equipment_type':'reactor',
            'display_name':'反应器组',
            'alternatives':combined,
            'selected_solution_id':next((x.get('solution_id') for x in combined if x.get('selection_status')=='recommended'),None),
            'selected_scheme_id':(selected_scheme or {}).get('scheme_id'),
            'selection_basis':(selected_scheme or {}).get('conclusion') or reactor_result.get('conclusion'),
            'selection_basis_source':selection_basis,
            'pending_user_confirmation':True if selection_basis=='algorithm_recommendation' else None,
            '_meta':{'source_type':'EA','source_file':source_file,'source_path':'selected_combined_scheme + combined_schemes','condition':'retrofit'}
        })
    for r in reactors:
        schemes=((r.get('retrofit') or {}).get('schemes') or [])
        if len(schemes) <= 1:
            continue
        rid=str(r.get('id'))
        selected_type=None
        selected_combined=next((x for x in combined if x.get('selection_status')=='recommended'),None)
        if selected_combined:
            a=next((a for a in selected_combined.get('actions') or [] if str(a.get('equipment_id'))==rid),None)
            selected_type=(a or {}).get('scheme_type')
        alternatives=[]
        for sc in schemes:
            st=sc.get('scheme_type')
            alternatives.append({
                'solution_id':f'reactor_{rid}_{sc.get("scheme_index")}',
                'scheme_index':sc.get('scheme_index'),
                'scheme_type':st,
                'name':sc.get('description') or f'方案{sc.get("scheme_index")}',
                'route_summary':sc.get('description'),
                'parameters':sc,
                'selection_status':'recommended' if selected_type and st==selected_type else 'alternative',
                '_meta':{'source_type':'EA','source_file':source_file,'source_path':f'reactors[id={rid}].retrofit.schemes[scheme_index={sc.get("scheme_index")}]','condition':'retrofit'}
            })
        sets.append({
            'solution_set_id':f'reactor_{rid}',
            'scope_level':'equipment',
            'equipment_type':'reactor',
            'equipment_id':rid,
            'equipment_tag':r.get('tag'),
            'display_name':r.get('name') or r.get('tag') or rid,
            'alternatives':alternatives,
            'selected_solution_id':next((x.get('solution_id') for x in alternatives if x.get('selection_status')=='recommended'),None),
            'selection_basis':r.get('conclusion'),
            '_meta':{'source_type':'EA','source_file':source_file,'source_path':f'reactors[id={rid}].retrofit.schemes','condition':'retrofit'}
        })
    return sets


def selected_reactor_actions(reactor_result, source_file='reactor_result'):
    selected, basis = _reactor_selected_scheme(reactor_result)
    if not selected:
        return {}, []
    scheme_ref = 'selected_combined_scheme' if basis == 'user_selected' else f'combined_schemes[scheme_index={selected.get("scheme_index")}]'
    action_map = {}
    for group in ('reuse','addition','parallel','heat_transfer','no_retrofit'):
        for item in selected.get(group, []) or []:
            action_map[str(item.get('id'))] = {'group': group, **item}
    rows=[]
    for rid, a in action_map.items():
        r=find_by_id(reactor_result.get('reactors',[]), rid)
        if not r:
            rows.append({'equipment_id':rid,'action':a,'status':'source_object_missing'})
            continue
        scheme_type=a.get('scheme_type')
        chosen=None
        for sc in ((r.get('retrofit') or {}).get('schemes') or []):
            if sc.get('scheme_type') == scheme_type:
                chosen=sc; break
        category = reactor_action_category(a['group'])
        rows.append({
            'equipment_id': rid,
            'tag': r.get('tag'),
            'name': r.get('name'),
            'form': r.get('form'),
            'report_category': category,
            'action_group': a['group'],
            'scheme_type': scheme_type,
            'scheme_description': (chosen or {}).get('description'),
            'selected_parameters': chosen,
            'evaluation_status': r.get('evaluation_status'),
            'retrofit_required': r.get('retrofit_required'),
            'conclusion': r.get('conclusion'),
            'validation': r.get('validation'),
            '_meta': {'source_type':'EA','source_file':source_file,'source_path':f'reactors[id={rid}] + {scheme_ref}','condition':'retrofit','approval_status':'frozen'}
        })
    return selected, rows


def selected_tower_actions(tower_result, source_file='tower_result'):
    cs=tower_result.get('combined_schemes') or {}
    rows=[]
    expected_new=[]
    for item in cs.get('optimal',[]) or []:
        rid=str(item.get('id'))
        r=find_by_id(tower_result.get('separator',[]),rid)
        rows.append({'equipment_id':rid,'name':item.get('name'),'report_category':'无变化','action_group':'optimal','route':((r or {}).get('retrofit') or {}).get('route'),'detail':r,'_meta':{'source_type':'EA','source_file':source_file,'source_path':f'combined_schemes.optimal[id={rid}]','condition':'retrofit','approval_status':'frozen'}})
    for pair in cs.get('parallel',[]) or []:
        if len(pair)>=2:
            old,new=pair[0],pair[1]
            rid=str(old.get('id')); r=find_by_id(tower_result.get('separator',[]),rid)
            rows.append({'equipment_id':rid,'name':old.get('name'),'report_category':'无变化','action_group':'parallel_base','route':((r or {}).get('retrofit') or {}).get('route'),'detail':r,'_meta':{'source_type':'EA','source_file':source_file,'source_path':f'combined_schemes.parallel[{rid}]','condition':'retrofit','approval_status':'frozen'}})
            expected_new.append(str(new.get('id')))
            rows.append({'equipment_id':str(new.get('id')),'name':new.get('name'),'report_category':'新增','action_group':'parallel_new','is_new':True,'based_on':rid,'detail':((r or {}).get('retrofit') or {}).get('plan_detail',{}).get('optimal_retrofit'),'device_paras':((r or {}).get('retrofit') or {}).get('plan_detail',{}).get('device_paras'),'_meta':{'source_type':'EA','source_file':source_file,'source_path':f'combined_schemes.parallel[new={new.get("id")}]','condition':'retrofit','approval_status':'frozen'}})
    for pair in cs.get('series',[]) or []:
        if len(pair)>=2:
            old,new=pair[0],pair[1]
            rid=str(old.get('id')); r=find_by_id(tower_result.get('separator',[]),rid)
            rows.append({'equipment_id':rid,'name':old.get('name'),'report_category':'无变化','action_group':'series_base','route':((r or {}).get('retrofit') or {}).get('route'),'detail':r,'_meta':{'source_type':'EA','source_file':source_file,'source_path':f'combined_schemes.series[{rid}]','condition':'retrofit','approval_status':'frozen'}})
            expected_new.append(str(new.get('id')))
            new_detail=find_by_id(tower_result.get('separator',[]),str(new.get('id')))
            rows.append({'equipment_id':str(new.get('id')),'name':new.get('name'),'report_category':'新增','action_group':'series_new','is_new':True,'based_on':rid,'detail':new_detail,'_meta':{'source_type':'EA','source_file':source_file,'source_path':f'combined_schemes.series[new={new.get("id")}]','condition':'retrofit','approval_status':'frozen'}})
    for item in cs.get('other',[]) or []:
        rid=str(item.get('id')); r=find_by_id(tower_result.get('separator',[]),rid)
        rows.append({'equipment_id':rid,'name':item.get('name'),'report_category':'无变化','action_group':'other','detail':r,'_meta':{'source_type':'EA','source_file':source_file,'source_path':f'combined_schemes.other[id={rid}]','condition':'retrofit','approval_status':'frozen'}})
    return cs, rows, expected_new


def extract_reactor_utilities(reactor_rows, source_file='reactor_result'):
    out=[]
    for r in reactor_rows:
        sc=r.get('selected_parameters') or {}
        op=sc.get('operating_conditions') or {}
        u=op.get('utility')
        if u:
            out.append({
                'equipment_id':r.get('equipment_id'),
                'equipment_name':r.get('name'),
                'medium':u.get('medium'),
                'inlet_temperature_c':u.get('inlet_temperature_c'),
                'outlet_temperature_c':u.get('outlet_temperature_c'),
                'mass_flow_kg_h':u.get('mass_flow_kg_h'),
                '_meta': {'source_type':'EA','source_file':source_file,'condition':'retrofit'}
            })
    return out


def build_issues(expected_new_equipment_ids, topo, equip_catalog, equipment_rows):
    """Cross-source object consistency checks without project-specific IDs/names."""
    issues=[]
    topo_nodes={str(x.get('id')):x for x in topo.get('nodes',[])}
    eq_nodes={str(x.get('id')):x for x in equip_catalog}
    expected=[str(x) for x in (expected_new_equipment_ids or [])]
    missing_topo=[x for x in expected if x not in topo_nodes]
    missing_eq=[x for x in expected if x not in eq_nodes]
    if missing_topo:
        issues.append({'severity':'high','code':'EXPECTED_NEW_EQUIPMENT_MISSING_IN_TOPOLOGY','message':'设备专业选定方案要求的部分新增设备未进入改造拓扑。','expected_ids':expected,'missing_ids':missing_topo,'action':'回溯拓扑生成/设备汇总，补齐设备专业反馈。'})
    if missing_eq:
        issues.append({'severity':'high','code':'EXPECTED_NEW_EQUIPMENT_MISSING_IN_EQUIPMENT_CATALOG','message':'设备专业选定方案要求的部分新增设备未进入设备对象集。','missing_ids':missing_eq,'action':'补齐设备对象后再冻结4.2.4主要设备一览表。'})
    for eid in sorted(set(topo_nodes) & set(eq_nodes)):
        tn=(topo_nodes[eid].get('name') or '').strip(); en=(eq_nodes[eid].get('name') or '').strip()
        if tn and en and tn != en:
            issues.append({'severity':'medium','code':'EQUIPMENT_NAME_MISMATCH','equipment_id':eid,'message':f'设备ID {eid}名称不一致：topology={tn}；equipment={en}。','action':'统一对象主数据名称，以稳定equipment_id/位号作为主键。'})
    names={}
    for e in topo.get('edges',[]): names.setdefault(e.get('name'),[]).append(e.get('id'))
    for name,ids in names.items():
        if name and len(ids)>1:
            issues.append({'severity':'low','code':'DUPLICATE_STREAM_NAME','message':f'改造拓扑存在重复流股名称“{name}”：{ids}','action':'报告引用以stream_id为主键，显示名后续修正。'})
    for r in equipment_rows or []:
        if r.get('report_category') in ('新增','利旧','改造') and not r.get('tag'):
            issues.append({'severity':'medium','code':'EQUIPMENT_TAG_MISSING','equipment_id':r.get('equipment_id'),'message':f'设备“{r.get("name") or r.get("equipment_id") or "未命名设备"}”暂无正式位号。','action':'4.2.4设备表可标“待定”，正式版需项目/设备专业确认。'})
    return issues



def build_post_stage_recommendation(topo, tower_conclusion, reactor_conclusion, reactor_selected=None):
    """Normalize the later-stage engineering recommendation used by the report.

    Candidate-scheme algorithm recommendations are intentionally excluded here.
    The authoritative report-facing recommendation is assembled from the frozen
    retrofit topology plus selected equipment results available after scheme
    validation/engineering feedback. reactor_selected is the combined reactor
    scheme the report adopts: user-selected (selected_combined_scheme) when
    available, otherwise the algorithm recommendation flagged pending user
    confirmation (映射表 使用注意#1); reactor_conclusion alone describes the
    algorithm-recommended scheme and is not the adoption basis.
    """
    raw_changes=topo.get('schemeInfo',[]) or []
    if isinstance(raw_changes, dict):
        changes=[raw_changes]
    elif isinstance(raw_changes, list):
        changes=raw_changes
    else:
        changes=[]
    primary=None
    for item in changes:
        if not isinstance(item,dict):
            continue
        if item.get('sourceType')=='separator_feedback' or item.get('name')=='series_retrofit':
            continue
        primary=item
        break
    if primary is None and changes:
        primary=changes[0] if isinstance(changes[0],dict) else None
    return {
        'stage':'post_validation_recommendation',
        'status':'available' if (primary or tower_conclusion or reactor_conclusion) else 'not_available',
        'scheme_id': (primary or {}).get('scheme_id') or (primary or {}).get('schemeId') or (primary or {}).get('id'),
        'scheme_name': (primary or {}).get('name'),
        'scheme_description': (primary or {}).get('description'),
        'description_basis':'final_retrofit_topology',
        'topology_changes': changes,
        'tower_result': tower_conclusion,
        'reactor_result': reactor_conclusion,
        'reactor_selected_scheme': reactor_selected,
        'source_precedence':['retrofit_topology','EA_tower_selected_scheme','EA_reactor_selected_scheme'],
        '_meta':{
            'source_type':'PA+EA',
            'stage':'post_validation',
            'report_role':'formal_scheme_recommendation',
            'note':'候选方案身份来自首次推荐池；后续优化属于同一方案内部迭代。正式报告的最终方案描述只使用最终改造拓扑及最终设备事实，不写算法试错/失败历史。反应器采用方案以 selected_combined_scheme（用户选定）为准；缺失时按算法推荐编制并标记待用户确认。'
        }
    }

def coverage_status():
    return [
      {'section':'3.1','status':'partial','reason':'改造前后产品小时流量/组成可取；年运行时间未提供，年产能FA暂不能冻结。'},
      {'section':'4.1.1','status':'ready','reason':'按已冻结规则简写“沿用现有原料路线”。'},
      {'section':'4.1.2','status':'ready','reason':'按已冻结规则简写“沿用现有工艺路线”。'},
      {'section':'4.1.3','status':'partial','reason':'装置方案、塔/反应器专业方案可编；投资/运行费用等经济比较缺失。'},
      {'section':'4.1.4','status':'ready_with_checks','reason':'诊断、改造拓扑、流股、关键设备参数已具备；需处理塔器反馈与拓扑不一致。'},
      {'section':'4.2.1','status':'ready_with_checks','reason':'改造拓扑可生成流程说明；需补齐设备专业要求的新增设备到拓扑后再冻结。'},
      {'section':'4.2.2','status':'ready','reason':'design_case/retrofit_case streams可形成物料平衡摘要。'},
      {'section':'4.2.3','status':'partial','reason':'主要原料小时流量及部分设备公用工程可用，完整装置公用工程消耗缺失。'},
      {'section':'4.2.4','status':'partial_blocked_for_freeze','reason':'EA设备参数足够生成草表，但设备专业新增对象与装置设备对象集需保持一致。'},
      {'section':'4.4','status':'blocked','reason':'缺自控专业输入。'},
      {'section':'5.1','status':'partial','reason':'原料小时用量可取；年运行时间、规格和供应方式缺失。'},
      {'section':'5.4','status':'partial','reason':'反应器HO/CW等设备级需求可取；完整水电汽气需求未覆盖。'},
      {'section':'7.1-7.3','status':'blocked','reason':'缺总图、道路、储运、外管等企业/专业资料。'},
      {'section':'8.1','status':'blocked','reason':'缺企业公用工程系统能力、当前负荷和可用余量。'},
      {'section':'10','status':'blocked','reason':'缺完整能耗实物量输入；折标规则已内置（默认折标准煤GB/T 2589，折标准油需显式指定）。'},
      {'section':'18.2','status':'partial','reason':'设备改造量可支撑各工作包工期估算，章节起草可结合外部常规工期基准组织语言，无需用户确认。'},
      {'section':'19-21','status':'blocked','reason':'投资/融资/财务规则与输入尚未冻结。'},
      {'section':'26','status':'ready_conditional','reason':'可汇总已完成事实和缺口，但不得形成未有依据的完整可行性结论。'}
    ]


# ===== 事实归一化区（原独立事实解析 Tool 逻辑上移） =====

LEGACY_FULLSET={'plant_level','plant_diagnosis','retrofit_topology','retrofit_equipment','new_device_params','tower_result','reactor_result'}


def adopted_from_legacy(facts):
    later=facts.get('post_stage_recommendation') or {}
    available=later.get('status')=='available'
    tower_rows=((facts.get('equipment') or {}).get('tower') or {}).get('report_rows') or []
    reactor_rows=((facts.get('equipment') or {}).get('reactor') or {}).get('report_rows') or []
    actions=[]
    for row in tower_rows+reactor_rows:
        actions.append({'equipment_id':row.get('equipment_id'),'equipment_name':row.get('name') or row.get('tag'),'report_category':row.get('report_category'),'action_group':row.get('action_group'),'scheme_type':row.get('scheme_type'),'source_type':((row.get('_meta') or {}).get('source_type'))})
    return {
        'status':'available' if available else 'not_available','selection_status':'adopted' if available else 'unknown',
        'scheme_id':later.get('scheme_id'),'scheme_name':later.get('scheme_name'),'scheme_description':later.get('scheme_description'),
        'topology_changes':later.get('topology_changes') or [],'equipment_actions':actions,
        'reactor_selected_scheme':later.get('reactor_selected_scheme'),
        'effective_basis':later.get('source_precedence') or [],
        'description_basis':later.get('description_basis') or 'final_topology_and_final_equipment_facts',
        'iteration_history_required_for_report':False,
        '_meta':{'report_role':'global_adopted_scheme','candidate_recommendation_is_not_adopted_scheme':True}
    }


def normalize_facts(facts):
    meta=facts.get('meta') or {}
    facts['meta']={'schema_version':'1.1','generated_at':meta.get('generated_at'),'scope':'feasibility_report_project_facts','adapter_mode':meta.get('adapter_mode') or 'legacy_fullstack'}
    adopted=adopted_from_legacy(facts); facts['adopted_scheme']=adopted
    facts['scheme_state']={'status':'adopted' if adopted['status']=='available' else 'undetermined','adopted_scheme_name':adopted.get('scheme_name'),'candidate_pool_rule':'4.1.3始终使用第一次推荐阶段形成的完整候选池；后续优化属于同一方案内部迭代，不形成新的可研候选方案身份。','downstream_rule':'adopted_scheme可用时，除4.1.3候选方案比较外，其他章节不得回退为最终方案未定；最终方案描述以最终拓扑和最终设备事实为准。'}
    facts.pop('post_stage_recommendation',None)
    facts['fa']={'annual_capacity':{'status':'not_calculated','tool':'annualization_calculator'},'annual_material_consumption':{'status':'not_calculated','tool':'annualization_calculator'},'utility_margin':fa_utility_margin_blocked(),'energy_conversion':{'status':'not_calculated','tool':'energy_conversion_calculator'},'investment':{'status':'not_available'},'finance':{'status':'not_available'}}
    facts['gaps']=[g for g in (facts.get('gaps') or []) if g.get('field') not in ('candidate_to_final_scheme_mapping','candidate_to_adopted_scheme_history')]
    return facts


def build_legacy_facts(input_dir, manifest_path, user_inputs_path=None):
    """Legacy fullstack 适配核心：--input-dir/--manifest/--user-inputs -> Project Facts dict。

    进程内等价于原 CLI 路径（python internal/facts/builders.py ...），不再需要
    subprocess 或 tempdir JSON 中转；__main__ 薄壳与本函数共用同一实现。
    """
    input_dir=Path(input_dir)
    manifest=yaml.safe_load(Path(manifest_path).read_text(encoding='utf-8'))
    ui_path=Path(user_inputs_path).resolve() if user_inputs_path else None
    if ui_path is not None and not ui_path.exists():
        raise FileNotFoundError(f'user inputs not found: {ui_path}')
    user_inputs=load_data(ui_path) if ui_path is not None else {}
    ui_project=user_inputs.get('project',{}) if isinstance(user_inputs,dict) else {}
    ui_enterprise=user_inputs.get('enterprise',{}) if isinstance(user_inputs,dict) else {}
    manifest_project=manifest.get('project',{}) if isinstance(manifest.get('project'),dict) else {}
    profile=manifest.get('project_profile',{}) if isinstance(manifest.get('project_profile'),dict) else {}
    changes=profile.get('changes',{}) if isinstance(profile.get('changes'),dict) else {}

    def p(source_id): return input_dir / manifest['sources'][source_id]['file']
    plant=load_data(p('plant_level'))
    plant_info=load_data(p('plant_info')) if 'plant_info' in manifest.get('sources',{}) else {}
    topo=load_data(p('retrofit_topology'))
    equip=load_data(p('retrofit_equipment'))
    newparams=load_data(p('new_device_params'))
    tower=load_data(p('tower_result'))
    reactor=load_data(p('reactor_result'))
    diagnosis=parse_diagnosis(load_text(p('plant_diagnosis')))
    candidate_scheme=None
    if 'candidate_scheme' in manifest.get('sources',{}):
        candidate_raw=load_data(p('candidate_scheme'))
        candidate_scheme=adapt_candidate_scheme(candidate_raw, manifest['sources']['candidate_scheme']['file'])

    code_to_name,_=comp_maps(plant.get('components',[]))
    semantic={str(e.get('id')):e.get('name') for e in topo.get('edges',[])}
    design_streams=normalize_streams(plant['design_case']['streams'],semantic,code_to_name,'design',manifest['sources']['plant_level']['file'])
    retrofit_streams=normalize_streams(plant['retrofit_case']['streams'],semantic,code_to_name,'retrofit',manifest['sources']['plant_level']['file'])
    external_ids=[str(e.get('id')) for e in topo.get('edges',[]) if not e.get('source')]
    design_feeds=[s for s in design_streams if s['stream_id'] in external_ids]
    retrofit_feeds=[s for s in retrofit_streams if s['stream_id'] in external_ids]
    design_products=[s for s in design_streams if s.get('target_product_stream')]
    retrofit_products=[s for s in retrofit_streams if s.get('target_product_stream')]

    r_selected, r_rows=selected_reactor_actions(reactor, manifest['sources']['reactor_result']['file'])
    r_solution_sets=reactor_solution_sets(reactor, manifest['sources']['reactor_result']['file'])
    t_selected, t_rows, tower_expected_new=selected_tower_actions(tower, manifest['sources']['tower_result']['file'])
    utilities=extract_reactor_utilities(r_rows, manifest['sources']['reactor_result']['file'])
    issues=build_issues(tower_expected_new,topo,equip.get('equipment',[]),t_rows+r_rows)
    algo_construction_unit=_enterprise_from_sources(plant_info,plant)
    construction_unit=_coalesce(ui_project.get('construction_unit'), manifest_project.get('construction_unit'), algo_construction_unit)
    if ui_project.get('construction_unit'): construction_unit_source='user_input'
    elif manifest_project.get('construction_unit'): construction_unit_source='manifest'
    elif algo_construction_unit: construction_unit_source='algorithm_output'
    else: construction_unit_source=None
    plant_info_hours=_plant_info_hours(plant_info)
    annual_hours,annual_hours_source=resolve_annual_hours(ui_project.get('annual_operating_hours'),plant_info_hours,manifest_project.get('annual_operating_hours'))
    project_location=_coalesce(ui_project.get('project_location'), manifest_project.get('project_location'))
    enterprise_profile=ui_enterprise.get('basic_profile')

    design_annual=annual_tonnes((design_products[0] if design_products else {}).get('flow_kg_h'), annual_hours)
    retrofit_annual=annual_tonnes((retrofit_products[0] if retrofit_products else {}).get('flow_kg_h'), annual_hours)
    annual_material=[]
    for s0 in retrofit_feeds:
        annual_material.append({'stream_id':s0.get('stream_id'),'name':s0.get('name'),'hourly_kg_h':s0.get('flow_kg_h'),'annual_t_a':annual_tonnes(s0.get('flow_kg_h'),annual_hours)})

    if candidate_scheme:
        issues.append({'severity':'medium','code':'CANDIDATE_RECOMMENDATION_NOT_FINAL_SELECTION','message':'已识别装置级候选方案算法输出；其中recommendation仅代表candidate阶段算法推荐，不等同于最终工程采用方案。','action':'4.1.3可用于候选方案比较；最终采用方案应以后续显式选择、验证结果或冻结的工程化拓扑/设备结果确认。'})

    reactor_selected_summary=None
    if isinstance(reactor, dict):
        _r_sel,_r_basis=_reactor_selected_scheme(reactor)
        if _r_sel:
            reactor_selected_summary={
                'scheme_id':_r_sel.get('scheme_id'),
                'scheme_index':_r_sel.get('scheme_index'),
                'total_new_equipment_volume_m3':_r_sel.get('total_new_equipment_volume_m3'),
                'conclusion':_r_sel.get('conclusion'),
                'selection_basis_source':_r_basis
            }
            if _r_basis=='algorithm_recommendation':
                reactor_selected_summary['pending_user_confirmation']=True
                _label=_r_sel.get('scheme_id') or f"方案{_r_sel.get('scheme_index')}"
                issues.append({'severity':'medium','code':'REACTOR_SCHEME_NOT_USER_CONFIRMED','message':f'反应器算法输出未含用户选定方案（selected_combined_scheme），报告暂按算法推荐方案（{_label}）编制。','action':'请用户确认最终采用方案；用户选定与算法推荐数值可能存在细微差异（如推荐52.89 m³ vs 选定52.80 m³），确认后以选定方案为准。'})

    coverage=coverage_status()
    for item in coverage:
        if item.get('section')=='3.1' and annual_hours is not None:
            item.update(status='ready',reason='产品小时流量/组成及年运行时间已具备，可派生年生产规模。')
        if item.get('section')=='5.1' and annual_hours is not None:
            item.update(status='partial',reason='主要原料小时流量及年运行时间已具备，可派生年需用量；规格和供应方式仍可能缺失。')
        if item.get('section')=='4.1.3' and candidate_scheme:
            item.update(status='ready_with_checks',reason='候选方案比较资料已具备；正式推荐结论以后段改造拓扑及塔器/反应器选定结果为准。')

    post_stage_recommendation=build_post_stage_recommendation(topo,tower.get('conclusion'),reactor.get('conclusion'),reactor_selected=reactor_selected_summary)

    pn=manifest['project_name']
    pn_source=manifest_project.get('project_name_source') or manifest.get('project_name_source')
    if ui_project.get('project_name') and (not pn or pn=='待确认项目' or (pn_source or 'default') in ('workspace_dir','default','algorithm_output')):
        pn=ui_project['project_name']; pn_source='user_input'

    ui_energy=user_inputs.get('energy',{}) if isinstance(user_inputs,dict) and isinstance(user_inputs.get('energy'),dict) else {}
    accounting_standard,accounting_standard_source=resolve_conversion_type(ui_energy.get('accounting_standard'),profile.get('energy_accounting_standard'))

    facts={
      'meta':{
        'schema_version':'1.0',
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'baseline_status':manifest.get('baseline_status'),
        'note':'当前版本用于跑通可研Skill整本编制链，不代表设计院正式交付深度。'
      },
      'project':{
        'project_id':manifest['project_id'],
        'project_name':pn,
        'project_name_source':pn_source,
        'project_level':profile.get('project_level') or manifest_project.get('project_level') or 'unit',
        'project_type':profile.get('project_type') or manifest_project.get('project_type') or 'mixed',
        'profile_provenance':profile.get('profile_provenance') or {},
        'construction_unit':construction_unit,
        'construction_unit_source':construction_unit_source,
        'project_location':project_location,
        'raw_material_route_changed':_route_changed(changes,'raw_material_route_changed',profile.get('project_type') or manifest_project.get('project_type')),
        'process_route_changed':_route_changed(changes,'process_route_changed',profile.get('project_type') or manifest_project.get('project_type'))
      },
      'sources':source_meta(manifest),
      'component_catalog':plant.get('components',[]),
      'process':{
        'design':{
          'streams':design_streams,
          'external_feeds':design_feeds,
          'product_streams':design_products,
          'separators':plant['design_case'].get('separator_config',[]),
          'reactors':plant['design_case'].get('reactor_config',[]),
          'topology':plant['design_case'].get('topology',{})
        },
        'retrofit':{
          'streams':retrofit_streams,
          'external_feeds':retrofit_feeds,
          'product_streams':retrofit_products,
          'separators':plant['retrofit_case'].get('separator_config',[]),
          'reactors':plant['retrofit_case'].get('reactor_config',[]),
          'topology':{'nodes':topo.get('nodes',[]),'edges':topo.get('edges',[])},
          'scheme_changes':topo.get('schemeInfo',[]),
          'new_device_process_params':newparams
        }
      },
      'diagnosis':diagnosis,
      'scheme_analysis': candidate_scheme or {'stage':'not_available','candidates':[],'algorithm_recommendation':None},
      'post_stage_recommendation': post_stage_recommendation,
      'equipment':{
        'object_catalog':equip.get('equipment',[]),
        'tower':{
          'version':manifest['sources']['tower_result'].get('version') or 'current',
          'conclusion':tower.get('conclusion'),
          'selected_scheme_set':t_selected,
          'report_rows':t_rows,
          'expected_new_equipment_ids':tower_expected_new
        },
        'reactor':{
          'conclusion':reactor.get('conclusion'),
          'selected_scheme':r_selected,
          'selection_basis_source':reactor_selected_summary.get('selection_basis_source') if reactor_selected_summary else None,
          'solution_sets':r_solution_sets,
          'report_rows':r_rows,
          'utility_requirements':utilities
        }
      },
      'user':{
        'owner':construction_unit,
        'construction_unit':construction_unit,
        'annual_operating_hours':annual_hours,
        'annual_operating_hours_source':annual_hours_source,
        'utility_system_capacity':None,
        'project_location':project_location,
        'supply_conditions':None,
        'energy_accounting_media':ui_energy.get('accounting_media'),
        'energy_accounting_standard':accounting_standard
      },
      'enterprise':{
        'basic_profile':enterprise_profile,
        'basic_profile_source':'user_input' if enterprise_profile else None
      },
      'energy':{
        'accounting_media':ui_energy.get('accounting_media') or [],
        'accounting_standard':accounting_standard,
        'accounting_standard_source':accounting_standard_source,
        'consumption':ui_energy.get('consumption') or [],
        'conversion_rules':load_data(ENGINEERING_RULES_ROOT/'energy_conversion_rules.json'),
        'note':'默认折标准煤（GB/T 2589-2020，电力当量值、蒸汽128.6 kgce/t）；折标准油（GB/T 50441-2016）仅在用户显式指定/模板口径/专项指标/上游冻结口径时启用。不从设备级HO/CW等公用工程线索自动推断能耗折标边界；由项目资料/后续Adapter明确纳入核算的能源介质。'
      },
      'fa':{
        'annual_capacity':({'status':'calculated','annual_operating_hours':annual_hours,'design_t_a':design_annual,'retrofit_t_a':retrofit_annual,'formula':'hourly_kg_h × annual_operating_hours / 1000'} if annual_hours is not None else {'status':'blocked','reason':'annual_operating_hours missing'}),
        'annual_material_consumption':({'status':'calculated','annual_operating_hours':annual_hours,'items':annual_material,'formula':'hourly_kg_h × annual_operating_hours / 1000'} if annual_hours is not None else {'status':'blocked','reason':'annual_operating_hours missing'}),
        'utility_margin':fa_utility_margin_blocked(),
        'energy_conversion':{'status':'blocked','reason':'complete energy physical quantities / accounting boundary missing; dual-system conversion rules are built in (default: standard coal, GB/T 2589)'},
        'investment':{'status':'not_implemented_1.0'},
        'finance':{'status':'not_implemented_1.0'}
      },
      'gaps':[
        {'responsibility':'U','field':'annual_operating_hours','reason':'计算年产能、年原辅料消耗必需'},
        {'responsibility':'U','field':'utility_system_capacity/current_load','reason':'8.1公用工程余量/缺口计算必需'},
        {'responsibility':'U/C','field':'equipment_tags_for_new_objects','reason':'部分新增设备无正式位号'},
        {'responsibility':'U/PA','field':'energy_consumption_physical_quantities_and_boundary','reason':'第10章节能计算需要明确纳入核算的能源介质及其完整实物量；折标规则已内置（默认折标准煤，蒸汽折油标需提供表压/压力等级）'},
        {'responsibility':'U/R/C','field':'general_layout_storage_pipe_network','reason':'7.1-7.3当前输出未覆盖'},
        {'responsibility':'C/FA','field':'investment_finance_rules','reason':'19-21尚未冻结'}
      ],
      'consistency_issues':issues,
      'section_coverage':coverage
    }

    if annual_hours is not None:
        facts['gaps']=[g for g in facts['gaps'] if g.get('field')!='annual_operating_hours']
    if construction_unit:
        facts['gaps']=[g for g in facts['gaps'] if g.get('field')!='construction_unit']
    else:
        facts['gaps'].insert(0,{'responsibility':'U','field':'construction_unit','reason':'用于1.1.1/1.1.2建设单位信息；若用户不提供则报告保留缺口并继续生成'})

    return facts


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input-dir',required=True)
    ap.add_argument('--manifest',required=True)
    ap.add_argument('--output-dir',required=True)
    ap.add_argument('--user-inputs')
    args=ap.parse_args()
    out_dir=Path(args.output_dir); out_dir.mkdir(parents=True,exist_ok=True)
    facts=build_legacy_facts(args.input_dir,args.manifest,args.user_inputs)
    issues=facts['consistency_issues']

    dump_json(facts,out_dir/'project_facts.sample.json')
    dump_json(issues,out_dir/'consistency_report.json')
    dump_json(facts['section_coverage'],out_dir/'section_coverage.json')

    # Human-readable reports
    lines=['# 当前版本 数据一致性检查','']
    if not issues:
        lines.append('未发现阻断性问题。')
    else:
        for i,x in enumerate(issues,1):
            lines += [f'## {i}. {x["code"]}｜{x["severity"]}', x['message'], '', f'**建议处理：** {x.get("action","")}', '']
    (out_dir/'数据一致性检查.md').write_text('\n'.join(lines),encoding='utf-8')

    lines=['# 当前版本 章节覆盖情况','', '| 章节 | 状态 | 当前判断 |','|---|---|---|']
    for x in facts['section_coverage']:
        lines.append(f'| {x["section"]} | {x["status"]} | {x["reason"]} |')
    (out_dir/'章节覆盖情况.md').write_text('\n'.join(lines),encoding='utf-8')

    # Brief extracted facts for quick inspection
    dsn=facts['process']['design']; rft=facts['process']['retrofit']
    brief={
      'design_external_feeds':[{k:v for k,v in s.items() if k in ('stream_id','name','flow_kg_h')} for s in dsn['external_feeds']],
      'retrofit_external_feeds':[{k:v for k,v in s.items() if k in ('stream_id','name','flow_kg_h')} for s in rft['external_feeds']],
      'design_products':[{k:v for k,v in s.items() if k in ('stream_id','name','flow_kg_h')} for s in dsn['product_streams']],
      'retrofit_products':[{k:v for k,v in s.items() if k in ('stream_id','name','flow_kg_h')} for s in rft['product_streams']],
      'diagnosis_yield':facts['diagnosis'].get('yield_summary'),
      'tower_conclusion':facts['equipment']['tower']['conclusion'],
      'reactor_conclusion':facts['equipment']['reactor']['conclusion'],
      'reactor_selected_scheme':(facts.get('post_stage_recommendation') or {}).get('reactor_selected_scheme'),
      'reactor_utilities':facts['equipment']['reactor']['utility_requirements'],
      'candidate_scheme_count':len((facts['scheme_analysis'] or {}).get('candidates',[])),
      'candidate_algorithm_recommendation':(facts['scheme_analysis'] or {}).get('algorithm_recommendation'),
      'construction_unit':facts['project']['construction_unit'],
      'annual_operating_hours':facts['user']['annual_operating_hours'],
      'consistency_issue_count':len(issues)
    }
    dump_json(brief,out_dir/'关键事实快照.json')

    print(json.dumps({'output_dir':str(out_dir),'issues':len(issues),'coverage_items':len(facts['section_coverage'])},ensure_ascii=False))

if __name__=='__main__':
    main()


# ===== 通用事实构建区（原 runtime/generic_fact_builder.py） =====

def _src_by_type(manifest):
    out={}
    for sid,s in (manifest.get('sources') or {}).items():
        if not isinstance(s,dict): continue
        st=s.get('source_type') or sid
        out.setdefault(st,[]).append((sid,s))
    return out


def _path(input_dir, source):
    p=Path(source.get('file') or source.get('path'))
    return p if p.is_absolute() else Path(input_dir)/p


def _first_source(src_by_type, st):
    arr=src_by_type.get(st) or []
    return arr[0] if arr else (None,None)


def _load_json_type(input_dir, src_by_type, st):
    sid,s=_first_source(src_by_type,st)
    if not s: return None,None,None
    p=_path(input_dir,s)
    return load_data(p), sid, s


def _load_text_type(input_dir, src_by_type, st):
    sid,s=_first_source(src_by_type,st)
    if not s: return None,None,None
    p=_path(input_dir,s)
    return load_text(p), sid, s


def _source_meta(manifest):
    rows=[]
    for sid,s in (manifest.get('sources') or {}).items():
        st=s.get('source_type') or sid
        rows.append({
            'source_id':sid,'source_type':st,'source_role':s.get('role'),
            'file':s.get('file'),'version':s.get('version'),'capabilities':s.get('capabilities') or [],
            'approval_status':'candidate' if st=='candidate_scheme' else ('frozen' if s.get('role') in ('PA','EA') else 'reference')
        })
    return rows


def _empty_case():
    return {'streams':[],'external_feeds':[],'product_streams':[],'separators':[],'reactors':[],'topology':{'nodes':[],'edges':[]}}


def build_generic_facts(input_dir, manifest, user_inputs=None):
    user_inputs=user_inputs or {}
    ui_project=(user_inputs.get('project') or {}) if isinstance(user_inputs,dict) else {}
    ui_enterprise=(user_inputs.get('enterprise') or {}) if isinstance(user_inputs,dict) else {}
    manifest_project=(manifest.get('project') or {}) if isinstance(manifest.get('project'),dict) else {}
    profile=(manifest.get('project_profile') or {}) if isinstance(manifest.get('project_profile'),dict) else {}
    changes=(profile.get('changes') or {}) if isinstance(profile.get('changes'),dict) else {}
    srcs=_src_by_type(manifest)

    plant,_,plant_src=_load_json_type(input_dir,srcs,'plant_level')
    plant_info,_,plant_info_src=_load_json_type(input_dir,srcs,'plant_info')
    topo,_,topo_src=_load_json_type(input_dir,srcs,'retrofit_topology')
    equip,_,equip_src=_load_json_type(input_dir,srcs,'retrofit_equipment')
    newparams,_,newparams_src=_load_json_type(input_dir,srcs,'new_device_params')
    tower,_,tower_src=_load_json_type(input_dir,srcs,'tower_result')
    reactor,_,reactor_src=_load_json_type(input_dir,srcs,'reactor_result')
    diagnosis_text,_,diag_src=_load_text_type(input_dir,srcs,'plant_diagnosis')
    candidate_raw,_,candidate_src=_load_json_type(input_dir,srcs,'candidate_scheme')

    plant=plant or {}; plant_info=plant_info or {}; topo=topo or {}; equip=equip or {}; newparams=newparams or {}; tower=tower or {}; reactor=reactor or {}
    diagnosis=parse_diagnosis(diagnosis_text) if diagnosis_text else {}
    candidate_scheme=adapt_candidate_scheme(candidate_raw, candidate_src.get('file')) if candidate_raw and candidate_src else None

    components=plant.get('components') or []
    code_to_name,_=comp_maps(components)
    semantic={str(e.get('id')):e.get('name') for e in topo.get('edges',[])}
    design=_empty_case(); retrofit=_empty_case()
    if isinstance(plant.get('design_case'),dict):
        dc=plant['design_case']; streams=normalize_streams(dc.get('streams') or [],semantic,code_to_name,'design',(plant_src or {}).get('file') or 'process_case_result')
        design.update({'streams':streams,'separators':dc.get('separator_config') or [],'reactors':dc.get('reactor_config') or [],'topology':dc.get('topology') or {'nodes':[],'edges':[]}})
    if isinstance(plant.get('retrofit_case'),dict):
        rc=plant['retrofit_case']; streams=normalize_streams(rc.get('streams') or [],semantic,code_to_name,'retrofit',(plant_src or {}).get('file') or 'process_case_result')
        retrofit.update({'streams':streams,'separators':rc.get('separator_config') or [],'reactors':rc.get('reactor_config') or []})
    if topo:
        retrofit['topology']={'nodes':topo.get('nodes') or [],'edges':topo.get('edges') or []}
        retrofit['scheme_changes']=topo.get('schemeInfo') or []
    if newparams: retrofit['new_device_process_params']=newparams

    external_ids={str(e.get('id')) for e in topo.get('edges',[]) if not e.get('source')}
    for case in (design,retrofit):
        case['external_feeds']=[s for s in case.get('streams',[]) if (not s.get('source_equipment_id')) or str(s.get('stream_id')) in external_ids]
        case['product_streams']=[s for s in case.get('streams',[]) if s.get('target_product_stream')]

    t_selected={}; t_rows=[]; expected_new=[]
    if tower:
        t_selected,t_rows,expected_new=selected_tower_actions(tower,(tower_src or {}).get('file') or 'equipment_result')
    r_selected={}; r_rows=[]; r_solution_sets=[]; reactor_selected_summary=None
    if reactor:
        r_selected,r_rows=selected_reactor_actions(reactor,(reactor_src or {}).get('file') or 'equipment_result')
        r_solution_sets=reactor_solution_sets(reactor,(reactor_src or {}).get('file') or 'equipment_result')
        _r_sel,_r_basis=_reactor_selected_scheme(reactor)
        if _r_sel:
            reactor_selected_summary={
                'scheme_id':_r_sel.get('scheme_id'),
                'scheme_index':_r_sel.get('scheme_index'),
                'total_new_equipment_volume_m3':_r_sel.get('total_new_equipment_volume_m3'),
                'conclusion':_r_sel.get('conclusion'),
                'selection_basis_source':_r_basis
            }
    utilities=extract_reactor_utilities(r_rows,(reactor_src or {}).get('file') or 'equipment_result') if r_rows else []
    issues=build_issues(expected_new,topo,equip.get('equipment') or [],t_rows+r_rows)
    if reactor_selected_summary and reactor_selected_summary.get('selection_basis_source')=='algorithm_recommendation':
        reactor_selected_summary['pending_user_confirmation']=True
        _label=reactor_selected_summary.get('scheme_id') or f"方案{reactor_selected_summary.get('scheme_index')}"
        issues.append({'severity':'medium','code':'REACTOR_SCHEME_NOT_USER_CONFIRMED','message':f'反应器算法输出未含用户选定方案（selected_combined_scheme），报告暂按算法推荐方案（{_label}）编制。','action':'请用户确认最终采用方案；用户选定与算法推荐数值可能存在细微差异（如推荐52.89 m³ vs 选定52.80 m³），确认后以选定方案为准。'})

    algo_construction_unit=_enterprise_from_sources(plant_info,plant)
    construction_unit=ui_project.get('construction_unit') or manifest_project.get('construction_unit') or algo_construction_unit
    if ui_project.get('construction_unit'): construction_unit_source='user_input'
    elif manifest_project.get('construction_unit'): construction_unit_source='manifest'
    elif algo_construction_unit: construction_unit_source='algorithm_output'
    else: construction_unit_source=None
    project_location=ui_project.get('project_location') or manifest_project.get('project_location')
    plant_info_hours=_plant_info_hours(plant_info)
    annual_hours,annual_hours_source=resolve_annual_hours(ui_project.get('annual_operating_hours'),plant_info_hours,manifest_project.get('annual_operating_hours'),reject_nonpositive=True,reset_source_on_error=True)

    post=build_post_stage_recommendation(topo,tower.get('conclusion'),reactor.get('conclusion'),reactor_selected=reactor_selected_summary)
    level=profile.get('project_level') or manifest_project.get('project_level') or 'unit'
    ptype=profile.get('project_type') or manifest_project.get('project_type') or 'mixed'

    gaps=[]
    def gap(resp,field,reason,sections=None):
        gaps.append({'responsibility':resp,'field':field,'reason':reason,'affected_sections':list(sections) if sections else []})
    if not construction_unit: gap('U','construction_unit','用于项目基本情况与建设单位描述。',GAP_AFFECTED_SECTIONS['construction_unit'])
    if annual_hours is None: gap('U','annual_operating_hours','年产量、年原辅料量及部分经济指标年化需要。',GAP_AFFECTED_SECTIONS['annual_operating_hours'])
    if not plant:
        gap('PA/U','process_case_result','未获得可归一为改造前/后工况、流股或物料衡算的输入；相关章节只能保留结构化缺口。',GAP_AFFECTED_SECTIONS['process_case_result'])
    if not (topo or post.get('status')=='available'):
        gap('PA/C','adopted_topology_or_scheme','未获得最终采用方案对应的工程拓扑/稳定方案边界。',GAP_AFFECTED_SECTIONS['adopted_topology_or_scheme'])
    if level in ('equipment','unit','plant') and not (tower or reactor or equip):
        gap('EA/PA','equipment_result','未获得设备对象或设备专业结果。',GAP_AFFECTED_SECTIONS['equipment_result'])
    if not diagnosis: gap('PA/U','diagnosis_result','缺少诊断/瓶颈事实，项目必要性与方案形成依据不完整。',GAP_AFFECTED_SECTIONS['diagnosis_result'])
    gap('U','utility_system_capacity/current_load','公用工程供需平衡需要企业系统能力、现状负荷和余量。',GAP_AFFECTED_SECTIONS['utility_system_capacity/current_load'])
    gap('U/PA','energy_consumption_physical_quantities_and_boundary','节能计算需要明确核算边界及能源实物量；折标规则已内置（默认折标准煤，蒸汽折油标需提供表压/压力等级）。',GAP_AFFECTED_SECTIONS['energy_consumption_physical_quantities_and_boundary'])
    gap('U/R/C','general_layout_storage_pipe_network','总图、储运、外管等专业条件需项目资料或专业确认。',GAP_AFFECTED_SECTIONS['general_layout_storage_pipe_network'])
    gap('C/FA','investment_finance_rules','投资、融资、财务输入和规则未形成完整Contract。',GAP_AFFECTED_SECTIONS['investment_finance_rules'])

    pn=manifest.get('project_name') or manifest_project.get('project_name') or profile.get('project_name') or '待确认项目'
    pn_source=manifest.get('project_name_source') or manifest_project.get('project_name_source') or profile.get('project_name_source')
    if ui_project.get('project_name') and (not pn or pn=='待确认项目' or (pn_source or 'default') in ('workspace_dir','default','algorithm_output')):
        pn=ui_project['project_name']; pn_source='user_input'

    ui_energy=(user_inputs.get('energy') or {}) if isinstance(user_inputs,dict) and isinstance(user_inputs.get('energy'),dict) else {}
    accounting_standard,accounting_standard_source=resolve_conversion_type(ui_energy.get('accounting_standard'),profile.get('energy_accounting_standard'))

    facts={
        'meta':{'schema_version':'1.1','generated_at':datetime.now(timezone.utc).isoformat(),'scope':'feasibility_report_project_facts','adapter_mode':'generic_partial'},
        'project':{
            'project_id':manifest.get('project_id') or manifest_project.get('project_id') or profile.get('project_id') or 'PROJECT',
            'project_name':pn,
            'project_name_source':pn_source,
            'project_level':level,'project_type':ptype,
            'profile_provenance':profile.get('profile_provenance') or {},
            'construction_unit':construction_unit,'construction_unit_source':construction_unit_source,'project_location':project_location,
            'raw_material_route_changed':_route_changed(changes,'raw_material_route_changed',ptype),
            'process_route_changed':_route_changed(changes,'process_route_changed',ptype),
            'changes':changes,
            'target_component_codes':profile.get('target_component_codes') or []
        },
        'sources':_source_meta(manifest),
        'component_catalog':components,
        'process':{'design':design,'retrofit':retrofit},
        'diagnosis':diagnosis,
        'scheme_analysis':candidate_scheme or {'stage':'not_available','candidates':[],'algorithm_recommendation':None},
        'post_stage_recommendation':post,
        'equipment':{
            'object_catalog':equip.get('equipment') or [],
            'tower':{'version':(tower_src or {}).get('version') or 'current','conclusion':tower.get('conclusion'),'selected_scheme_set':t_selected,'report_rows':t_rows,'expected_new_equipment_ids':expected_new},
            'reactor':{'conclusion':reactor.get('conclusion'),'selected_scheme':r_selected,'selection_basis_source':reactor_selected_summary.get('selection_basis_source') if reactor_selected_summary else None,'solution_sets':r_solution_sets,'report_rows':r_rows,'utility_requirements':utilities}
        },
        'user':{'owner':construction_unit,'construction_unit':construction_unit,'annual_operating_hours':annual_hours,'annual_operating_hours_source':annual_hours_source,'utility_system_capacity':None,'project_location':project_location,'supply_conditions':None,'energy_accounting_media':ui_energy.get('accounting_media'),'energy_accounting_standard':accounting_standard},
        'enterprise':{'basic_profile':ui_enterprise.get('basic_profile'),'basic_profile_source':'user_input' if ui_enterprise.get('basic_profile') else None},
        'energy':{'accounting_media':ui_energy.get('accounting_media') or [],'accounting_standard':accounting_standard,'accounting_standard_source':accounting_standard_source,'consumption':ui_energy.get('consumption') or [],'conversion_rules':load_data(ENGINEERING_RULES_ROOT/'energy_conversion_rules.json'),'note':'默认折标准煤（GB/T 2589-2020）；折标准油（GB/T 50441-2016）仅在显式口径要求时启用。不从设备级公用工程线索自动推断能耗核算边界。'},
        'fa':{'energy_conversion':{'status':'not_calculated','tool':'energy_conversion_calculator'}},'gaps':gaps,'consistency_issues':issues,'section_coverage':[]
    }
    return facts
