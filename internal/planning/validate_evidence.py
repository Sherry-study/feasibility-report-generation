#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.domain import production_guard
from internal.common import emit_result

REQUIRED=('evidence_id','task_id','claim','source_title','publisher','source_url','source_type','content_summary')
OFFICIAL_TYPES={'official_government','official_standard','official_regulator','official_statistics','official_enterprise_filing','official_association','enterprise_disclosure'}


def validate(tasks, evidence, mode='production'):
    """Pure function entry: returns (result_dict, exit_code)."""
    ev=evidence
    issues=[]
    try: production_guard(ev,'research_evidence',mode)
    except Exception as e: issues.append(str(e))
    if ev.get('contract_version')!='1.0': issues.append('contract_version must be 1.0')
    if ev.get('project_id')!=tasks.get('project_id'): issues.append('project_id does not match research tasks')
    if mode=='test' and ev.get('test_only') is not True:
        issues.append('test mode requires test_only=true evidence')
    task_map={x['task_id']:x for x in tasks.get('tasks',[])}; seen=set(); by_task={k:[] for k in task_map}
    for i,item in enumerate(ev.get('items',[])):
        miss=[k for k in REQUIRED if not item.get(k)]
        if miss: issues.append(f'item[{i}] missing: {miss}')
        eid=item.get('evidence_id')
        if eid in seen: issues.append(f'duplicate evidence_id: {eid}')
        seen.add(eid); tid=item.get('task_id')
        if tid not in task_map: issues.append(f'unknown task_id: {tid}')
        else: by_task[tid].append(item)
        url=item.get('source_url','')
        if url and urlparse(url).scheme not in ('http','https','file','urn'): issues.append(f'invalid source_url scheme for {eid}: {url}')
        if item.get('source_type')=='search_snippet': issues.append(f'{eid}: search_snippet cannot be final evidence')
        if mode=='production' and str(url).startswith('urn:test:'): issues.append(f'{eid}: urn:test is forbidden in production')
    for tid,t in task_map.items():
        rows=by_task.get(tid,[])
        unique_sources={(x.get('publisher'),x.get('source_title'),x.get('source_url')) for x in rows}
        if len(unique_sources)<int(t.get('minimum_sources',1)):
            issues.append(f'{tid} requires at least {t.get("minimum_sources")} distinct sources; got {len(unique_sources)}')
        if t.get('requires_official_source') and rows:
            if not any((x.get('source_type') in OFFICIAL_TYPES) or x.get('official_source') is True for x in rows):
                issues.append(f'{tid} requires at least one official/primary source')
    result={'valid':not issues,'mode':mode,'issues':issues,'evidence_count':len(ev.get('items',[])),'task_count':len(task_map),'covered_tasks':sum(1 for v in by_task.values() if v)}
    return result, (0 if not issues else 2)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--tasks',required=True); ap.add_argument('--evidence',required=True); ap.add_argument('--mode',choices=['production','test'],default='production'); ap.add_argument('--output')
    a=ap.parse_args(); tasks=json.loads(Path(a.tasks).read_text(encoding='utf-8')); ev=json.loads(Path(a.evidence).read_text(encoding='utf-8'))
    result,code=validate(tasks,ev,a.mode)
    emit_result(a.output or None, result); return code
if __name__=='__main__': raise SystemExit(main())
