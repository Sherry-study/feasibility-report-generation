#!/usr/bin/env python3
"""Build the seven report tables currently consumed by the 当前版本/当前版本 Word writer.
Uses only normalized Project Facts; no project-specific filenames are required.
"""
import argparse, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.common import load_data, dump_json
from internal.domain import confirmed_status_category, equipment_report_name, first_product_stream

def f(v,n=2): return '待补充' if v is None else f'{v:,.{n}f}'
def comp_map(s): return {x.get('component_code') or x.get('component'):x.get('mass_fraction') for x in s.get('composition',[])}

def _fmt_num(v):
    if v is None: return None
    try:
        fv=float(v)
        return str(int(fv)) if fv.is_integer() else f'{fv:g}'
    except Exception:
        return str(v)

def _tower_type_label(raw_type):
    t=str(raw_type or '').strip()
    if not t: return None
    u=t.upper()
    if u=='F1': return 'F1浮阀塔'
    if u in ('BX','PACKED','PACKING'): return '填料塔'
    if 'F1' in u: return f'{t}浮阀塔'
    if 'PACK' in u or '填料' in t: return '填料塔'
    return f'{t}型塔'

def _tower_spec_from_device_paras(dp):
    if not isinstance(dp,dict): return []
    parts=[]
    tray_type=dp.get('tray_type')
    type_label=_tower_type_label(tray_type)
    if type_label: parts.append(type_label)
    si=dp.get('struct_info') or []
    pairs=[x for x in si if isinstance(x,(list,tuple)) and len(x)>=2]
    if pairs:
        ds=[_fmt_num(x[0]) for x in pairs if x[0] is not None]
        if ds: parts.append('直径'+'/'.join(ds)+' mm')
        seconds=[_fmt_num(x[1]) for x in pairs if x[1] is not None]
        if seconds:
            if str(tray_type or '').upper() in ('BX','PACKED','PACKING'):
                parts.append('填料高度'+'/'.join(seconds)+' mm')
            else:
                parts.append('板间距'+'/'.join(seconds)+' mm')
    if dp.get('tray_num') is not None: parts.append(f"理论/计算级数{_fmt_num(dp.get('tray_num'))}")
    return parts

def _tower_spec_from_separator_limit(limit):
    if not isinstance(limit,dict): return []
    parts=[]
    sections=limit.get('hydraulic_sections') or []
    types=[str(x.get('type') or '') for x in sections if isinstance(x,dict)]
    type_label=_tower_type_label(types[0] if types else None)
    if type_label: parts.append(type_label)
    ds=[]; seconds=[]
    for x in sections:
        if not isinstance(x,dict): continue
        if x.get('D') is not None: ds.append(_fmt_num(float(x['D'])*1000))
        # HT in original EA output is tray spacing in m. Packed sections generally have no HT.
        if x.get('HT') is not None: seconds.append(_fmt_num(float(x['HT'])*1000))
    if ds: parts.append('直径'+'/'.join(ds)+' mm')
    if seconds: parts.append('板间距'+'/'.join(seconds)+' mm')
    if limit.get('N_trays') is not None: parts.append(f"理论/计算级数{_fmt_num(limit.get('N_trays'))}")
    return parts

def tower_spec(r):
    """Format report-facing tower specs without discarding EA fields.

    Priority:
    1) Explicit normalized device_paras on a newly objectized tower row;
    2) selected retrofit device_paras in the original EA object;
    3) existing separator_limit/hydraulic section data for retained base towers.
    """
    d=r.get('detail') or {}
    if isinstance(d,dict) and ('retrofit' in d or 'separator_limit' in d or 'id' in d):
        obj=d
    else:
        obj=d.get('detail') if isinstance(d,dict) and isinstance(d.get('detail'),dict) else d
    retrofit=(obj.get('retrofit') or {}) if isinstance(obj,dict) else {}
    dp=(r.get('device_paras') if isinstance(r.get('device_paras'),dict) else None)
    if dp is None:
        dp=(retrofit.get('plan_detail') or {}).get('device_paras') if isinstance(retrofit,dict) else None
    if dp is None and isinstance(obj,dict):
        dp=obj.get('device_paras')

    # For retained base towers, report the existing equipment geometry rather than
    # accidentally showing a newly designed parallel/series tower's geometry.
    if r.get('action_group') in ('parallel_base','series_base'):
        limit=(obj.get('detail') or {}).get('separator_limit') if isinstance(obj,dict) and isinstance(obj.get('detail'),dict) else (obj.get('separator_limit') if isinstance(obj,dict) else None)
        parts=_tower_spec_from_separator_limit(limit)
        return '；'.join(parts) or '现有主要结构参数待设备资料补充'

    parts=_tower_spec_from_device_paras(dp)
    if not parts and isinstance(obj,dict):
        limit=(obj.get('detail') or {}).get('separator_limit') if isinstance(obj.get('detail'),dict) else obj.get('separator_limit')
        parts=_tower_spec_from_separator_limit(limit)
    return '；'.join(parts) or '设备主要规格待设备专业结果补充'

def reactor_spec(p,form=None):
    p=p or {}; out=[]
    if form: out.append(str(form))
    if p.get('diameter') is not None: out.append(f"直径{_fmt_num(p['diameter'])} mm")
    if p.get('cylinder_height') is not None: out.append(f"筒体高{_fmt_num(p['cylinder_height'])} mm")
    if p.get('total_volume') is not None: out.append(f"总容积{float(p['total_volume']):.2f} m³")
    if p.get('catalyst_volume') is not None: out.append(f"有效体积{float(p['catalyst_volume']):.2f} m³")
    if p.get('heat_exchange_area') is not None: out.append(f"换热面积{_fmt_num(p['heat_exchange_area'])} m²")
    return '；'.join(out) or '待补充'


def _confirmed_description(row, default=None):
    return row.get('confirmed_retrofit_description') or default

def equipment_rows(facts):
    """Build only reportable equipment engineering actions for 4.2.4.

    Formal categories are intentionally narrow:
    - 新增: a new equipment object is added by the project;
    - 改造: an existing equipment body/specification is physically modified;
    - 利旧: an existing equipment object is reassigned to a new engineering use/role.
    Existing equipment that continues its original duty with no body change (including
    other / optimal / parallel_base / series_base / no_retrofit) is not listed in the
    three formal tables. Project Facts and confirmation can still retain those states.
    """
    out={'新增':[],'利旧':[],'改造':[]}
    for r in facts.get('equipment',{}).get('tower',{}).get('report_rows',[]):
        cat=confirmed_status_category(r.get('confirmed_retrofit_status'),r.get('report_category','无变化')); ag=r.get('action_group'); note=''
        name=r.get('confirmed_name') or r.get('name')
        if ag=='parallel_new': note=f"与{r.get('based_on','原')}塔并联"
        elif ag=='series_new': note=f"与{r.get('based_on','原')}塔串联"
        note=_confirmed_description(r,note)
        if cat not in out:
            continue
        out[cat].append({'type':'塔类','tag':r.get('confirmed_tag') or ('待定' if cat=='新增' else str(r.get('equipment_id') or '待定')),'name':equipment_report_name(name),'spec':tower_spec(r),'qty':1,'content':'-','note':note})
    for r in facts.get('equipment',{}).get('reactor',{}).get('report_rows',[]):
        ag=r.get('action_group'); p=r.get('selected_parameters') or {}; tag=r.get('confirmed_tag') or r.get('tag') or '待定'; name=r.get('confirmed_name') or r.get('name'); form=r.get('form')
        confirmed_cat=confirmed_status_category(r.get('confirmed_retrofit_status'),r.get('report_category'))
        confirmed_desc=_confirmed_description(r,r.get('scheme_description'))
        if ag=='reuse' and isinstance(name,str) and name.startswith('新'):
            name=name[1:]+'（利旧设备）'
        if ag=='parallel':
            # The original reactor remains on its original duty; only the added parallel
            # reactor is a reportable engineering object.
            total=int(p.get('quantity') or 2); current=1; add=max(total-current,1)
            out['新增'].append({'type':'反应器类','tag':'待定','name':equipment_report_name(f'{name}_并联'),'spec':reactor_spec(p,form),'qty':add,'content':'—','note':confirmed_desc or '新增并联设备'})
        elif ag=='addition' or confirmed_cat=='改造':
            out['改造'].append({'type':'反应器类','tag':tag,'name':name,'spec':reactor_spec(p,form),'qty':1,'content':confirmed_desc or '设备改造','note':r.get('conclusion') or ''})
        elif ag=='reuse' or confirmed_cat=='利旧':
            out['利旧'].append({'type':'反应器类','tag':tag,'name':name,'spec':reactor_spec(p,form),'qty':1,'content':confirmed_desc or '利用已有设备承担新的工程用途','note':'利旧用于新的工程用途/角色'})
    return out

def ext_streams(facts,case):
    rows=[]
    for s in facts.get('process',{}).get(case,{}).get('streams',[]):
        if not s.get('source_equipment_id') or not s.get('target_equipment_id'):
            rows.append(s)
    return rows

def build(facts):
    t={}; dp=first_product_stream(facts,'design'); rp=first_product_stream(facts,'retrofit')
    annual_hours=facts.get('user',{}).get('annual_operating_hours')
    fa_cap=facts.get('fa',{}).get('annual_capacity',{}) or {}
    dp_name=(dp or {}).get('name') or ''
    rp_name=(rp or {}).get('name') or ''
    pname=dp_name or rp_name
    rows31=[
      [f'{pname}小时产量' if pname else '产品小时产量','kg/h',f((dp or {}).get('flow_kg_h')),f((rp or {}).get('flow_kg_h')),'工艺计算结果'],
      [f'{pname}年生产规模' if pname else '年生产规模','t/a',f(fa_cap.get('design_t_a')),f(fa_cap.get('retrofit_t_a')),f'按年运行{annual_hours} h折算' if annual_hours is not None else '缺年运行时间']
    ]
    # Optional target-component metric is driven by Project Facts Contract, never by hardcoded chemistry.
    target_codes=((facts.get('project') or {}).get('target_component_codes') or [])
    if target_codes:
        dcm=comp_map(dp or {}); rcm=comp_map(rp or {})
        q=lambda cm:100*sum((cm.get(k,0) or 0) for k in target_codes)
        rows31.append(['目标关键组分合计质量分数','%',f(q(dcm),4),f(q(rcm),4),'按project.target_component_codes计算'])
    t['3.1-1']={'title':'改造前后生产规模及产品指标对比表','columns':['项目','单位','设计/基准','改造后','说明'],'rows':rows31}

    # 4.1-2 Key-equipment alternatives. Prefer an equipment-group solution set when
    # the EA result provides multiple integrated routes; otherwise use individual
    # equipment with >1 alternatives. Unsupported economy/period judgements stay gaps.
    solution_sets=((facts.get('equipment') or {}).get('reactor') or {}).get('solution_sets') or []
    chosen_sets=[x for x in solution_sets if x.get('scope_level')=='equipment_group' and len(x.get('alternatives') or [])>1]
    if not chosen_sets:
        chosen_sets=[x for x in solution_sets if x.get('scope_level')=='equipment' and len(x.get('alternatives') or [])>1]
    key_rows=[]
    for ss in chosen_sets:
        target=ss.get('display_name') or ss.get('equipment_tag') or ss.get('equipment_id') or '关键设备'
        for alt in ss.get('alternatives') or []:
            route=alt.get('route_summary') or alt.get('name') or '待补充'
            vol=alt.get('total_new_equipment_volume_m3')
            if vol is not None and f'{float(vol):.2f}' not in str(route):
                route=f'{route}；新增有效体积约{float(vol):.2f} m³'
            if alt.get('selection_status')=='recommended':
                judgement='选定' if alt.get('selection_basis_source')=='user_selected' else '推荐'
            else:
                judgement='备选'
            key_rows.append([target,route,judgement])
    if key_rows:
        t['4.1-2']={
            'title':'关键设备方案比较表',
            'columns':['设备位号/名称','候选路线','推荐结论'],
            'rows':key_rows
        }
    d={x.get('stream_id'):x for x in ext_streams(facts,'design')}; r={x.get('stream_id'):x for x in ext_streams(facts,'retrofit')}; ids=list(dict.fromkeys(list(d)+list(r)))
    t['4.2-1']={'title':'改造前后物料平衡摘要表（外部流股）','columns':['流股ID','名称','方向','单位','改造前','改造后','备注'],'rows':[[sid,(r.get(sid) or d.get(sid) or {}).get('name'), '输入' if not (r.get(sid) or d.get(sid)).get('source_equipment_id') else '输出','kg/h',f((d.get(sid) or {}).get('flow_kg_h')),f((r.get(sid) or {}).get('flow_kg_h')),'产品' if (r.get(sid) or d.get(sid)).get('target_product_stream') else ''] for sid in ids]}
    # 表4.2-2 主要消耗定额及改造前后对比表：主要原料取算法外部进料定义（external_feeds）；
    # 未列入进料、组成近纯水的输入流股为装置内洗涤水循环回用，按循环量合并列示；蒸汽/电力等公用工程实物量无全厂口径数据，如实待补充
    fd={x.get('stream_id'):x for x in facts.get('process',{}).get('design',{}).get('external_feeds',[])}
    fr={x.get('stream_id'):x for x in facts.get('process',{}).get('retrofit',{}).get('external_feeds',[])}
    rows_42=[]
    for sid in list(dict.fromkeys(list(fd)+list(fr))):
        dv=(fd.get(sid) or {}).get('flow_kg_h'); rv=(fr.get(sid) or {}).get('flow_kg_h')
        chg=''
        if dv is not None and rv is not None and float(dv)>0: chg=f'{(float(rv)-float(dv))/float(dv)*100:+.1f}%'
        elif dv is None and rv is not None: chg='新增'
        rows_42.append([(fr.get(sid) or fd.get(sid) or {}).get('name') or sid,'kg/h',f(dv),f(rv),chg,'主要原料'])
    wash_d=wash_r=0.0
    for sid in ids:
        if sid in fd or sid in fr: continue
        st=r.get(sid) or d.get(sid) or {}
        if st.get('source_equipment_id'): continue
        comp=st.get('composition') or []
        dom=max(comp,key=lambda c:c.get('mass_fraction') or 0) if comp else {}
        if (dom.get('mass_fraction') or 0)>=0.99 and dom.get('component_name')=='水':
            wash_d+=float((d.get(sid) or {}).get('flow_kg_h') or 0); wash_r+=float((r.get(sid) or {}).get('flow_kg_h') or 0)
    if wash_d or wash_r:
        rows_42.append(['洗涤水（循环回用）','kg/h',f(wash_d) if wash_d else '待补充',f(wash_r) if wash_r else '待补充','-','装置内水循环回用，非新鲜水消耗'])
    for nm,unit in [('蒸汽','t/h'),('电力','kW'),('循环水','t/h'),('氮气/仪表空气','Nm3/h')]:
        rows_42.append([nm,unit,'待补充','待补充','待补充','公用工程实物量待专业汇总'])
    if rows_42:
        t['4.2-2']={'title':'主要消耗定额及改造前后对比表','columns':['项目','单位','改造前','改造后','变化量/变化率','说明'],'rows':rows_42}
    eq=equipment_rows(facts)
    for cat,key,title in [('新增','4.2-3','新增工艺设备汇总表'),('利旧','4.2-4','利旧工艺设备汇总表'),('改造','4.2-5','改造工艺设备汇总表')]:
        rows=[]
        for i,x in enumerate(eq[cat],1):
            rows.append([i,x['type'],x['tag'],x['name'],x['content'],x['spec'],x['qty'],x['note']] if cat=='改造' else [i,x['type'],x['tag'],x['name'],x['spec'],x['qty'],x['note']])
        cols=['序号','设备类别','设备位号','设备名称','主要改造内容','改造后主要规格/设计参数','数量','备注'] if cat=='改造' else ['序号','设备类别','设备位号','设备名称','主要规格/设计参数','数量','备注']
        if rows:
            t[key]={'title':title,'columns':cols,'rows':rows}
    d={x.get('stream_id'):x for x in facts.get('process',{}).get('design',{}).get('external_feeds',[])}; r={x.get('stream_id'):x for x in facts.get('process',{}).get('retrofit',{}).get('external_feeds',[])}; ids=list(dict.fromkeys(list(d)+list(r)))
    annual_map={str(x.get('stream_id')):x for x in (facts.get('fa',{}).get('annual_material_consumption',{}) or {}).get('items',[]) or []}
    t['5.1-1']={'title':'主要原辅料需求表（当前可用字段）','columns':['名称','规格/质量要求','单位','改造前小时用量','改造后小时用量','改造后年需用量','来源/供应方式','备注'],'rows':[[ (r.get(sid) or d.get(sid) or {}).get('name'),'待补充','kg/h',f((d.get(sid) or {}).get('flow_kg_h')),f((r.get(sid) or {}).get('flow_kg_h')), (f(annual_map.get(str(sid),{}).get('annual_t_a'))+' t/a') if annual_map.get(str(sid),{}).get('annual_t_a') is not None else '待补充','待补充',f'按年运行{annual_hours} h折算' if annual_hours is not None else '年用量待年运行时间'] for sid in ids]}
    rows=[]
    for u in facts.get('equipment',{}).get('reactor',{}).get('utility_requirements',[]): rows.append([u.get('medium'),f"{u.get('inlet_temperature_c')}→{u.get('outlet_temperature_c')}℃",'kg/h','—',f(u.get('mass_flow_kg_h')),u.get('equipment_name'),'设备专业计算结果'])
    # Tower heat loads can be extracted from normalized row detail.
    for rr in facts.get('equipment',{}).get('tower',{}).get('report_rows',[]):
        d=rr.get('detail') or {}; obj=d.get('detail') if isinstance(d,dict) and isinstance(d.get('detail'),dict) else d; op=(obj.get('optimal') or {}) if isinstance(obj,dict) else {}
        if op.get('QB_kW') is not None: rows.append(['再沸热负荷','—','kW','—',f(op.get('QB_kW')),rr.get('name'),'非蒸汽耗量'])
    t['5.4-1']={'title':'水、电、汽和其他动力需求线索表（部分）','columns':['介质/负荷','参数/等级','单位','改造前','改造后/需求','对象','说明'],'rows':rows}
    return t

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--facts',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    t=build(load_data(a.facts)); dump_json(t,a.output); print(json.dumps({'output':a.output,'tables':list(t)},ensure_ascii=False))
if __name__=='__main__': main()
