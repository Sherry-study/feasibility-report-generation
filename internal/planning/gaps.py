#!/usr/bin/env python3
"""Planning-stage gap analysis: missing-input routing honoring chapter_plan applicability."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.domain import GAP_AFFECTED_SECTIONS, is_disabled_status, plan_status_map
from internal.planning.chapter_rules import load_chapter_rules, select_active_rules, evaluate_required_facts


def action_for(resp):
    vals=resp if isinstance(resp,list) else [str(resp or '')]
    if 'U' in vals: return 'ask_user_or_use_project_document'
    if 'PA' in vals or 'EA' in vals: return 'request_upstream_professional_result'
    if 'R' in vals: return 'research_if_publicly_obtainable'
    if 'C' in vals: return 'request_confirmation'
    if 'FA' in vals: return 'run_feasibility_calculation_tool'
    return 'record_gap'


def _status_for(section,pmap):
    s=str(section)
    if s in pmap: return pmap[s]
    # Match parent range entries such as 10.1-10.6 and exact parent sections.
    candidates=[]
    for key,status in pmap.items():
        if '-' not in key and (s==key or s.startswith(key+'.')): candidates.append((len(key),status))
        elif key=='10.1-10.6' and s.startswith('10'): candidates.append((2,status))
    return max(candidates,default=(0,''))[1]

def _active(section,pmap):
    st=_status_for(section,pmap)
    return not is_disabled_status(st)


def _question_for_rule_gap(gap, action):
    field=gap.get('fact_id') or gap.get('field')
    desc=gap.get('reason') or field
    section=gap.get('section_id')
    if action=='confirmation_required':
        question=f'请确认或修正{desc}，并通过确认响应或用户输入重跑。'
        blocking=True
    else:
        question=f'请补充{desc}。若暂时无法提供，可跳过并保留缺口。'
        blocking=False
    return {
      'field':field,
      'phase':'writing',
      'question':question,
      'unit':None,
      'blocking':blocking,
      'affected_sections':[section] if section else [],
      'rule_id':gap.get('rule_id'),
      'fact_id':gap.get('fact_id'),
      'missing_action':action,
    }


def _add_deferred(deferred, item):
    key=item.get('field')
    for old in deferred:
        if old.get('field')==key:
            old_sections=set(old.get('affected_sections') or [])
            old_sections.update(item.get('affected_sections') or [])
            old['affected_sections']=sorted(old_sections)
            if item.get('rule_id') and not old.get('rule_id'):
                old['rule_id']=item.get('rule_id')
            return
    deferred.append(item)


def analyze(facts,plan=None):
    gaps=[]; pmap=plan_status_map(plan)
    for g in facts.get('gaps') or []:
        x=dict(g); field=x.get('field'); affected=x.get('affected_sections') or GAP_AFFECTED_SECTIONS.get(field,[])
        x['affected_sections_all']=list(affected)
        x['affected_sections']=[s for s in affected if _active(s,pmap)] if pmap else list(affected)
        x['suppressed_by_plan']=bool(affected) and not x['affected_sections']
        x['recommended_action']=action_for(x.get('responsibility'))
        if not x['suppressed_by_plan']: gaps.append(x)
    blocked_sections=[]; rule_missing=[]; rule_deferred=[]
    registry=load_chapter_rules()
    active_rules=select_active_rules(registry,plan or {})
    for rule in active_rules:
        if rule.get('coverage_class') not in ('llm_narrative','deterministic_only'): continue
        for finding in evaluate_required_facts(rule,facts):
            action=finding.get('missing_action')
            gap={
              'responsibility':finding.get('responsibility') or [],
              'field':f"chapter_rule.{finding.get('rule_id')}.{finding.get('fact_id')}",
              'reason':finding.get('description') or finding.get('reason'),
              'affected_sections':[finding.get('section_id')],
              'rule_id':finding.get('rule_id'),
              'section_id':finding.get('section_id'),
              'fact_id':finding.get('fact_id'),
              'missing_action':action,
              'source_paths':finding.get('source_paths') or [],
              'recommended_action':action_for(finding.get('responsibility')),
            }
            gaps.append(gap); rule_missing.append(gap)
            if action in ('ask_user','confirmation_required'):
                rule_deferred.append(_question_for_rule_gap(gap,action))
            if action in ('waiting_upstream','calculation_blocked','confirmation_required'):
                blocked_sections.append(gap)
    # Deferred questions are collected at the writing-preparation phase (after the
    # engineering-fact confirmation gate, during stage-3 gap re-run), never as a
    # blocking preflight interrupt before confirmation.
    deferred=[]; project=facts.get('project') or {}; user=facts.get('user') or {}
    for item in rule_deferred:
        _add_deferred(deferred,item)
    if _active('1.1.2',pmap) and not (project.get('construction_unit') or user.get('construction_unit') or user.get('owner')):
        _add_deferred(deferred,{'field':'construction_unit','phase':'writing','question':'请提供本项目建设单位（主办单位）名称。若暂时无法提供，可跳过并保留缺口。','unit':None,'blocking':False,'affected_sections':['1.1.1','1.1.2']})
    annual_sections=[s for s in GAP_AFFECTED_SECTIONS['annual_operating_hours'] if _active(s,pmap)]
    if annual_sections and user.get('annual_operating_hours') in (None,''):
        _add_deferred(deferred,{'field':'annual_operating_hours','phase':'writing','question':'请提供年运行时长。若暂时无法确定，可跳过；相关年化指标将保持缺口。','unit':'h/a','blocking':False,'affected_sections':annual_sections})
    if _active('1.1.1',pmap) and (project.get('project_name_source') or 'default') in ('workspace_dir','default'):
        _add_deferred(deferred,{'field':'project_name','phase':'writing','question':'请提供正式项目名称（当前仅以数据源目录名/默认值占位，会直接用于报告封面与1.1.1）。将答案写入 user_inputs.yaml（project.project_name）或经 --project-name 提供后，携带 --user-inputs 与原确认参数重跑；若确认使用占位名，可跳过。','unit':None,'blocking':False,'affected_sections':['1.1.1']})
    if _active('18.2',pmap) and not facts.get('implementation_schedule'):
        # 实施进度不向用户提问：无企业计划值时由章节规则授权 LLM 直接起草工期估算。
        pass
    grouped={}
    for g in gaps:
        resp=g.get('responsibility','UNKNOWN')
        key='/'.join(resp) if isinstance(resp,list) else str(resp or 'UNKNOWN')
        grouped.setdefault(key,[]).append(g)
    return {'contract_version':'1.3','status':'gaps_found' if gaps else 'complete','gaps':gaps,'rule_missing_facts':rule_missing,'blocked_sections':blocked_sections,'deferred_questions':deferred,'by_responsibility':grouped,'chapter_plan_applied':bool(pmap)}
