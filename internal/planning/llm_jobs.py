#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from internal.common import dump_json, load_data
from internal.planning.chapter_rules import (
    bind_section_context,
    evaluate_required_facts,
    load_chapter_rules,
    select_active_rules,
)

SYSTEM_INSTRUCTION='你是流程工业改造项目可行性研究报告章节编制器。只使用给定工程事实、章节上下文和research_evidence；不得创造数值、工程条件、市场事实或经济参数。正文采用设计院可研语体，只写工程事实、必要论证和结论，不解释检索、算法、证据流程或系统内部状态。外部证据仅用source_evidence_ids留痕，不在正文列URL或证据ID。'
BLOCKING_ACTIONS={'ask_user','confirmation_required','waiting_upstream','calculation_blocked'}


def _skip_llm_when_deterministic(section_id, facts):
    """Skip LLM when confirmed facts already support deterministic report prose."""
    sid=str(section_id)
    project=facts.get('project') or {}
    if sid=='4.1.1' and project.get('raw_material_route_changed') is False:
        return True
    if sid=='18.2':
        sched=facts.get('implementation_schedule') or {}
        if sched.get('packages'):
            return True
    return False


def compact_jobs_for_validation(payload):
    """Persist only fields required by incremental draft validation."""
    return {
      'contract_version':payload.get('contract_version','1.0'),
      'project_id':payload.get('project_id'),
      'jobs':[{
        'section_id':j.get('section_id'),
        'allowed_headings':j.get('allowed_headings') or [],
        'research_task_ids':j.get('research_task_ids') or [],
      } for j in (payload.get('jobs') or [])],
      'skipped_sections':payload.get('skipped_sections') or [],
    }


def _task_ids_for_section(section_id, tasks):
    tids=[]
    for task in tasks.get('tasks',[]) or []:
        targets=[str(x) for x in task.get('target_sections',[task.get('section_id')])]
        if str(section_id) in targets:
            tids.append(task.get('task_id'))
    return [x for x in tids if x]


def _open_items(findings):
    return [
      {
        'fact_id':x.get('fact_id'),
        'description':x.get('description'),
        'missing_action':x.get('missing_action'),
        'responsibility':x.get('responsibility') or [],
        'source_paths':x.get('source_paths') or [],
      }
      for x in findings
    ]


def build_jobs(profile_file, plan_file, facts, tasks, evidence):
    """Pure function entry: rules + confirmed facts + tasks/evidence -> jobs payload."""
    profile=profile_file.get('project_profile',profile_file)
    active_rules=select_active_rules(load_chapter_rules(),plan_file)
    jobs=[]; skipped=[]
    for rule in active_rules:
        if rule.get('coverage_class')!='llm_narrative':
            continue
        sid=str(rule.get('section_id'))
        if _skip_llm_when_deterministic(sid,facts):
            skipped.append({'section_id':sid,'rule_id':rule.get('rule_id'),'reason':'deterministic_context_available'})
            continue
        findings=evaluate_required_facts(rule,facts)
        blocking=[x for x in findings if x.get('missing_action') in BLOCKING_ACTIONS]
        tids=_task_ids_for_section(sid,tasks)
        ev=[x for x in evidence.get('items',[]) if x.get('task_id') in tids]
        research_missing=[x for x in findings if x.get('missing_action')=='research_required']
        if research_missing and not ev:
            blocking.extend(research_missing)
        if blocking:
            skipped.append({'section_id':sid,'rule_id':rule.get('rule_id'),'reason':'blocked_required_facts','missing_facts':blocking})
            continue
        context=bind_section_context(rule,profile,facts)
        context['open_items'].extend(_open_items([x for x in findings if x.get('missing_action')=='allow_open_item']))
        output=rule.get('output') or {}
        allowed=output.get('allowed_headings') or []
        jobs.append({
          'job_id':f"LLM-SEC-{sid.replace('.','')}-01",
          'rule_id':rule.get('rule_id'),
          'rule_version':rule.get('rule_version'),
          'rule_status':rule.get('rule_status'),
          'section_id':sid,
          'title':rule.get('title') or sid,
          'research_task_ids':tids,
          'system_instruction':SYSTEM_INSTRUCTION,
          'chapter_context':context,
          'project_context':context,
          'research_evidence':ev,
          'required_structure':output.get('required_structure') or [],
          'allowed_headings':allowed,
          'generation_rules':rule.get('generation_rules') or {},
          'quality_checks':rule.get('quality_checks') or [],
          'output_contract':{
            'section_id':sid,
            'draft_status':'draft|partial|blocked|not_applicable',
            'subsections':[{'heading':'必须来自allowed_headings','paragraphs':['正式可研正文']}],
            'source_evidence_ids':['仅在使用外部证据时填写证据ID']
          }
        })
    return {'contract_version':'1.0','project_id':profile.get('project_id'),'jobs':jobs,'skipped_sections':skipped}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--profile',required=True); ap.add_argument('--plan',required=True); ap.add_argument('--facts',required=True)
    ap.add_argument('--research-tasks',required=True); ap.add_argument('--research-evidence',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args()
    out=build_jobs(load_data(a.profile),load_data(a.plan),load_data(a.facts),load_data(a.research_tasks),load_data(a.research_evidence))
    jobs=out.get('jobs') or []
    dump_json(out,a.output)
    print(json.dumps({'output':a.output,'jobs':len(jobs),'sections':[j['section_id'] for j in jobs]},ensure_ascii=False))


if __name__=='__main__': main()
