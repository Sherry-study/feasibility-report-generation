#!/usr/bin/env python3
"""Research Evidence 分片提交与收集辅助工具。

设计目的：Research 任务彼此独立，串行检索会拖慢宿主 Agent 的完整流程。
本模块把 `needs_research` 状态拆成固定 worker pack，并提供 submit/collect
两个确定性动作：

  needs_research
    -> research_fragments/worker_contexts/research_worker_XX.json（最多 2 个）
    -> 宿主并行派发 research worker
    -> --submit <fragment> 按任务校验证据并写入 validated/<task_id>.json
    -> --output <research_evidence.json> 合并并执行完整校验门

每个 task 的 submit 复用最终 Evidence 校验函数，只改变校验粒度，不降低
严格性。`validated/` 采用每任务一个文件，避免并发写同一文件。

CLI 返回码：0 通过；2 硬错误；4 覆盖不完整但已有分片合法。
"""
import argparse, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.common import dump_json, emit_result
from internal.planning.validate_evidence import validate as validate_evidence, REQUIRED

TASK_FILE_PREFIX='task_'
RESEARCH_WORKER_PREFIX='research_worker_'
DEFAULT_RESEARCH_WORKERS=2


def _search_budget(task):
    """暴露给 research worker 的硬检索预算。

    证据质量和 minimum_sources 仍由 validator 控制；这里的预算只防止在证据
    已经足够后继续开放式检索。
    """
    minimum=max(1,int(task.get('minimum_sources') or 1))
    rtype=str(task.get('research_type') or '')
    if rtype=='market_forecast':
        return {
            'max_search_rounds':2,
            'max_open_sources':max(3,min(4,minimum+1)),
            'stop_when_minimum_sufficient':True,
            'rule':'达到 minimum_sources 且已足以支撑章节核心判断后立即停止；不得为补充非关键细节继续搜索。',
        }
    return {
        'max_search_rounds':1,
        'max_open_sources':min(3,minimum+1),
        'stop_when_minimum_sufficient':True,
        'rule':'达到 minimum_sources 且足以支撑目标章节后立即停止；只有来源冲突或主检索失败时才允许继续。',
    }


def _task_pack(task, project_id):
    """把完整 Research task 压缩成单个 worker 可读的自包含任务包。"""
    queries=list(task.get('suggested_queries') or [])
    rtype=str(task.get('research_type') or '')
    primary_count=2 if rtype=='market_forecast' else 1
    optional=['published_at','accessed_at','official_source','source_authority','reliability','geography','data_period','effective_status','standard_code','numeric_facts','notes']
    return {
        'task_id':task.get('task_id'),
        'section_id':task.get('section_id'),
        'research_type':rtype,
        'purpose':task.get('purpose'),
        'subject':task.get('subject'),
        'geography':task.get('geography'),
        'time_scope':task.get('time_scope'),
        'questions':task.get('questions'),
        'primary_queries':queries[:primary_count],
        'fallback_queries':queries[primary_count:],
        'preferred_sources':task.get('preferred_sources'),
        'minimum_sources':task.get('minimum_sources'),
        'requires_official_source':task.get('requires_official_source'),
        'prohibited':task.get('prohibited'),
        'search_budget':_search_budget(task),
        'output_contract':{
            'fragment_file':'<output-dir>\\research_fragments\\evidence_<task_id>.json',
            'fragment_format':{'task_id':task.get('task_id'),'items':[{'evidence_id':'EV-<TYPE>-<NN>','task_id':task.get('task_id'),'claim':'一句话可核验论断','source_title':'来源标题','publisher':'发布方','source_url':'https://...','source_type':'见下方枚举','content_summary':'内容摘要（含关键数值时填 numeric_facts）'}]},
            'required_fields':list(REQUIRED),
            'optional_fields':optional,
            'source_type_examples':['official_government','official_statistics','official_association','official_enterprise_filing','industry_report','enterprise_disclosure','news_media','database'],
            'hard_rules':['source_type 不得为 search_snippet','source_url 必须是 http/https 可访问地址','同一任务至少 minimum_sources 个不同来源（publisher+title+url 均不同）','禁止编造数值或推断未披露信息','生产模式禁止任何测试标记'],
        },
        'project_id':project_id,
    }


def plan_research_workers(tasks_payload):
    """确定性双通道分配：市场任务单独作为长耗时通道，其余任务共用一组。

    这样宿主不需要重新规划 worker；只有一种通道存在时，只生成一个非空 worker。
    """
    tasks=list(tasks_payload.get('tasks') or [])
    market=[t for t in tasks if str(t.get('research_type'))=='market_forecast']
    general=[t for t in tasks if str(t.get('research_type'))!='market_forecast']
    lanes=[]
    if market:
        lanes.append(('research_worker_01','market_long_pole',market))
    if general:
        wid='research_worker_02' if market else 'research_worker_01'
        lanes.append((wid,'general_research',general))
    return lanes


def _load(path):
    """读取 JSON 文件；Research 分片工具只接受 JSON。"""
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _safe(name):
    """把 task_id 转成可作为文件名的稳定字符串。"""
    for ch in '\\/:*?"<>|.-':
        name=name.replace(ch,'_')
    return name


def write_research_packs(tasks_payload, frag_dir):
    """写入最多两个自包含 research worker pack，不生成冗余 digest/plan 文件。"""
    fdir=Path(frag_dir); cdir=fdir/'worker_contexts'; cdir.mkdir(parents=True,exist_ok=True)
    tasks=list(tasks_payload.get('tasks') or [])
    pid=tasks_payload.get('project_id')
    lanes=plan_research_workers(tasks_payload)
    worker_rows=[]; total_bytes=0
    for wid,lane,owned in lanes:
        pack={
            'contract_version':'1.0','pack_type':'research_worker','worker_id':wid,'lane':lane,'project_id':pid,
            'execution_rules':[
                '按 tasks[] 顺序完成；严格遵守每个 task.search_budget，达到最小充分证据后立即停止。',
                '每完成一个 task 写 evidence_<task_id>.json 并 submit；失败只修当前 task。',
                '禁止读取 research_tasks.json、其他 worker pack 或工程事实全量文件。',
            ],
            'tasks':[_task_pack(t,pid) for t in owned],
        }
        path=cdir/f'{wid}.json'; dump_json(pack,path)
        size=path.stat().st_size; total_bytes+=size
        worker_rows.append({'worker_id':wid,'lane':lane,'context_pack':f'worker_contexts/{wid}.json','task_ids':[str(t.get('task_id')) for t in owned],'tasks':len(owned),'pack_bytes':size})
    manifest={'contract_version':'1.0','plan_type':'research_workers','task_count':len(tasks),'worker_count':len(worker_rows),'workers':worker_rows,'total_pack_bytes':total_bytes}
    return {'contexts_dir':str(cdir),'packs':len(worker_rows),'workers':len(worker_rows),'task_count':len(tasks),'total_pack_bytes':total_bytes,'manifest_data':manifest}


def write_research_host_workflow(tasks_payload, frag_dir, output_dir, skill_root, worker_manifest=None):
    """写入宿主执行说明，作为首次 Research 的唯一入口。"""
    fdir=Path(frag_dir); outdir=Path(output_dir)
    manifest=worker_manifest or {'workers':[]}
    workers=manifest.get('workers') or []
    L=[
      '# Research 执行入口（严格执行）','',
      f'共 {len(tasks_payload.get("tasks") or [])} 个任务，已固定为 {len(workers)} 个 worker。不要扫描目录、不要读取 research_tasks.json、不要重新分配任务。','',
      '1. **一次性并行派发全部 Research Worker**；每个 subAgent 只读自己的 worker pack。',
      '2. 严格遵守 `search_budget`：先 primary_queries；达到 minimum_sources 且足够支撑正文后立即停止；非必要不跑 fallback_queries。',
      '3. 每完成一个 task 写 evidence_<task_id>.json 并 submit；失败只修当前 task。',
      '4. 全部完成后只 collect 一次；collect exit 0 后立即重新调用 chapter_planning Tool，传入 collect 产物 research_evidence.json。若返回 needs_llm，直接执行 Draft HOST_WORKFLOW，中间不总结、不重新规划。','',
      '## Worker','```'
    ]
    for w in workers:
        L.append(f"{w['worker_id']}: {w['context_pack']} ; lane={w['lane']} ; tasks={', '.join(w['task_ids'])}")
    L += ['```','',
      '## submit（每 task）','```',
      f'python "{skill_root}/internal/planning/research_fragments.py" --tasks "{outdir}/research_tasks.json" --fragments-dir "{fdir}" --submit "{outdir}/research_fragments/evidence_<task_id>.json"',
      '```','',
      '## collect（全部 task 后一次）','```',
      f'python "{skill_root}/internal/planning/research_fragments.py" --tasks "{outdir}/research_tasks.json" --fragments-dir "{fdir}" --output "{outdir}/research_evidence.json"',
      '```','',
      f'collect exit 0 后立即重新调用 `chapter_planning` Tool，并设置 `research_evidence="{outdir}/research_evidence.json"`。'
    ]
    wf=fdir/'HOST_WORKFLOW.md'; wf.write_text('\n'.join(L)+'\n',encoding='utf-8')
    return str(wf)


def _fragment_items(raw):
    """兼容三种 Evidence 分片形态：带 task_id、仅 items、或裸数组。"""
    if isinstance(raw,dict):
        return raw.get('task_id'), list(raw.get('items') or [])
    if isinstance(raw,list):
        return None, list(raw)
    raise ValueError('fragment must be an object with items[] or an item array')


def submit_research(tasks_payload, frag_dir, fragment_path, mode='production'):
    """校验单个 task 的 Evidence，并在通过后持久化到 validated/<task_id>.json。"""
    fdir=Path(frag_dir)
    raw=_fragment_items(_load(fragment_path))
    declared_tid, items=raw
    tids={i.get('task_id') for i in items if i.get('task_id')}
    if declared_tid: tids.add(declared_tid)
    if len(tids)!=1:
        return {'valid':False,'issues':[f'fragment must contain items for exactly one task_id, got {sorted(t for t in tids if t)}']},2
    tid=next(iter(tids))
    task_map={t.get('task_id'):t for t in tasks_payload.get('tasks') or []}
    if tid not in task_map:
        return {'valid':False,'issues':[f'unknown task_id: {tid}']},2
    single={'contract_version':'1.0','project_id':tasks_payload.get('project_id'),'tasks':[task_map[tid]]}
    ev={'contract_version':'1.0','project_id':tasks_payload.get('project_id'),'items':items}
    result,code=validate_evidence(single,ev,mode)
    if code==0:
        vdir=fdir/'validated'; vdir.mkdir(parents=True,exist_ok=True)
        dump_json({'task_id':tid,'items':items},vdir/(TASK_FILE_PREFIX+_safe(str(tid))+'.json'))
    result['task_id']=tid
    return result,code


def _collect_entries(fdir, issues):
    """收集 validated/ 与 loose fragment，返回 {task_id: [items]}。"""
    by_task={}
    seen={}
    vdir=fdir/'validated'
    sources=[]
    if vdir.is_dir(): sources+=sorted(vdir.glob('*.json'))
    for f in sorted(fdir.glob('*.json')):
        if f.parent==fdir and not f.name.startswith(('batch_','evidence_plan','collect_status','worker_plan')): sources.append(f)
    for f in sources:
        try: raw=_fragment_items(_load(f))
        except Exception as e: issues.append(f'{f.name}: {e}'); continue
        tid,items=raw
        for it in items:
            t=it.get('task_id') or tid
            eid=it.get('evidence_id')
            if not t: issues.append(f'{f.name}: item without task_id'); continue
            if eid in seen and seen[eid]!=f'{t}|{f.name}':
                issues.append(f'duplicate evidence_id {eid} (also in {seen[eid]})'); continue
            if eid: seen[eid]=f'{t}|{f.name}'
            by_task.setdefault(t,[]).append(it)
    return by_task


def collect_research(tasks_payload, frag_dir, output_path, mode='production'):
    """合并 Evidence 分片并执行完整校验门；返回码 0/2/4。"""
    fdir=Path(frag_dir); issues=[]
    by_task=_collect_entries(fdir,issues)
    items=[it for t in tasks_payload.get('tasks') or [] for it in by_task.get(t.get('task_id'),[]) ]
    known_extra=[t for t in by_task if t not in {x.get('task_id') for x in tasks_payload.get('tasks') or []}]
    for t in known_extra: issues.append(f'unknown task_id in fragments: {t}')
    missing=[t.get('task_id') for t in tasks_payload.get('tasks') or [] if not by_task.get(t.get('task_id'))]
    # Partial-coverage runs validate only covered tasks (same gate, same
    # strictness); missing tasks are reported via exit 4 instead of being
    # turned into spurious hard errors by the minimum_sources check.
    covered_tasks=[t for t in tasks_payload.get('tasks') or [] if by_task.get(t.get('task_id'))]
    sub_payload={'contract_version':'1.0','project_id':tasks_payload.get('project_id'),'tasks':covered_tasks}
    ev={'contract_version':'1.0','project_id':tasks_payload.get('project_id'),'items':items}
    result,code=validate_evidence(sub_payload,ev,mode)
    all_issues=list(issues)+list(result.get('issues') or [])
    status='generated' if (not all_issues and not missing) else ('partial' if not all_issues else 'error')
    if all_issues or missing:
        out={'valid':not all_issues,'mode':mode,'status':status,'issues':all_issues,'missing_tasks':missing,
             'covered_tasks':[t.get('task_id') for t in tasks_payload.get('tasks') or [] if by_task.get(t.get('task_id'))],
             'evidence_count':len(items),'task_count':len(tasks_payload.get('tasks') or [])}
        return out,(2 if all_issues else 4)
    Path(output_path).parent.mkdir(parents=True,exist_ok=True)
    dump_json(ev,output_path)
    return {'valid':True,'mode':mode,'status':'generated','issues':[],'missing_tasks':[],'covered_tasks':sorted(by_task),'evidence_count':len(items),'task_count':len(tasks_payload.get('tasks') or []),'research_evidence':str(output_path)},0


def main():
    ap=argparse.ArgumentParser(description='提交或收集 Research Evidence 分片。')
    ap.add_argument('--tasks',required=True)
    ap.add_argument('--fragments-dir',required=True)
    ap.add_argument('--mode',choices=['production','test'],default='production')
    ap.add_argument('--submit')
    ap.add_argument('--output')
    ap.add_argument('--result-output')
    a=ap.parse_args()
    tasks_payload=_load(a.tasks); fdir=Path(a.fragments_dir)
    if a.submit:
        result,code=submit_research(tasks_payload,fdir,a.submit,a.mode)
        emit_result(a.result_output or None,result); return code
    if a.output:
        result,code=collect_research(tasks_payload,fdir,a.output,a.mode)
        emit_result(a.result_output or None,result); return code
    ap.error('either --submit or --output is required'); return 2
if __name__=='__main__': raise SystemExit(main())
