#!/usr/bin/env python3
"""section_drafts validation.

Two layers share the same per-draft rules:
- validate_draft_entries(jobs, evidence, drafts_list, mode): per-draft checks
  (headings / evidence binding / claims / banned terms / duplicates). Used by the
  full-document gate below and by the incremental fragment collector
  (internal/planning/draft_fragments.py) for fail-fast per-fragment feedback.
- validate(jobs, evidence, drafts_doc, mode): full-document gate = header checks
  (contract_version / project_id / test_only) + per-draft entries + coverage.
  Signature and issue wording are unchanged from the pre-refactor contract.
"""
import argparse, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.domain import production_guard
from internal.common import emit_result
from internal.planning.chapter_rules import TEMPLATE_RENDERED_SECTIONS
BAD_TERMS=['Project Facts','Payload','ResearchProvider','LLMProvider','Source Resolver','Skill current','Skill previous','candidate recommendation','工程化拓扑','PA/EA/FA']
BAD_REPORT_PHRASES=['主要资料来源','参考资料：','商业报告口径','仅作量级参考','未经核验','正式引用需','不同公开资料','本报告不采纳相互冲突','非正式结算价，仅作行情参考','候选阶段技术排序','不自动等同']


def validate_draft_entries(jobs, evidence, drafts, mode='production'):
    """Per-draft checks on a drafts list. Returns (issues, covered_section_ids)."""
    ev=evidence; issues=[]
    valid_ev={x.get('evidence_id') for x in ev.get('items',[])}; job_map={str(x.get('section_id')):x for x in jobs.get('jobs',[])}; seen=set()
    for i,d in enumerate(drafts or []):
        sid=str(d.get('section_id')); job=job_map.get(sid)
        if not job:
            # template_rendered 章节不再进入 llm_jobs：历史草稿直接忽略（不再视为硬错误），
            # 其余无对应 job 的草稿仍为硬错误（防章节号漂移）。
            if sid in TEMPLATE_RENDERED_SECTIONS: continue
            issues.append(f'draft[{i}] section_id {sid} has no requested job'); continue
        if sid in seen: issues.append(f'duplicate section draft: {sid}')
        seen.add(sid)
        if d.get('draft_status') not in ('draft','partial','blocked','not_applicable'): issues.append(f'invalid draft_status for section {sid}')
        cited=set(d.get('source_evidence_ids',[])); allowed=set(job.get('allowed_headings',[]))
        for sub in d.get('subsections',[]):
            h=str(sub.get('heading') or '')
            if allowed and h not in allowed: issues.append(f'section {sid} unexpected subsection heading: {h}')
        unknown=cited-valid_ev
        if unknown: issues.append(f'section {sid} cites unknown evidence: {sorted(unknown)}')
        if d.get('draft_status') in ('draft','partial') and not cited and job.get('research_task_ids'): issues.append(f'section {sid} draft/partial must cite evidence')
        for c in d.get('claims',[]):
            ids=set(c.get('evidence_ids',[]))
            if not ids: issues.append(f'section {sid} claim has no evidence_ids: {c.get("text","")[:80]}')
            if ids-cited: issues.append(f'section {sid} claim cites evidence not declared in source_evidence_ids: {sorted(ids-cited)}')
            if ids-valid_ev: issues.append(f'section {sid} claim cites unknown evidence: {sorted(ids-valid_ev)}')
        text='\n'.join(p for s in d.get('subsections',[]) for p in s.get('paragraphs',[]))
        for term in BAD_TERMS:
            if term in text: issues.append(f'section {sid} exposes internal term: {term}')
        for phrase in BAD_REPORT_PHRASES:
            if phrase in text: issues.append(f'section {sid} contains research-process/disclaimer language not allowed in report body: {phrase}')
    return issues, seen


def validate(jobs, evidence, drafts, mode='production'):
    """Pure function entry: returns (result_dict, exit_code)."""
    dr=drafts; issues=[]
    try: production_guard(dr,'section_drafts',mode)
    except Exception as e: issues.append(str(e))
    if dr.get('contract_version')!='1.0': issues.append('contract_version must be 1.0')
    if dr.get('project_id')!=jobs.get('project_id'): issues.append('project_id does not match LLM jobs')
    if mode=='test' and dr.get('test_only') is not True: issues.append('test mode requires test_only=true section_drafts')
    entry_issues, seen = validate_draft_entries(jobs, evidence, dr.get('drafts',[]), mode)
    issues.extend(entry_issues)
    job_map={str(x.get('section_id')) for x in jobs.get('jobs',[])}
    missing=set(job_map)-seen
    if missing: issues.append(f'missing drafts for sections: {sorted(missing)}')
    result={'valid':not issues,'mode':mode,'issues':issues,'draft_count':len(dr.get('drafts',[])),'job_count':len(job_map)}
    return result, (0 if not issues else 2)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--jobs',required=True); ap.add_argument('--evidence',required=True); ap.add_argument('--drafts',required=True); ap.add_argument('--mode',choices=['production','test'],default='production'); ap.add_argument('--output')
    a=ap.parse_args(); jobs=json.loads(Path(a.jobs).read_text(encoding='utf-8')); ev=json.loads(Path(a.evidence).read_text(encoding='utf-8')); dr=json.loads(Path(a.drafts).read_text(encoding='utf-8'))
    result,code=validate(jobs,ev,dr,a.mode)
    emit_result(a.output or None, result); return code
if __name__=='__main__': raise SystemExit(main())
