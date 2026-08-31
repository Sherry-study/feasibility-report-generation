from __future__ import annotations
import re

from internal.domain import (
    equipment_display_name, is_disabled_status, plan_status_map, normalize_media_name,
    energy_rule_index, energy_steam_block,
)


def P(text,indent=True): return {'type':'paragraph','text':text,'indent':indent}
def H(text,level): return {'type':'heading','text':text,'level':level}
def NL(items): return {'type':'numbered_list','items':items}
def T(spec,caption,table_id): return {'type':'table','columns':spec.get('columns',[]),'rows':spec.get('rows',[]),'caption':caption,'table_id':table_id}

def norm_label(item):
    s=f"《{item.get('name','')}》"; code=item.get('code') or item.get('document_no')
    if code:
        code=re.sub(r'（?参考案例标注[^）]*）?','',str(code)).strip().rstrip('（').strip()
        if code: s+=f'（{code}）'
    return s+'；'

def draft_map(drafts): return {str(x.get('section_id')):x for x in (drafts or {}).get('drafts',[])}
def add_draft_blocks(blocks,draft,current_heading=None):
    if not draft or draft.get('draft_status') not in ('draft','partial'): return False
    wrote=False
    for sub in draft.get('subsections') or []:
        hd=str(sub.get('heading') or '').strip()
        if hd and hd!=current_heading:
            m=re.match(r'^(\d+(?:\.\d+)*)\s+',hd); level=min((m.group(1).count('.')+1 if m else 2),4); blocks.append(H(hd,level))
        for para in sub.get('paragraphs') or []:
            txt=str(para).strip()
            if txt: blocks.append(P(txt)); wrote=True
    return wrote

def scheme_display(adopted):
    """采用方案的业务化表述。

    候选池可保留“方案A/方案B”等比较编号，但推荐结论及其他章节优先显示
    方案的工程名称，避免把内部候选编号带入正式正文。
    """
    nm=str((adopted or {}).get('scheme_name') or '').strip()
    patterns=[
        r'^方案\s*[A-Za-z0-9一二三四五六七八九十]+\s*[（(]\s*(?:新增设备\s*[:：]\s*)?(.+?)\s*[）)]$',
        r'^方案\s*[A-Za-z0-9一二三四五六七八九十]+\s*[:：]\s*(.+)$',
    ]
    for pattern in patterns:
        m=re.match(pattern,nm)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return nm or '采用方案'


def _same_scheme_identity(candidate, adopted):
    cid=candidate.get('scheme_id'); aid=adopted.get('scheme_id')
    if cid not in (None,'') and aid not in (None,''):
        return str(cid)==str(aid)
    cname=str(candidate.get('name') or '').strip(); aname=str(adopted.get('scheme_name') or '').strip()
    return bool(cname and aname and cname==aname)

def candidate_table(facts):
    """Compare the complete first recommendation pool.

    Later optimization does not add a report candidate. For the finally adopted scheme,
    if a stable identity match is available, its report-facing description is replaced
    with the final topology-based engineering description; other candidates retain the
    first-pool description. Identity is never guessed from similarity.
    """
    rows=[]; adopted=facts.get('adopted_scheme') or {}
    for c in (facts.get('scheme_analysis') or {}).get('candidates') or []:
        ev=c.get('evaluation') or {}
        def lvl(k): return ((ev.get(k) or {}).get('level')) or '—'
        brief=c.get('brief') or c.get('description')
        if _same_scheme_identity(c,adopted) and adopted.get('scheme_description'):
            brief=adopted.get('scheme_description')
        rows.append([c.get('name'),c.get('retrofit_type_label') or c.get('retrofit_type'),brief,lvl('technical_feasibility'),lvl('implementation_complexity'),lvl('operational_risk')])
    return {'columns':['方案','改造类型','方案概要','技术可行性','实施复杂度','运行风险'],'rows':rows}

def investment_scope_table(facts):
    tables=[]
    eq=facts.get('equipment') or {}
    for kind,label in [('tower','塔器'),('reactor','反应器')]:
        for r in ((eq.get(kind) or {}).get('report_rows') or []):
            cat=r.get('report_category'); ag=r.get('action_group')
            if cat=='新增' or cat=='改造' or ag in ('parallel','addition'):
                name=r.get('name') or r.get('tag') or r.get('equipment_id')
                action='新增' if cat=='新增' else ('改造' if cat=='改造' else ('新增并联设备' if ag=='parallel' else '改造'))
                tables.append([label,name,action,r.get('scheme_description') or r.get('conclusion') or '按设备专业结果实施'])
    # Deduplicate by kind+name+action
    seen=set(); rows=[]
    for x in tables:
        key=tuple(x[:3])
        if key not in seen: seen.add(key); rows.append(x)
    return {'columns':['对象类别','对象','投资边界','说明'],'rows':rows}

def equipment_summary_paragraphs(facts):
    """把设备动作归并为可研正文中的“改造策略”描述，而不是设备状态清单。"""
    confirmed=((facts.get('confirmed_baseline') or {}).get('equipment_summary') or [])
    if confirmed:
        changed=[]
        for x in confirmed:
            status=str(x.get('retrofit_status') or '')
            if status in ('当前未识别改造要求','不改造','无变化','无变化（运行参数优化）'):
                continue
            label=' '.join([str(x.get('tag') or '').strip(),str(x.get('name') or '').strip()]).strip() or str(x.get('equipment_id') or '设备')
            kind=str(x.get('equipment_type') or '')
            changed.append((kind,label,status))
        if changed:
            action_map={
                '无变化（配套新增并联设备）':'采用并联扩能',
                '无变化（配套新增串联设备）':'采用串联扩能',
                '新增':'新增',
                '改造':'实施本体改造',
                '利旧':'利旧现有设备',
            }
            kind_name={'tower':'塔器','reactor':'反应器'}
            kind_items={}
            for kind,label,status in changed:
                action=action_map.get(status,'实施相应改造')
                kind_items.setdefault(kind,[]).append(f'{label}{action}')
            parts=[]
            for kind,items in kind_items.items():
                parts.append(f"{kind_name.get(kind,'相关设备')}方面，"+'，'.join(items))
            return ['；'.join(parts)+'。具体设备规格、数量和改造内容见本报告第4.2.4节。']

    eq=facts.get('equipment') or {}; out=[]
    towers=((eq.get('tower') or {}).get('report_rows') or [])
    parallel=[equipment_display_name(r.get('name')) for r in towers if r.get('action_group')=='parallel_base']
    series=[equipment_display_name(r.get('name')) for r in towers if r.get('action_group')=='series_base']
    tparts=[]
    if parallel: tparts.append('、'.join(parallel)+'采用并联扩能')
    if series: tparts.append('、'.join(series)+'采用串联扩能')
    if tparts: out.append('塔器方面，'+'；'.join(tparts)+'。')
    reactors=((eq.get('reactor') or {}).get('report_rows') or [])
    rparts=[]
    add=[equipment_display_name(r.get('name') or r.get('tag')) for r in reactors if r.get('action_group')=='addition']
    par=[equipment_display_name(r.get('name') or r.get('tag')) for r in reactors if r.get('action_group')=='parallel']
    reuse=[equipment_display_name(r.get('name') or r.get('tag')) for r in reactors if r.get('action_group')=='reuse']
    if add: rparts.append('、'.join(add)+'实施扩容或本体改造')
    if par: rparts.append('、'.join(par)+'采用并联扩能')
    if reuse: rparts.append('、'.join(reuse)+'利旧现有设备')
    if rparts: out.append('反应器方面，'+'；'.join(rparts)+'。')
    if out:
        out.append('具体设备规格、数量和改造内容见本报告第4.2.4节。')
    return out


def _unit_operation(unit):
    """根据已知设备类型/名称提炼工艺操作，不补造具体反应机理。"""
    name=str((unit or {}).get('name') or '')
    typ=str((unit or {}).get('type') or '').lower()
    if '预处理' in name:
        return '预处理'
    if '水洗' in name or '洗涤' in name:
        return '洗涤'
    if '回收' in name:
        return '回收'
    if '精制' in name:
        return '精制'
    if '反应器' in name or 'reactor' in typ:
        return '反应'
    if '蒸馏' in name or '分离' in name or '塔' in name or typ in ('separator','tower','column','distillation'):
        return '分离'
    return '处理'


def _group_process_units(units):
    groups=[]
    for unit in units or []:
        name=equipment_display_name(unit.get('name'))
        if not name:
            continue
        op=_unit_operation(unit)
        if groups and groups[-1]['operation']==op:
            groups[-1]['names'].append(name)
        else:
            groups.append({'operation':op,'names':[name]})
    return groups


def _operation_clause(operation, names, first=False):
    """生成单个工艺段核心短语，转折词由段落组装统一处理。"""
    if operation=='预处理':
        return f'经过{names}'
    if operation=='反应':
        return f'进入{names}进行反应转化'
    if operation=='洗涤':
        return f'经{names}洗涤分离'
    if operation=='回收':
        return f'进入{names}进行物料回收'
    if operation=='精制':
        return f'送至{names}进一步精制'
    if operation=='分离':
        return f'进入{names}进行分离'
    return f'经过{names}处理'


def topology_paragraphs(analysis, facts=None):
    """把拓扑关系转换为按物料流向组织的工艺流程说明。

    仍只消费既有拓扑，不增加LLM步骤；区别仅在于不再把设备节点机械串成一条长句。
    """
    if not analysis or analysis.get('status') not in ('analyzed','partial'):
        return []
    feed=(analysis.get('main_feed') or {}).get('name') or '原料'
    product=(analysis.get('target_product') or {}).get('name') or '目标产品'
    units=[x for x in ((analysis.get('main_path') or {}).get('process_units') or []) if x.get('name')]
    groups=_group_process_units(units)
    paras=[]

    # 每3个工艺段组织为一个自然段，既保留流向，又避免“一设备一句”的机械感。
    clauses=[]
    for idx,group in enumerate(groups):
        names='、'.join(group['names'])
        clauses.append(_operation_clause(group['operation'],names,first=(idx==0)))
    for offset in range(0,len(clauses),3):
        chunk=clauses[offset:offset+3]
        if not chunk:
            continue
        linked=[]
        for local_idx,clause in enumerate(chunk):
            if local_idx==0:
                linked.append(clause)
            else:
                linked.append('随后'+clause)
        prefix=f'{feed}进入装置后，先' if offset==0 else '完成前述处理后，物料继续'
        text=prefix+'；'.join(linked)
        if offset+3>=len(clauses):
            text+=f'，最终得到{product}'
        paras.append(text+'。')

    secondary=[x for x in (analysis.get('secondary_process_units') or []) if x.get('name')]
    reaction_branch=[equipment_display_name(x.get('name')) for x in secondary if _unit_operation(x)=='反应']
    recovery_branch=[equipment_display_name(x.get('name')) for x in secondary if _unit_operation(x) in ('洗涤','回收')]
    other_branch=[equipment_display_name(x.get('name')) for x in secondary if _unit_operation(x) not in ('反应','洗涤','回收')]
    branch_parts=[]
    if reaction_branch:
        branch_parts.append('增设'+ '、'.join(reaction_branch) +'强化反应过程')
    if recovery_branch:
        branch_parts.append('设置'+ '、'.join(recovery_branch) +'完成洗涤和物料回收')
    if other_branch:
        branch_parts.append('设置'+ '、'.join(other_branch) +'承担辅助处理')
    if branch_parts:
        paras.append('除主流程外，装置还根据工艺需要'+'；'.join(branch_parts)+'。相关物流处理后返回主流程或送入相应下游单元。')

    rc=analysis.get('recycle_connections') or []
    recycle_text=[]
    for x in rc:
        a=equipment_display_name(x.get('from')); b=equipment_display_name(x.get('to'))
        if a and b and '分离器' not in a and '混合器' not in b:
            recycle_text.append(f'{a}处理后的部分物流返回{b}')
    if recycle_text:
        paras.append('装置设置必要的循环回用流程，主要包括'+'；'.join(recycle_text[:4])+'，用于回收利用可返回系统的物料。')

    ch=analysis.get('retrofit_changes') or {}
    newnodes=[n for n in (ch.get('new_nodes') or []) if n.get('name') and str(n.get('type') or '').lower() not in {'tee','mixer'}]
    if newnodes:
        names=[equipment_display_name(n.get('name')) for n in newnodes if n.get('name')]
        text='本次改造对流程的主要调整为新增'+ '、'.join(names) +'，并相应调整其上下游连接管线'
        if ((facts or {}).get('project') or {}).get('process_route_changed') is False:
            text+='；除上述局部调整外，装置原有主体工艺路线保持不变'
        paras.append(text+'。')
    elif ((facts or {}).get('project') or {}).get('process_route_changed') is False:
        paras.append('本次改造不改变装置主体工艺路线，主要通过既有工艺环节的局部强化和设备能力调整实现改造目标。')
    return paras


def build_report_model(profile,facts,tables,topology_analysis,standards,chapter_plan=None,drafts=None):
    p=profile.get('project_profile',profile); project_name=p.get('project_name') or (facts.get('project') or {}).get('project_name') or '改造项目'
    owner=(facts.get('project') or {}).get('construction_unit') or '待明确'
    project_meta=facts.get('project') or {}
    project_level=project_meta.get('project_level') or p.get('project_level') or 'unit'
    nature_by_level={
        'equipment':'现有设备技术改造',
        'unit':'现有装置技术改造',
        'system':'现有系统技术改造',
        'plant':'现有全厂生产系统技术改造',
    }
    project_nature=nature_by_level.get(project_level,'现有生产系统技术改造')
    annual_hours=(facts.get('user') or {}).get('annual_operating_hours')
    adopted=facts.get('adopted_scheme') or {}; adopted_available=adopted.get('status')=='available'
    dmap=draft_map(drafts)
    sections=[]
    def S(heading,level=1):
        sec={'heading':heading,'level':level,'blocks':[]}; sections.append(sec); return sec['blocks']

    # 1
    b=S('1 总论'); b+= [H('1.1 概述',2),H('1.1.1 项目名称、建设单位及项目性质',3),P(f'项目名称：{project_name}。',False),P(f'建设单位：{owner}。',False),P(f'项目性质：{project_nature}。',False)]
    b.append(H('1.1.2 主办单位基本情况',3))
    if not add_draft_blocks(b,dmap.get('1.1.2'),'1.1.2 主办单位基本情况'): b.append(P('主办单位基本情况依据建设单位正式资料及可核验公开信息编制。'))
    b.append(H('1.1.3 项目提出的背景、投资的目的、意义和必要性',3))
    if not add_draft_blocks(b,dmap.get('1.1.3'),'1.1.3 项目提出的背景、投资的目的、意义和必要性'):
        diag=facts.get('diagnosis') or {}; ys=diag.get('yield_summary') or {}; desc='现有生产对象存在制约项目目标实现的工艺或设备问题。'
        if ys.get('design_total_yield_pct') is not None and ys.get('operating_total_yield_pct') is not None: desc+=f"运行总收率约{ys['operating_total_yield_pct']:.2f}%，低于设计水平{ys['design_total_yield_pct']:.2f}%。"
        b.append(P(desc+'本项目通过工艺方案优化和关键设备改造，改善项目目标相关的处理能力、运行效率或产品质量。'))
    b.append(H('1.1.4 编制依据和原则',3))
    rc=standards.get('report_compilation') or {}
    b.append(NL(list(rc.get('basis') or [])+list(rc.get('principles') or [])))
    b.append(H('1.1.5 研究范围',3)); b.append(P('研究范围包括生产规模和产品方案、工艺技术方案、主要设备、原辅料与动力、公用工程、节能、项目实施计划、投资估算、财务分析及研究结论。各专业深度以本项目已取得的设计条件和专业计算成果为边界。'))
    b.append(H('1.2 研究结论',2))
    if not add_draft_blocks(b,dmap.get('1.2'),'1.2 研究结论'):
        if adopted_available:
            b.append(P(f"本项目已形成明确的改造方案，推荐采用{scheme_display(adopted)}。现阶段主要工艺及设备改造范围已经明确，可据此开展后续工程设计和技术经济评价。"))
        else:
            b.append(P('本项目主要问题和改造目标已经明确，推荐技术方案尚需结合专业校核结果进一步确定。'))

    # 2
    b=S('2 市场预测分析')
    if not add_draft_blocks(b,dmap.get('2')): b.append(P('市场预测应结合产品用途、供需格局、竞争情况及价格趋势进行分析，并以可核验市场资料为依据。'))

    # 3
    b=S('3 生产规模和产品方案'); b.append(H('3.1 生产规模和产品方案',2))
    scale_text='本项目生产规模按改造后工艺计算结果确定，改造前后生产规模及产品指标见表3.1-1。产品方案原则上保持现有产品定位，通过本次工艺与设备改造提升装置处理能力和产品产出能力。'
    if annual_hours is None:
        scale_text+='年生产规模在年运行时长确定后按统一口径折算。'
    b.append(P(scale_text))
    if '3.1-1' in tables: b.append(T(tables['3.1-1'],'表 3.1-1 改造前后生产规模及产品指标对比表','3.1-1'))

    # 4
    b=S('4 工艺技术方案'); b.append(H('4.1 工艺技术方案的选择',2)); b.append(H('4.1.1 原料路线确定的原则和依据',3))
    if not add_draft_blocks(b,dmap.get('4.1.1'),'4.1.1 原料路线确定的原则和依据'):
        if (facts.get('project') or {}).get('raw_material_route_changed') is False: b.append(P('本项目不涉及原料路线调整，沿用现有原料路线。'))
        else: b.append(P('原料路线按照供应可靠性、质量适配性和工艺适用性进行比较确定。'))
    b.append(H('4.1.2 国内、外工艺技术概况',3))
    if not add_draft_blocks(b,dmap.get('4.1.2'),'4.1.2 国内、外工艺技术概况'):
        if (facts.get('project') or {}).get('process_route_changed') is False: b.append(P('本项目不涉及工艺路线调整，沿用现有工艺路线。'))
        else: b.append(P('工艺技术路线按照成熟度、工业化应用和本项目适用性进行分析。'))
    b.append(H('4.1.3 工艺技术方案的比较和选择',3))
    b.append(H('4.1.3.1 候选改造方案概述',4))
    candidates=((facts.get('scheme_analysis') or {}).get('candidates') or [])
    retrofit_types=[]
    for c in candidates:
        label=c.get('retrofit_type_label') or c.get('retrofit_type')
        if label and label not in retrofit_types:
            retrofit_types.append(str(label))
    overview='围绕现有生产系统已识别的主要瓶颈和本项目改造目标，对候选改造方案的技术思路、改造范围及工程实施影响进行比较。'
    if retrofit_types:
        overview+=f"候选方案涵盖{'、'.join(retrofit_types)}等改造路径，各方案从不同角度改善现有工艺环节的处理能力或运行效果。"
    overview+='具体方案及主要差异见下节比选。'
    b.append(P(overview))

    b.append(H('4.1.3.2 技术、消耗、设备、投资与风险比较',4))
    wrote_compare=add_draft_blocks(b,dmap.get('4.1.3.2'),'4.1.3.2 技术、消耗、设备、投资与风险比较')
    if not wrote_compare:
        b.append(P('方案比选重点考虑技术效果、改造范围、现有设备利用条件、实施难度及运行影响。对于当前尚缺乏可靠依据的投资和运行费用，不作推测性比较，待后续技经工作中进一步核实。'))
    ct=candidate_table(facts)
    if ct['rows']:
        b.append({'type':'table','columns':ct['columns'],'rows':ct['rows'],'caption':'表 4.1-1 候选工艺技术方案比选表','table_id':'4.1-1'})
    if '4.1-2' in tables:
        b.append(P('对于存在多种设备配置路线的关键设备或设备组，在工艺方案确定的基础上进一步比较设备实施方式，推荐关系以已确认的设备专业结果为准。'))
        b.append(T(tables['4.1-2'],'表 4.1-2 关键设备方案比较表','4.1-2'))

    b.append(H('4.1.3.3 推荐方案及推荐理由',4))
    if adopted_available:
        desc=(adopted.get('scheme_description') or '').replace('位置：','改造位置为').replace('单元作用：','其作用为').replace('机理：','技术原理为')
        opening=f"综合各候选方案的技术效果、改造范围和工程实施影响，推荐采用{scheme_display(adopted)}。"
        if desc.strip():
            opening+=desc.strip()
        b.append(P(opening))
        rsel=adopted.get('reactor_selected_scheme') or {}
        rtext=(rsel.get('conclusion') or '').strip()
        if rtext:
            body=re.sub(r'^方案\s*\d+\s*[:：]\s*','',rtext).strip()
            body=body[3:] if body.startswith(('方案：','方案:')) else body
            if body:
                b.append(P('反应器配置方面，采用'+body.rstrip('。')+'。'))
            if rsel.get('pending_user_confirmation'):
                b.append(P('反应器具体规格和配置仍需在下一阶段结合设备专业条件进一步核定。'))
        for summary in equipment_summary_paragraphs(facts):
            b.append(P(summary))
    else:
        b.append(P('推荐方案应在现有专业成果基础上进一步核定，并结合技术效果和工程实施条件确定。'))
    b.append(H('4.1.4 工艺技术描述',3))
    if adopted_available: b.append(P((adopted.get('scheme_description') or '采用既定改造方案实施工艺流程优化。').replace('位置：','改造位置为').replace('单元作用：','其作用为').replace('机理：','技术原理为')))
    else: b.append(P('工艺技术描述按照最终确认的工艺拓扑和专业设备方案编制。'))
    b.append(H('4.2 工艺流程和消耗定额',2)); b.append(H('4.2.1 工艺流程说明',3))
    paras=topology_paragraphs(topology_analysis,facts)
    if paras:
        for x in paras: b.append(P(x))
    else: b.append(P('工艺流程按照改造后装置拓扑、主要流股及设备连接关系编制。'))
    b.append(H('4.2.2 物料平衡说明',3)); b.append(P('改造前后物料平衡采用一致的工艺计算口径，主要外部进出流股见下表。'))
    if '4.2-1' in tables: b.append(T(tables['4.2-1'],'表 4.2-1 改造前后物料平衡摘要表','4.2-1'))
    b.append(H('4.2.3 工艺消耗定额',3)); b.append(P('主要原料小时用量及已具备的公用工程负荷按专业计算结果列示；完整项目消耗定额在公用工程实物量边界确定后统一汇总。'))
    if '4.2-2' in tables: b.append(T(tables['4.2-2'],'表 4.2-2 主要消耗定额及改造前后对比表','4.2-2'))
    b.append(H('4.2.4 主要工艺设备一览表',3)); b.append(P('本节仅汇总因本项目产生设备工程动作的对象：新增设备、原设备本体改造设备，以及既有设备调整为新的用途/位置/工程角色的利旧设备。原设备继续原用途且设备本体无变化的对象不列入本节三类设备表。规格参数采用相应设备专业结果。'))
    for tid,cap in [('4.2-3','表 4.2-3 新增工艺设备汇总表'),('4.2-4','表 4.2-4 利旧工艺设备汇总表'),('4.2-5','表 4.2-5 改造工艺设备汇总表')]:
        if tid in tables: b.append(T(tables[tid],cap,tid))
    b.append(H('4.2.4.1 主要设备设计、制造及检验规范',4))
    for x in standards.get('process_equipment') or []: b.append(P(norm_label(x),False))
    b.append(H('4.4 自动控制',2))
    if not add_draft_blocks(b,dmap.get('4.4'),'4.4 自动控制'): b.append(P('自动控制改造范围应与工艺、设备改造边界相匹配，并与现有控制系统保持兼容。新增测点、控制回路及联锁要求由自控专业结合装置现状确定。'))
    b.append(H('4.4.1 主要设计标准和规范',3))
    for x in standards.get('automatic_control') or []: b.append(P(norm_label(x),False))

    # 5
    b=S('5 原材料、辅助材料、燃料和动力供应'); b.append(H('5.1 主要原材料、辅助材料、燃料的种类、规格、年需用量',2))
    _ah=(facts.get('user') or {}).get('annual_operating_hours')
    if _ah: b.append(P(f'主要原料需求按改造工况计算结果确定，年需用量按年运行{_ah}小时折算。'))
    else: b.append(P('主要原料需求按改造工况计算结果确定；年需用量在年运行时长明确时按统一年化口径计算。'))
    add_draft_blocks(b,dmap.get('5.1'),'5.1 主要原材料、辅助材料、燃料的种类、规格、年需用量')
    if '5.1-1' in tables: b.append(T(tables['5.1-1'],'表 5.1-1 主要原辅料及燃料需求表','5.1-1'))
    b.append(H('5.4 水、电、汽和其他动力供应',2)); b.append(P('公用工程需求按专业计算结果汇总。设备热负荷与蒸汽实物消耗严格区分，系统供需平衡以企业公用工程能力和运行负荷校核结果为准。'))
    if '5.4-1' in tables: b.append(T(tables['5.4-1'],'表 5.4-1 水、电、汽和其他动力需求表（已识别部分）','5.4-1'))

    # 7（7.1/7.2/7.3 为规范引用型章节：正文由确定性模板渲染，规范引用采用泛指表述，
    # 不虚构具体标准编号；具体布置、罐容与工程量仍以项目事实和专业校核为准）
    b=S('7 总图运输、储运、土建、界区内外管网'); b.append(H('7.1 总图运输',2))
    if not add_draft_blocks(b,dmap.get('7.1'),'7.1 总图运输'):
        b.append(P('本项目为装置内改造，新增及改造设备布置结合现有总平面及现场条件确定，总平面布置、防火间距、消防车道及运输组织满足现行石油化工企业总图运输及防火相关规范要求；具体布置与间距校核以实际总平面图为准，不以规范条文替代现场校核。'))
    b.append(H('7.2 储运',2))
    if not add_draft_blocks(b,dmap.get('7.2'),'7.2 储运'):
        b.append(P('本项目原料路线与产品方案不变，储运依托现有原料罐区、产品罐区及装卸设施；因产能提升，原辅料及产品储运量相应增加，储存、装卸及运输能力按改造后物流量与现有设施能力校核，并满足现行危险化学品储存、装卸及运输相关规范要求；实际罐容与周转能力以项目事实为准。'))
    b.append(H('7.3 厂区外管网',2))
    if not add_draft_blocks(b,dmap.get('7.3'),'7.3 厂区外管网'):
        b.append(P('本项目原料与产品接口保持不变，厂区外管网依托既有管网系统；界区接口、外管管径、材质、敷设方式及工程量按现有管网条件与新增介质需求确定，管道及管廊设计满足现行相关规范及属地要求；实际接口与路由以项目资料和专业计算为准。'))

    # 8
    b=S('8 公用工程方案和辅助生产设施'); b.append(H('8.1 公用工程方案',2)); b.append(P('公用工程方案按照改造后项目需求与企业现有系统设计能力、运行负荷及可用余量进行供需平衡。设备级已识别的介质和热负荷需求作为系统校核输入，不直接替代公用工程系统能力结论。'))
    _utility=(facts.get('enterprise') or {}).get('utility_system') or (facts.get('user') or {}).get('utility_system_capacity')
    if not _utility:
        b.append(P('企业现有公用工程系统的设计能力、当前负荷及可用余量尚需进一步落实；相关数据具备后应对改造后水、电、汽及其他动力需求进行系统供需校核。'))
    if '8.1-1' in tables: b.append(T(tables['8.1-1'],'表 8.1-1 公用工程系统供需平衡表','8.1-1'))
    b.append(H('8.1.1 给水排水设计依据',3)); [b.append(P(norm_label(x),False)) for x in standards.get('utility_water') or []]
    b.append(H('8.1.2 供配电设计依据',3)); [b.append(P(norm_label(x),False)) for x in standards.get('utility_power') or []]

    # 10
    b=S('10 节能'); b.append(H('10.1 编制依据',2)); b.append(H('10.1.1 国家及有关法律、法规和政策',3)); [b.append(P(norm_label(x),False)) for x in standards.get('energy_laws') or []]
    b.append(H('10.1.2 主要标准、规范',3)); [b.append(P(norm_label(x),False)) for x in standards.get('energy_standards') or []]
    b.append(H('10.2 项目用能概况',2))
    _urs=[]
    for _eq in ('reactor','tower'):
        _urs.extend(((facts.get('equipment') or {}).get(_eq) or {}).get('utility_requirements') or [])
    _media=[]
    for _u in _urs:
        _m=_u.get('medium') if isinstance(_u,dict) else None
        if _m and _m not in _media: _media.append(str(_m))
    _media_text=('，设备专业已识别的公用工程介质包括'+ '、'.join(_media)+'。') if _media else '。'
    b.append(P('项目用能范围按照改造方案涉及的电力、蒸汽及其他明确纳入核算边界的能源介质确定'+_media_text+'设备级热负荷、冷却负荷与能源实物消耗严格区分，最终能耗指标以统一核算边界下的实物量汇总和确定性折标结果为准。'))
    b.append(H('10.3 能源供应状况',2))
    if not add_draft_blocks(b,dmap.get('10.3'),'10.3 能源供应状况'):
        b.append(P('能源及公用工程供应条件以建设单位现有系统台账、运行负荷和接口条件为依据。'))
    b.append(H('10.4 项目节能分析与措施',2))
    scheme_txt=scheme_display(adopted) if adopted_available else '项目改造方案'
    b.append(P(f'{scheme_txt}的节能分析重点关注工艺负荷分配、设备匹配、热损失和输送损失，并优先通过优化运行条件、合理配置新增设备及减少无效能量损失控制单位产品能耗。节能量必须由改造前后统一能耗边界和能源实物量计算确定；在实物量未完整闭合前不形成推测性节能量。'))
    b.append(H('10.5 项目能耗指标',2))
    energy=facts.get('energy') or {}
    rules=energy.get('conversion_rules') or {}
    ctype=energy.get('accounting_standard') or 'standard_coal'
    conv_label='折标准煤' if ctype=='standard_coal' else '折标准油'
    total_col='标准煤t/a' if ctype=='standard_coal' else '标准油t/a'
    res_key='standard_coal_tce' if ctype=='standard_coal' else 'standard_oil_toe'
    idx=energy_rule_index(rules,ctype); steam=energy_steam_block(rules,ctype)
    fa_conv=(facts.get('fa') or {}).get('energy_conversion') or {}
    _block_text={'steam_pressure_or_grade_missing':'折标需蒸汽实际表压/压力等级','formula_input_missing':'需上年电厂发电标准煤耗（等价值）','unit_mismatch':'单位与折标规则不符','quantity_or_factor_missing':'实物量/系数缺失'}
    def _coef(name):
        rule=idx.get(normalize_media_name(name)) or {}
        if not rule and steam and '蒸汽' in str(name): return '按蒸汽实际表压区间确定（GB/T 50441-2016 表3.0.8）'
        fv=rule.get('coefficient'); fu=rule.get('coefficient_unit')
        if fv is None and rule.get('match_type')=='formula': return '按上年电厂发电标准煤耗确定（等价值）'
        return f'{fv} {fu}' if fv is not None and fu else (str(fv) if fv is not None else '待补充')
    rows=[]
    if fa_conv.get('items'):
        # 折标计算已由确定性 FA 工具完成：直接列示计算结果（含阻断项与缺失介质的处理状态）
        for it in fa_conv['items']:
            if it.get('status')=='calculated':
                coef=f"{it.get('coefficient')} {it.get('coefficient_unit')}" if it.get('coefficient') is not None else '待补充'
                if it.get('steam_grade'): coef=f"{it.get('steam_grade')}：{coef}"
                rows.append([it.get('medium'),it.get('unit'),it.get('quantity'),coef,it.get(res_key)])
            else:
                reason=_block_text.get(it.get('reason'),'待补充')
                rows.append([it.get('medium'),'待补充','待补充',reason,'待补充'])
        for m in fa_conv.get('missing_media') or []:
            rows.append([m.get('medium'),'待补充',str(m.get('quantity') or '待补充'),'折标系数待联网检索补充','待补充'])
    else:
        # 表行按装置实际用能列示：电力/蒸汽为常规动力与工艺加热（实物量待汇总，折标系数取自规则库）；
        # 导热油/循环冷却水按设备专业线索（reactor.utility_requirements）动态列示，折标口径待核算边界确定，不作推断
        urs=facts.get('equipment',{}).get('reactor',{}).get('utility_requirements',[]) or []
        eq=lambda m:'、'.join(dict.fromkeys(u.get('equipment_name') for u in urs if u.get('medium')==m and u.get('equipment_name')))
        rows.append(['电力','kWh','待补充',_coef('电力'),'待补充'])
        rows.append(['蒸汽','t','待补充',_coef('蒸汽'),'待补充'])
        if eq('HO'): rows.append(['导热油','t','待汇总','待核算边界确定','-'])
        if eq('CW'): rows.append(['循环冷却水','t','待汇总','是否折标待核算边界确定','-'])
    if rows: b.append({'type':'table','columns':['项目','单位','全年消耗量','折算系数',total_col],'rows':rows,'caption':'表 10.5-1 主要能源消耗分析','table_id':'10.5-1'})
    if ctype=='standard_coal':
        cite='电力折标系数依据《综合能耗计算通则》（GB/T 2589-2020）当量值0.1229 kgce/kWh；蒸汽折标系数128.6 kgce/t依据GB/T 2589-2008。'
    else:
        cite='电力、蒸汽折标系数依据《石油化工设计能耗计算标准》（GB/T 50441-2016），蒸汽按实际表压区间取值（表3.0.8），不做四舍五入。'
    b.append(P(f'本表按{conv_label}口径汇总，同一能耗计算表不混用折标准煤与折标准油两个体系。表中能源介质项按装置实际用能及设备专业线索列示：电力为机泵及辅助设施动力，蒸汽用于精馏塔再沸及工艺加热（塔再沸热负荷见5.4节，热源介质待核实），导热油为反应器供热用中间热载体，循环冷却水为反应器冷却及塔顶冷凝用耗能工质（均按设备专业计算结果列示）。{cite}导热油、循环冷却水是否纳入折标待能耗核算边界确定，不进行折标推断。全年消耗量待专业汇总及运行台账数据确定后填列。'))
    b.append(H('10.6 能耗分析',2))
    if not add_draft_blocks(b,dmap.get('10.6'),'10.6 能耗分析'): b.append(P('能耗分析采用项目改造前后对比口径，重点评价总能耗、单位产品能耗及变化原因，不进行行业能效标杆横向比较。'))

    # 18
    b=S('18 项目实施计划'); b.append(H('18.1 项目组织与管理',2)); b.append(P('项目组织机构及职责根据建设单位提供的项目组织架构、管理模式及职责分工编制。'))
    b.append(H('18.2 实施进度计划',2))
    sched=facts.get('implementation_schedule') or {}
    dur_map={str(p.get('name')):p.get('duration_months') for p in (sched.get('packages') or []) if isinstance(p,dict) and isinstance(p.get('duration_months'),(int,float)) and p.get('duration_months')>0}
    sched_available=bool(dur_map)
    drafted=False
    if not sched_available:
        drafted=add_draft_blocks(b,dmap.get('18.2'),'18.2 实施进度计划')
    if sched_available:
        total=sched.get('total_duration_months')
        if not (isinstance(total,(int,float)) and total>0): total=round(sum(dur_map.values()),1)
        lead='实施进度基于最终改造范围、主要设备及工程任务，按项目前期、基础设计、施工图设计、设备采购、土建施工、安装工程和试生产等工作包组织。'
        lead+=f'项目实施总工期约{total:g}个月，各工作包预计时长见下表。'
        b.append(P(lead,False))
    elif not drafted:
        b.append(P('实施进度基于最终改造范围、主要设备及工程任务，按项目前期、基础设计、施工图设计、设备采购、土建施工、安装工程和试生产等工作包组织，各工作包工期结合设备采购周期与施工窗口合理安排。'))
    if not drafted:
        pkg_map={str(p.get('name')):p for p in (sched.get('packages') or []) if isinstance(p,dict)}
        def _sched_pkg(work_package):
            if work_package in pkg_map: return pkg_map[work_package]
            for nm,pkg in pkg_map.items():
                if nm in work_package or work_package in nm: return pkg
            return None
        work_packages=['项目前期（各报告编制及审批）','基础设计','施工图设计','设备采购','土建施工','安装工程','试生产']
        schedule_rows=[]
        for wp in work_packages:
            pkg=_sched_pkg(wp); dur=(pkg or {}).get('duration_months')
            dur_text=f'{dur:g} 个月' if isinstance(dur,(int,float)) and dur>0 else '-'
            desc=(pkg or {}).get('description') or '待补充'
            dep=(pkg or {}).get('dependency') or '待补充'
            resp=(pkg or {}).get('owner') or '待补充'
            schedule_rows.append([wp,desc,dur_text,dep,resp])
        schedule={'columns':['阶段/工作包','主要内容','预计时长','前置条件/依赖','责任/确认'],'rows':schedule_rows}
        b.append(T(schedule,'表 18.2-1 项目实施进度计划表','18.2-1'))

    # 19
    b=S('19 投资估算')
    if adopted_available: b.append(P(f"投资估算范围以{scheme_display(adopted)}及设备专业结果为技术边界，纳入新增设备、原设备改造以及配套安装、配管、电仪和必要工程费用。"))
    else: b.append(P('投资估算范围以最终确认的工艺和设备方案为技术边界。'))
    scope=investment_scope_table(facts)
    if scope['rows']: b.append({'type':'table','columns':scope['columns'],'rows':scope['rows'],'caption':'表 19-1 主要投资对象范围','table_id':'19-scope'})
    b.append(P('投资估算以已确认的设备及工程改造范围为基础，设备价格、工程量、取费依据及价格基准按统一估算口径采用；尚未取得可靠依据的费用项目保持待估算，不生成推测金额。'))
    inv={'columns':['费用项目','金额（万元）','编制/价格依据','备注'],'rows':[['设备购置费','待估算','设备询价/价格基准','按采用方案设备对象'],['安装工程费','待估算','工程量及取费标准',''],['建筑/土建工程费','待估算','相关工程量及价格依据','费用分类保留'],['其他费用','待估算','取费规则',''],['预备费','待估算','取费规则',''],['建设期利息','待估算','融资参数',''],['流动资金','待估算','财务参数',''],['项目总投资','待估算','—','']]}; b.append(T(inv,'表 19-2 项目投资估算汇总表（框架）','19-1'))

    b=S('20 资金筹措'); b.append(P('资金筹措方案根据项目总投资、资本金安排和融资结构确定，资金使用计划应与建设进度相匹配。'))
    b=S('21 财务分析')
    if adopted_available: b.append(P(f"财务评价的技术边界与{scheme_display(adopted)}保持一致。收入、成本和现金流测算采用建设单位确认的产品价格、原辅料与能源成本、投资和融资参数。"))
    else: b.append(P('财务评价的技术边界与最终确认方案保持一致。'))
    b.append(P('财务评价以项目投资、产品及原辅料价格、能源成本、税费和融资参数为基础。内部收益率、净现值和投资回收期等指标仅在所需输入完整并经确认后计算；输入未闭合时不生成推测性指标。'))

    # 26 研究结论：LLM负责综合评价/总判断；固定缺口与建议使用正常工程语体渲染。
    b=S('26 研究结论'); b.append(H('26.1 综合评价',2))
    if not add_draft_blocks(b,dmap.get('26.1'),'26.1 综合评价'):
        b.append(H('26.1.1 技术方案综合评价',3))
        if adopted_available:
            b.append(P(f"现有研究成果表明，{scheme_display(adopted)}能够针对项目主要工艺问题实施有针对性的改造，技术路线和主要改造范围已经明确，具备进一步工程设计的基础。"))
        else:
            b.append(P('项目主要工艺问题已经识别，推荐技术方案仍需结合专业校核结果进一步确定。'))
        b.append(H('26.1.2 工程实施条件综合评价',3))
        b.append(P('本项目以既有生产设施为依托，具备实施改造的基本条件。新增设备布置、管线接入、自控改造及公用工程余量等条件仍需在下一阶段结合现场和专业资料进一步核实。'))
        b.append(H('26.1.3 投资与经济性综合评价',3))
        b.append(P('项目投资和经济性评价应以明确的工程量、设备价格、建设费用及财务参数为基础；在相关资料尚未完整具备时，本阶段不形成推测性的投资和收益指标。'))
    b.append(H('26.2 研究报告的结论',2))
    if not add_draft_blocks(b,dmap.get('26.2'),'26.2 研究报告的结论'):
        b.append(P('基于现有工艺和设备专业成果，本项目具备继续开展工程设计和技术经济评价的基础。项目是否进入投资实施阶段，应在公用工程及专项工程条件落实、投资估算完成并形成财务评价结果后综合确定。'))
    b.append(H('26.3 存在的问题',2)); gaps=[]
    if owner=='待明确': gaps.append('建设单位基本资料尚需补充。')
    if annual_hours is None: gaps.append('年运行时长尚需确认，以完成年生产规模和年原辅料消耗折算。')
    gaps += [
        '企业公用工程系统的设计能力、当前负荷及可用余量尚需补充，并据此核实改造后的供应能力。',
        '新增设备正式位号、主要管线接口及相关设备对应关系尚需进一步核实。',
        '总图、储运、厂区外管网和自动控制等专业条件尚需落实。',
        '投资估算和财务评价所需的工程量、设备价格及相关财务参数尚需确定。'
    ]
    b.append(NL(gaps))
    b.append(H('26.4 建议及实施条件',2))
    b.append(NL([
        '在下一阶段进一步落实工艺及设备改造范围，完善新增设备位号和主要管线接口。',
        '结合现场条件完成公用工程、总图储运、外管及自动控制等专业核实。',
        '在工程量和价格条件具备后完成投资估算，并据此开展财务评价。',
        '待上述工程条件和经济评价资料落实后，复核本报告研究结论并形成正式成果。'
    ]))

    # chapter_plan is authoritative for disabled/not-applicable top-level chapters.
    pmap=plan_status_map(chapter_plan)
    disabled={k for k,v in pmap.items() if is_disabled_status(v)}
    if disabled:
        kept=[]
        for sec in sections:
            m=re.match(r'^\s*(\d+(?:\.\d+)*)\b',str(sec.get('heading') or ''))
            sid=m.group(1) if m else None
            if sid and any(sid==d or sid.startswith(d+'.') for d in disabled if '-' not in d):
                continue
            kept.append(sec)
        sections=kept
    # cover 四元素对齐 report_formatter 契约: company_name/project_name/report_title/project_code
    # title/subtitle 为旧字段, 供未升级的 report_model 兼容渲染
    cover={'company_name':owner if owner!='待明确' else None,'project_name':project_name,'report_title':'可行性研究报告（初稿）','project_code':'xxxxxx','title':project_name,'subtitle':'可行性研究报告（初稿）'}
    return {'contract_version':'1.1','cover':cover,'properties':{'title':f'{project_name}可行性研究报告（初稿）','subject':'流程工业改造项目可行性研究报告'},'chapter_plan_snapshot':chapter_plan or {},'sections':sections}
