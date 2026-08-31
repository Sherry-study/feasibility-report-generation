#!/usr/bin/env python3
"""Planning-stage chapter applicability decision from Project Profile."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

VALID_LEVELS={'equipment','unit','system','plant'}


def decide(data):
    p=data.get('project_profile',data) or {}; c=p.get('changes',{}) or {}; typ=p.get('project_type') or 'mixed'; level=p.get('project_level') or p.get('retrofit_scope') or 'unit'
    if level not in VALID_LEVELS: level='unit'
    out=[]
    def add(sec,status,reason,required_capabilities=None): out.append({'section':sec,'status':status,'reason':reason,'required_capabilities':required_capabilities or []})
    def get(k): return c.get(k)
    add('1.1','full','总论保留；项目专属事实不足时记录缺口。',['project_context'])
    market=typ=='capacity_expansion' or get('product_changed') is True or get('price_basis_changed') is True
    # Market chapter is driven by business boundary, not project level itself.
    add('2','full' if market else 'disabled','扩产、产品或价格评价边界变化时启用市场预测。',['market_evidence'] if market else [])
    cap=get('capacity_changed') is True or get('product_changed') is True or typ=='capacity_expansion'
    cap_status='full' if cap else ('conditional' if level=='equipment' else 'concise_pending_confirmation')
    add('3.1',cap_status,'产能/产品变化时详细编制；设备级项目无装置产能目标时按影响筛查。',['process_case_result'] if cap else [])
    for sec,key,label in [('4.1.1','raw_material_route_changed','原料路线'),('4.1.2','process_route_changed','工艺路线')]:
        v=get(key); status='concise' if v is False else ('full' if v is True else 'blocked')
        add(sec,status,f'{label}未变化则简写；发生变化则展开；未知时不猜测。')
    add('4.1.3','full','方案比较与选择为改造项目核心章节。',['scheme_candidate_result','adopted_topology'])
    add('4.1.4','full','最终采用方案的技术描述为核心章节。',['adopted_topology'])
    ev=get('equipment_changed')
    if level=='system' and ev is not True:
        eq_status='conditional'
    else:
        eq_status='full' if ev is True or level=='equipment' else ('concise_or_not_applicable' if ev is False else 'conditional')
    add('4.2.4',eq_status,'设备变化及项目层级共同决定设备表深度。',['equipment_result'])
    add('4.4','full' if get('control_system_changed') is True else 'conditional','自控变化时详细编制。')
    add('5.1','full' if get('material_consumption_changed') is not False and level!='equipment' else 'conditional','原辅料变化时详细编制；设备级按影响筛查。',['process_case_result'])
    utility_status='full' if level in ('system','plant') or get('utility_demand_changed') is True else ('conditional' if level=='equipment' else 'concise_pending_utility_check')
    add('5.4',utility_status,'动力需求按项目层级、项目事实和公用工程核查深度编制。',['utility_result'])
    for sec,key in [('7.1','layout_changed'),('7.2','storage_changed'),('7.3','outside_pipe_network_changed')]:
        v=get(key)
        if level=='equipment' and v is not True: status='conditional'
        else: status='full' if v is True else ('concise' if v is False else 'blocked')
        add(sec,status,'工程影响明确无变化可简写；设备级默认先做影响筛查；未知时保留缺口。')
    add('8.1','full' if level in ('system','plant') or get('utility_system_changed') is True else 'screening_required','公用工程章节区分需求量与系统供需平衡。',['utility_result'])
    add('10.1-10.6','full' if level in ('unit','system','plant') else 'conditional','节能章节保留；设备级按能耗影响筛查，计算深度由能源数据决定。',['energy_input'])
    add('18','full_or_blocked','章节保留；实施周期需工程条件支撑。')
    add('19','full_or_blocked','章节保留；缺工程量/价格/取费时不生成金额。',['economic_input'])
    add('20','full_or_blocked','章节保留；缺融资方案时不生成确定结论。',['economic_input'])
    add('21','full_or_blocked','章节保留；缺完整财务输入时不计算指标。',['economic_input'])
    add('26','summary_gate','只汇总受控事实和已明确的保留条件。')
    return {'contract_version':'1.1','project_id':p.get('project_id'),'project_level':level,'project_type':typ,'retrofit_scope':p.get('retrofit_scope'),'chapter_plan':out}
