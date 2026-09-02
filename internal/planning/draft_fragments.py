#!/usr/bin/env python3
"""章节草稿分片提交与收集辅助工具。

设计目的：完整 `section_drafts.json` 往往超过宿主 LLM 单轮输出上限，容易在
写入 JSON 中途被截断。业务契约只需要最终得到一个完整且通过校验的
`section_drafts.json`，所以本模块把草稿写作拆为 worker batch，并用
submit/collect 两个确定性动作合成最终文件。它不修改阶段状态码、Schema
或主流程，只是辅助宿主 Agent 可靠收集草稿。

worker 分配是确定性的：默认最多 3 个非空 worker pack。章节仍是最小校验
单元；一个 worker 可以提交包含多个章节的 `batch_worker_XX.json`。

常用 CLI：
  plan:    python internal/planning/draft_fragments.py --plan --jobs llm_jobs.json \
                --output draft_fragments/batch_plan.json
  collect: python internal/planning/draft_fragments.py --jobs llm_jobs.json \
                --evidence research_evidence.json --fragments-dir draft_fragments \
                --output section_drafts.json
  check:   python internal/planning/draft_fragments.py --jobs llm_jobs.json \
                --evidence research_evidence.json --validate-fragment batch_01.json
  submit:  python internal/planning/draft_fragments.py --jobs llm_jobs.json \
                --evidence research_evidence.json --fragments-dir draft_fragments \
                --submit draft_1_2.json

分片文件可为裸 JSON 数组、`{"drafts": [...]}`，或单个 draft object。
submit 通过的章节会写入 `draft_fragments/validated/<section_id>.json`；
collect 时 submitted 版本优先，并在 `shadowed` 中记录被覆盖的原始分片。

CLI 返回码：0 完成或校验通过；2 存在硬错误；4 当前分片合法但覆盖不完整。
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.common import dump_json, emit_result
from internal.domain import production_guard
from internal.report.validate_drafts import validate, validate_draft_entries

MAX_SECTIONS_PER_BATCH=6
BATCH_CHAR_TARGET=12000
BATCH_CHAR_MIN=4000
DEFAULT_WORKER_COUNT=3


def _section_char_budget(section_id):
    """每节中文正文长度目标，用于约束 worker 输出不要膨胀。"""
    sid=str(section_id)
    budgets={
      '1.1.2':450, '1.1.3':650, '1.2':900,
      '2':1800,
      '4.1.2':750, '4.1.3.2':900,
      '18.2':700,
      '26.1':1100, '26.2':550,
    }
    return budgets.get(sid,650)


def _estimate(job):
    """估算章节输出字符数，供 worker 负载均衡使用。"""
    return _section_char_budget(job.get('section_id'))


def _stable_json(value):
    """生成稳定 JSON 字符串，用于去重和比较。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _json_bytes(value):
    """估算 JSON 落盘字节数，辅助控制 worker pack 体积。"""
    return len(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def _path_depth(path):
    """计算 fact path 深度，父路径优先进入共享上下文仓库。"""
    return len([x for x in str(path).split('.') if x])


def _is_parent_path(parent, child):
    """判断 parent 是否为 child 的同一路径或父路径。"""
    parent=str(parent)
    child=str(child)
    return parent == child or child.startswith(parent + '.')


def _values_for_token(value, token):
    """解析上下文路径 token，支持 `[]` 数组展开。"""
    is_array=token.endswith('[]')
    key=token[:-2] if is_array else token
    if isinstance(value,dict):
        nxt=value.get(key)
    elif isinstance(value,list):
        nxt=[x.get(key) for x in value if isinstance(x,dict)]
    else:
        return []
    if is_array:
        if isinstance(nxt,list):
            return nxt
        return [] if nxt in (None,'') else [nxt]
    return [nxt] if not isinstance(nxt,list) else nxt


def _resolve_selector(value, selector):
    """在已存父路径值上解析 selector，避免重复存储相同大对象。"""
    if not selector:
        return value
    values=[value]
    for token in str(selector).split('.'):
        next_values=[]
        for item in values:
            next_values.extend(_values_for_token(item, token))
        values=[x for x in next_values if x not in (None,'',[],{})]
        if not values:
            return None
    return values if len(values)!=1 else values[0]


def _selector(parent_path, child_path):
    """从父路径和子路径得到相对 selector。"""
    if str(parent_path)==str(child_path):
        return ''
    return str(child_path)[len(str(parent_path))+1:]


def _job_context_entries(job):
    """提取一个章节任务中可共享的上下文条目。"""
    pack=_pack_for(job)
    entries=[]
    for scope in ('chapter_context','project_context'):
        ctx=pack.get(scope)
        if not (isinstance(ctx,dict) and isinstance(ctx.get('paths'),dict)):
            continue
        for path,value in ctx['paths'].items():
            entries.append({'scope':scope,'source_path':str(path),'value':value})
    return entries


def _find_context_item(items, path, value):
    """在共享上下文仓库中查找相同值或可覆盖该值的父路径。"""
    value_key=_stable_json(value)
    for item in items:
        if item['_value_key']==value_key:
            return item, 'same_value'
    for item in items:
        stored_path=item['stored_path']
        if not _is_parent_path(stored_path, path):
            continue
        selector=_selector(stored_path, path)
        if _resolve_selector(item['value'], selector)==value:
            return item, 'parent_path'
    return None, ''


def _build_context_store(section_jobs):
    """为一组章节构建去重后的共享上下文仓库和章节引用。"""
    all_entries=[]
    by_section={}
    for job in section_jobs:
        sid=str(job.get('section_id'))
        entries=_job_context_entries(job)
        by_section[sid]=entries
        all_entries.extend(entries)

    items=[]
    for entry in sorted(all_entries, key=lambda x: (_path_depth(x['source_path']), x['source_path'], _stable_json(x['value']))):
        item,_reason=_find_context_item(items, entry['source_path'], entry['value'])
        if item:
            if entry['source_path'] not in item['source_paths']:
                item['source_paths'].append(entry['source_path'])
            continue
        cid=f"ctx_{len(items)+1:03d}"
        items.append({
            'context_id':cid,
            'stored_path':entry['source_path'],
            'source_paths':[entry['source_path']],
            'value':entry['value'],
            '_value_key':_stable_json(entry['value']),
        })

    refs_by_section={}
    for sid,entries in by_section.items():
        refs=[]
        seen=set()
        for entry in entries:
            item,reason=_find_context_item(items, entry['source_path'], entry['value'])
            if not item:
                continue
            ref={
                'source_path':entry['source_path'],
                'context_id':item['context_id'],
                'stored_path':item['stored_path'],
                'scope':entry['scope'],
            }
            sel=_selector(item['stored_path'], entry['source_path']) if reason=='parent_path' else ''
            if sel:
                ref['selector']=sel
            key=_stable_json(ref)
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
        refs_by_section[sid]=refs

    clean_items=[]
    for item in items:
        clean={k:v for k,v in item.items() if not k.startswith('_')}
        clean['source_paths']=sorted(clean['source_paths'])
        clean_items.append(clean)
    return clean_items, refs_by_section


def _evidence_key(item):
    """生成 Evidence 去重键，优先使用 evidence_id。"""
    return str(item.get('evidence_id') or _stable_json(item))


def _build_evidence_store(section_jobs):
    """为 worker pack 构建共享 Evidence 仓库和章节引用。"""
    items=[]
    refs_by_section={}
    index={}
    for job in section_jobs:
        sid=str(job.get('section_id'))
        refs=[]
        for evidence in job.get('research_evidence') or []:
            key=_evidence_key(evidence)
            if key not in index:
                ev=dict(evidence)
                if not ev.get('evidence_id'):
                    ev['evidence_id']=f"EV-WORKER-{len(items)+1:03d}"
                index[key]=ev['evidence_id']
                items.append(ev)
            refs.append(index[key])
        refs_by_section[sid]=refs
    return items, refs_by_section


def _job_open_items(job):
    """收集章节上下文中的 open_items，供兼容旧调用点使用。"""
    items=[]
    seen=set()
    for scope in ('chapter_context','project_context'):
        ctx=job.get(scope) or {}
        for item in ctx.get('open_items') or []:
            key=_stable_json(item)
            if key not in seen:
                seen.add(key)
                items.append(item)
    return items


def _common_output_contract(project_id, section_ids):
    """生成 worker 输出契约，约束 draft JSON 形态。"""
    return {
        'file_shape':{
            'contract_version':'1.0',
            'project_id':project_id,
            'drafts':'array of draft objects for every section_id assigned to this worker',
        },
        'required_section_ids':section_ids,
        'draft_item':{
            'section_id':'must match one assigned section_id',
            'draft_status':'draft|partial|blocked|not_applicable',
            'subsections':[{'heading':'must come from that section allowed_headings','paragraphs':['formal feasibility-report prose']}],
            'source_evidence_ids':['evidence_id values from evidence_refs, only when external evidence is used'],
        },
    }


def _build_worker_pack(jobs_payload, worker_id, section_jobs):
    """构建单个 draft worker 的自包含上下文包。"""
    ordered=sorted(section_jobs, key=lambda j: str(j.get('_worker_order', '')))
    section_ids=[str(j.get('section_id')) for j in ordered]
    context_items,context_refs=_build_context_store(ordered)
    evidence_items,evidence_refs=_build_evidence_store(ordered)
    system_instruction=''
    for job in ordered:
        if job.get('system_instruction'):
            system_instruction=job.get('system_instruction')
            break
    sections=[]
    for job in ordered:
        sid=str(job.get('section_id'))
        sections.append({
            'section_id':sid,
            'title':job.get('title') or sid,
            'allowed_headings':job.get('allowed_headings') or [],
            'required_structure':job.get('required_structure') or [],
            'generation_rules':job.get('generation_rules') or {},
            'quality_checks':job.get('quality_checks') or [],
            'length_budget_chars':_section_char_budget(sid),
            'length_rule':'在信息完整前提下尽量精炼；正文原则上不超过 length_budget_chars，避免重复事实和套话。',
            'context_refs':context_refs.get(sid) or [],
            'evidence_refs':evidence_refs.get(sid) or [],
        })
    return {
        'contract_version':'1.0',
        'pack_type':'draft_worker',
        'worker_id':worker_id,
        'project_id':jobs_payload.get('project_id'),
        'system_instruction':system_instruction,
        'common_output_contract':_common_output_contract(jobs_payload.get('project_id'), section_ids),
        'shared_context':{'items':context_items},
        'shared_research_evidence':{'items':evidence_items},
        'sections':sections,
        'output_file':f'batch_{worker_id}.json',
    }


def _worker_load(jobs_payload, worker_id, jobs):
    """估算某 worker 当前任务包的总负载。"""
    pack=_build_worker_pack(jobs_payload, worker_id, jobs)
    return _json_bytes(pack)+sum(_estimate(j) for j in jobs)


def _job_ordered_copy(jobs):
    """给 jobs 增加稳定顺序键，保证分配结果可复现。"""
    copied=[]
    for i,job in enumerate(jobs):
        item=dict(job)
        item['_worker_order']=f'{i:06d}'
        copied.append(item)
    return copied


def plan_worker_batches(jobs_payload, worker_count=DEFAULT_WORKER_COUNT):
    """从 llm_jobs payload 生成确定性 worker 分配计划。"""
    jobs=_job_ordered_copy(jobs_payload.get('jobs') or [])
    if not jobs:
        return {
          'contract_version':'1.0',
          'plan_type':'draft_workers',
          'jobs_total':0,
          'defaults':{'worker_count':worker_count},
          'assignment_strategy':'greedy_by_slimmed_context_increment_plus_estimated_output',
          'batches':[],
        }
    count=max(1,min(int(worker_count or DEFAULT_WORKER_COUNT),len(jobs)))
    workers=[{'worker_id':f'worker_{i+1:02d}','jobs':[],'load':0} for i in range(count)]
    for job in sorted(jobs, key=lambda j: (-_worker_load(jobs_payload,'single',[j]), j['_worker_order'])):
        best=None
        for index,worker in enumerate(workers):
            candidate=worker['jobs']+[job]
            candidate_load=_worker_load(jobs_payload, worker['worker_id'], candidate)
            inc=candidate_load-worker['load']
            score=(candidate_load, inc, len(candidate), index)
            if best is None or score<best[0]:
                best=(score,index,candidate,candidate_load)
        _score,index,candidate,candidate_load=best
        workers[index]['jobs']=candidate
        workers[index]['load']=candidate_load

    batches=[]
    for worker in workers:
        section_jobs=sorted(worker['jobs'], key=lambda j: j['_worker_order'])
        section_ids=[str(j.get('section_id')) for j in section_jobs]
        if not section_ids:
            continue
        pack=_build_worker_pack(jobs_payload, worker['worker_id'], section_jobs)
        batches.append({
            'batch_id':worker['worker_id'],
            'worker_id':worker['worker_id'],
            'file':f"batch_{worker['worker_id']}.json",
            'context_pack':f"worker_contexts/{worker['worker_id']}.json",
            'section_ids':section_ids,
            'sections':len(section_ids),
            'est_chars':sum(_estimate(j) for j in section_jobs),
            'estimated_pack_bytes':_json_bytes(pack),
            'estimated_total_load':_json_bytes(pack)+sum(_estimate(j) for j in section_jobs),
        })
    return {
      'contract_version':'1.0',
      'plan_type':'draft_workers',
      'jobs_total':len(jobs),
      'defaults':{'worker_count':worker_count,'actual_workers':len(batches)},
      'assignment_strategy':'greedy_by_slimmed_context_increment_plus_estimated_output',
      'batches':batches,
    }


def plan_batches(jobs_payload, max_sections=MAX_SECTIONS_PER_BATCH, char_target=BATCH_CHAR_TARGET, char_min=BATCH_CHAR_MIN, workers=DEFAULT_WORKER_COUNT):
    """兼容入口：默认返回 worker batch 分配计划。"""
    return plan_worker_batches(jobs_payload, worker_count=workers)


def plan_section_batches(jobs_payload, max_sections=MAX_SECTIONS_PER_BATCH, char_target=BATCH_CHAR_TARGET, char_min=BATCH_CHAR_MIN):
    """旧版按大章节分批的计划函数，仅保留给兼容调用点。"""
    jobs=[j for j in (jobs_payload.get('jobs') or [])]
    def chapter(sid): return str(sid).split('.',1)[0]
    groups=[]
    for j in jobs:
        ch=chapter(j.get('section_id'))
        if groups and groups[-1][0]==ch: groups[-1][1].append(j)
        else: groups.append((ch,[j]))
    # 在章节数和字符预算内贪心合并相邻大章节。
    batches=[]
    for ch,items in groups:
        est=sum(_estimate(j) for j in items); count=len(items)
        if batches and batches[-1]['sections']+count<=max_sections and batches[-1]['est_chars']+est<=char_target:
            b=batches[-1]; b['sections']+=count; b['est_chars']+=est; b['section_ids'].extend(str(j.get('section_id')) for j in items)
        else:
            batches.append({'sections':count,'est_chars':est,'section_ids':[str(j.get('section_id')) for j in items]})
    # 超出上限的大章节组继续拆分。
    final=[]
    for b in batches:
        ids=b['section_ids']
        if b['sections']<=max_sections: final.append(ids); continue
        for i in range(0,len(ids),max_sections): final.append(ids[i:i+max_sections])
    # 后处理：预算允许时，将孤立单节并入相邻 batch。
    merged=[]
    for ids in final:
        if merged and (len(ids)==1 or sum(_estimate(j) for j in jobs if str(j.get('section_id')) in ids)<char_min):
            prev=merged[-1]
            if len(prev)+len(ids)<=max_sections: prev.extend(ids); continue
        merged.append(list(ids))
    return {
      'contract_version':'1.0',
      'jobs_total':len(jobs),
      'defaults':{'max_sections_per_batch':max_sections,'char_target':char_target,'char_min':char_min},
      'batches':[{'batch_id':f'batch_{i+1:02d}','file':f'batch_{i+1:02d}.json','section_ids':ids,'est_chars':sum(_estimate(j) for j in jobs if str(j.get('section_id')) in ids)} for i,ids in enumerate(merged)],
    }


PACK_SLIM_KEYS=('_meta','composition','streams','report_rows','solution_sets')
PACK_PATH_CHAR_LIMIT=48000


def _slim(value):
    """对宿主可见上下文做确定性瘦身。

    只移除正文不消费的结构性噪声：`_meta`、`composition`、`streams`、
    `report_rows`、`solution_sets`。不猜测、不摘要、不改写事实；完整值
    仍保留在 `llm_jobs.json` 中。
    """
    if isinstance(value,dict):
        return {k:_slim(v) for k,v in value.items() if k not in PACK_SLIM_KEYS}
    if isinstance(value,list):
        return [_slim(x) for x in value]
    return value


def _pack_for(j):
    """从单个 job 生成宿主可见 pack：深拷贝并瘦身上下文。"""
    import copy
    pack=copy.deepcopy(j)
    if pack.get('chapter_context') is pack.get('project_context'):
        pack['project_context']=copy.deepcopy(pack.get('project_context'))
    for ctx_key in ('chapter_context','project_context'):
        ctx=pack.get(ctx_key)
        if isinstance(ctx,dict) and isinstance(ctx.get('paths'),dict):
            slimmed={}
            for k,v in ctx['paths'].items():
                s=_slim(v)
                # 体积兜底：瘦身后仍超限的长数组按顺序截断，避免 pack 膨胀；
                # 未截断完整值仍保留在 llm_jobs.json。
                if len(json.dumps(s,ensure_ascii=False))>PACK_PATH_CHAR_LIMIT and isinstance(s,list):
                    s=s[:50]+[{'_truncated':True,'note':'列表已截断，完整数据见 llm_jobs.json 同章节'}]
                slimmed[k]=s
            ctx['paths']=slimmed
    # 去重：project_context 往往逐节重复 chapter_context 中的同值路径，
    # pack 内删除完全相同的项目级路径引用，避免 N 次重复携带。
    cc=pack.get('chapter_context'); pc=pack.get('project_context')
    if isinstance(cc,dict) and isinstance(pc,dict) and isinstance(cc.get('paths'),dict) and isinstance(pc.get('paths'),dict):
        pc['paths']={k:v for k,v in pc['paths'].items() if not (k in cc['paths'] and cc['paths'][k]==v)}
    return pack


def write_context_packs(jobs_payload, frag_dir):
    """旧版拆分：一个章节一个上下文包并生成 digest。

    当前主流程使用 worker pack；本函数保留给旧调用点。它不修改
    `llm_jobs.json`，只在 `draft_fragments/contexts/` 生成节级包。
    """
    fdir=Path(frag_dir); cdir=fdir/'contexts'; cdir.mkdir(parents=True,exist_ok=True)
    jobs=jobs_payload.get('jobs') or []
    lines=[
        '# LLM 章节任务摘要（digest）','',
        f'共 {len(jobs)} 个章节任务。每节自包含上下文包位于 `contexts/job_<section_id>.json`（section_id 中的 `.` 替换为 `_`），',
        '包内已含：系统指令、章节上下文（由确认后工程事实绑定，已剔除内部溯源与组分明细等正文不消费的字段）、研究证据全文、允许标题、必需结构、生成规则与输出契约。',
        '宿主或并行 subAgent 只读单个包即可撰写该节，无需读取 llm_jobs.json 或工程事实全量文件。','',
        '| section_id | 标题 | 估正文字符 | 证据条数 |',
        '|---|---|---|---|',
    ]
    for j in jobs:
        sid=str(j.get('section_id'))
        fname='job_'+sid.replace('.','_')+'.json'
        dump_json(_pack_for(j),cdir/fname)
        lines.append(f"| {sid} | {j.get('title') or ''} | {_estimate(j)} | {len(j.get('research_evidence') or [])} |")
    (fdir/'digest.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'contexts_dir':str(cdir),'digest':str(fdir/'digest.md'),'packs':len(jobs)}


def write_worker_packs(jobs_payload, frag_dir, worker_count=DEFAULT_WORKER_COUNT):
    """只写宿主需要的 worker pack，不生成冗余 digest/manifest 文件。"""
    fdir=Path(frag_dir); cdir=fdir/'worker_contexts'; cdir.mkdir(parents=True,exist_ok=True)
    plan=plan_worker_batches(jobs_payload, worker_count=worker_count)
    jobs_by_id={str(j.get('section_id')):dict(j, _worker_order=f'{i:06d}') for i,j in enumerate(jobs_payload.get('jobs') or [])}
    total_bytes=0
    for batch in plan['batches']:
        section_jobs=[jobs_by_id[sid] for sid in batch['section_ids']]
        pack=_build_worker_pack(jobs_payload,batch['worker_id'],section_jobs)
        path=cdir/f"{batch['worker_id']}.json"
        dump_json(pack,path)
        pack_bytes=path.stat().st_size
        total_bytes+= pack_bytes
        batch['pack_bytes']=pack_bytes
        batch['context_pack']=f"worker_contexts/{batch['worker_id']}.json"
        batch['file']=f"batch_{batch['worker_id']}.json"
    plan['total_pack_bytes']=total_bytes
    plan['max_pack_bytes']=max((b.get('pack_bytes',0) for b in plan['batches']), default=0)
    return {
        'contexts_dir':str(cdir),
        'packs':len(plan['batches']),
        'workers':len(plan['batches']),
        'total_pack_bytes':total_bytes,
        'max_pack_bytes':plan['max_pack_bytes'],
        'manifest_data':plan,
    }


def write_host_workflow(jobs_payload, frag_dir, output_dir, evidence_path, skill_root, worker_manifest=None):
    """写入宿主并行 draft worker 执行说明，作为 needs_llm 后的唯一入口。"""
    fdir=Path(frag_dir); fdir.mkdir(parents=True,exist_ok=True)
    jobs=jobs_payload.get('jobs') or []
    outdir=Path(output_dir)
    manifest=worker_manifest or plan_worker_batches(jobs_payload)
    batches=manifest.get('batches') or []
    L=[
      '# Draft 执行入口（严格执行）','',
      f'共 {len(jobs)} 个 LLM 章节，固定为 {len(batches)} 个 worker。不要扫描输出目录，不要读取 llm_jobs.json / project_facts.json。','',
      '1. **一次性并行派发全部 worker**。每个 subAgent 只读取自己的 worker pack；生成对应 batch JSON；然后执行一次 submit。',
      '2. worker pack 内 `length_budget_chars` 是正文长度上限目标：优先精炼，不重复表格数字，不输出 claims/open_items。',
      '3. submit 部分失败时只重写 `retry_sections`，已通过章节不得返工。',
      '4. 全部 worker 完成后只运行一次 collect；collect exit 0 后立即重新调用 chapter_planning Tool，传入 collect 产物 section_drafts.json，再进入最终报告生成，中间不总结、不重新规划。','',
      '## Worker','```'
    ]
    for b in batches:
        L.append(f"{b['worker_id']}: {b['context_pack']} -> {b['file']} ; sections={', '.join(b['section_ids'])}")
    L += ['```','',
      '## submit（每 worker 一次）','```',
      f'python "{skill_root}\\internal\\planning\\draft_fragments.py" --output-dir "{outdir}" --fragments-dir "{fdir}" --submit "{outdir}\\draft_fragments\\batch_<worker_id>.json"',
      '```','',
      '## collect（全部 worker 后一次）','```',
      f'python "{skill_root}\\internal\\planning\\draft_fragments.py" --output-dir "{outdir}" --fragments-dir "{fdir}" --output "{outdir}\\section_drafts.json"',
      '```','',
      f'collect exit 0 后立即重新调用 `chapter_planning` Tool，并设置 `section_drafts="{outdir}\\section_drafts.json"`。'
    ]
    wf=fdir/'HOST_WORKFLOW.md'; wf.write_text('\n'.join(L)+'\n',encoding='utf-8')
    return str(wf)


def _fragment_entries(path):
    """读取一个草稿分片并返回 drafts 列表。

    兼容裸数组、`{"drafts": [...]}`、单个 draft object 三种形态；
    异常信息携带文件名，便于宿主定位失败分片。
    """
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception as e:
        raise ValueError(f'{path.name}: invalid JSON ({e})')
    if isinstance(data,dict):
        entries=data.get('drafts')
        if not isinstance(entries,list) and 'section_id' in data:
            entries=[data]
    else:
        entries=data
    if not isinstance(entries,list):
        raise ValueError(f'{path.name}: expected a JSON array of drafts, {{"drafts": [...]}} or a single draft object')
    return entries


def validate_fragment(jobs_payload, evidence, path, mode='production'):
    """只校验单个草稿/分片文件，不落盘、不合并。

    使用与 collect 相同的 production guard 和 validate_draft_entries 规则；
    返回 `(result_dict, exit_code)`。
    """
    p=Path(path)
    try: entries=_fragment_entries(p)
    except ValueError as e: return {'status':'invalid','issues':[str(e)],'file':str(p)}, 2
    try: production_guard({'drafts':entries},'section_drafts',mode)
    except Exception as e: return {'status':'invalid','issues':[f'{p.name}: {e}'],'file':str(p)}, 2
    f_issues,_=validate_draft_entries(jobs_payload,evidence,entries,mode)
    if f_issues: return {'status':'invalid','issues':[f'{p.name}: {x}' for x in f_issues],'file':str(p)}, 2
    return {'status':'valid','issues':[],'file':str(p),'sections':[str(d.get('section_id')) for d in entries]}, 0


def submit(fragments_dir, jobs_payload, evidence, path, mode='production'):
    """按章节校验 worker batch，并持久化每个通过的草稿。

    部分失败时不丢弃同 batch 中已通过章节。命令仍返回 2 提醒修复，
    但 `accepted_sections` 已落盘且不得重写；`retry_sections` 只列出
    需要修正的章节。
    """
    p=Path(path)
    try:
        entries=_fragment_entries(p)
    except ValueError as e:
        return {'status':'invalid','issues':[str(e)],'file':str(p),'accepted_sections':[],'retry_sections':[]},2
    try:
        production_guard({'drafts':entries},'section_drafts',mode)
    except Exception as e:
        return {'status':'invalid','issues':[f'{p.name}: {e}'],'file':str(p),'accepted_sections':[],'retry_sections':[str(d.get('section_id')) for d in entries]},2

    fdir=Path(fragments_dir); vdir=fdir/'validated'
    accepted=[]; retry=[]; issues=[]
    seen=set()
    for d in entries:
        sid=str(d.get('section_id'))
        if sid in seen:
            retry.append(sid); issues.append(f'{p.name}: duplicate section draft in worker batch: {sid}'); continue
        seen.add(sid)
        entry_issues,_=validate_draft_entries(jobs_payload,evidence,[d],mode)
        if entry_issues:
            retry.append(sid)
            issues.extend(f'{p.name}: {x}' for x in entry_issues)
            continue
        vdir.mkdir(parents=True,exist_ok=True)
        dump_json(d,vdir/f"{sid.replace('.','_')}.json")
        accepted.append(sid)

    if retry:
        return {
            'status':'partial_accept',
            'issues':issues,
            'file':str(p),
            'accepted_sections':accepted,
            'retry_sections':retry,
            'validated_dir':str(vdir),
            'next_action':'已通过章节已入库，禁止重写；只修 retry_sections，写成一个仅包含失败章节的新 JSON 后再次 --submit。',
        },2
    return {'status':'accepted','issues':[],'file':str(p),'submitted':accepted,'accepted_sections':accepted,'retry_sections':[],'validated_dir':str(vdir)},0

def collect(fragments_dir, jobs_payload, evidence, mode='production'):
    """合并分片、执行分片校验和覆盖检查。

    raw fragments 与 validated/ 两条输入通道使用同一套校验规则；
    同一章节同时存在时，submitted 版本优先，原始分片记录到 `shadowed`。
    返回 `(result_dict, exit_code)`，返回码见模块说明。
    """
    fdir=Path(fragments_dir)
    issues=[]; collected={}; docs={}; files=[]; shadowed=[]
    if not fdir.is_dir():
        return {'status':'invalid','issues':[f'fragments dir not found: {fdir}'],'fragments':[]}, 2
    for p in sorted(fdir.glob('*.json')):
        if p.name=='batch_plan.json' or p.name=='collect_status.json': continue
        files.append(p)
        try: entries=_fragment_entries(p)
        except ValueError as e: issues.append(str(e)); continue
        try: production_guard({'drafts':entries},'section_drafts',mode)
        except Exception as e: issues.append(f'{p.name}: {e}'); continue
        f_issues,_=validate_draft_entries(jobs_payload,evidence,entries,mode)
        issues.extend(f'{p.name}: {x}' for x in f_issues)
        for d in entries:
            sid=str(d.get('section_id'))
            if sid in collected: issues.append(f'{p.name}: duplicate section {sid} already submitted in {collected[sid]}')
            else: collected[sid]=p.name; docs[sid]=d
    vdir=fdir/'validated'
    if vdir.is_dir():
        for p in sorted(vdir.glob('*.json')):
            files.append(p)
            try: d=json.loads(p.read_text(encoding='utf-8-sig'))
            except Exception as e: issues.append(f'{p.name}: invalid JSON ({e})'); continue
            if not isinstance(d,dict):
                issues.append(f'{p.name}: expected a single draft object'); continue
            try: production_guard({'drafts':[d]},'section_drafts',mode)
            except Exception as e: issues.append(f'{p.name}: {e}'); continue
            sid=str(d.get('section_id'))
            if sid in collected: shadowed.append({'section_id':sid,'fragment':collected[sid],'replaced_by':p.name})
            docs[sid]=d; collected[sid]=p.name
    job_ids=[str(j.get('section_id')) for j in jobs_payload.get('jobs',[])]
    missing=[sid for sid in job_ids if sid not in collected]
    if issues:
        return {'status':'invalid','issues':issues,'fragments':[p.name for p in files],'covered':sorted(collected),'shadowed':shadowed}, 2
    if missing:
        return {'status':'partial','issues':[],'fragments':[p.name for p in files],'covered':sorted(collected),'missing':missing,'shadowed':shadowed,
                'next_action':'分片目前合法但覆盖不全：继续通过 --submit 提交缺失章节（或写入新分片）后重新运行收集器'}, 4
    ordered=[docs[sid] for sid in job_ids]
    doc={'contract_version':'1.0','project_id':jobs_payload.get('project_id'),'drafts':ordered}
    if mode=='test': doc['test_only']=True
    result,_=validate(jobs_payload,evidence,doc,mode)
    if not result.get('valid'):
        return {'status':'invalid','issues':[f'merged document failed final gate: {x}' for x in result.get('issues',[])],'fragments':[p.name for p in files],'covered':sorted(collected),'shadowed':shadowed}, 2
    return {'status':'complete','issues':[],'fragments':[p.name for p in files],'covered':sorted(collected),'missing':[],'shadowed':shadowed,'draft_count':len(ordered),'document':doc}, 0


def main():
    ap=argparse.ArgumentParser(description='章节草稿 worker 分片：计划、校验、提交与收集。')
    ap.add_argument('--jobs'); ap.add_argument('--evidence'); ap.add_argument('--fragments-dir'); ap.add_argument('--output')
    ap.add_argument('--output-dir',help='便捷参数：从 run_summary.json 自动定位 llm_jobs/research_evidence')
    ap.add_argument('--mode',choices=['production','test'],default='production')
    ap.add_argument('--plan',action='store_true',help='只生成 worker 分批计划（不收集）')
    ap.add_argument('--validate-fragment',help='只校验单个草稿/分片文件（不落盘、不合并），通过返回 0')
    ap.add_argument('--submit',help='校验并将单个草稿或 worker 分片提交到 draft_fragments/validated/')
    ap.add_argument('--workers',type=int,default=DEFAULT_WORKER_COUNT,help='worker 包数量上限，默认 3，任务少于该值时不生成空包')
    ap.add_argument('--max-sections-per-batch',type=int,default=MAX_SECTIONS_PER_BATCH)
    ap.add_argument('--char-target',type=int,default=BATCH_CHAR_TARGET)
    a=ap.parse_args()

    if a.output_dir and (not a.jobs or not a.evidence):
        outdir=Path(a.output_dir)
        rs=outdir/'run_summary.json'
        data=json.loads(rs.read_text(encoding='utf-8-sig')) if rs.exists() else {}
        def _locate(explicit, summary_key, filename):
            if explicit: return explicit
            v=data.get(summary_key)
            if v: return v
            # run_summary.json is overwritten by later stages; fall back to the conventional filename.
            p=outdir/filename
            return str(p) if p.exists() else None
        jobs_path=_locate(a.jobs,'llm_jobs','llm_jobs.json')
        evidence_path=_locate(a.evidence,'research_evidence','research_evidence.json')
    else:
        jobs_path=a.jobs; evidence_path=a.evidence

    if a.plan:
        if not jobs_path: ap.error('--plan 需要 --jobs 或 --output-dir')
        payload=json.loads(Path(jobs_path).read_text(encoding='utf-8-sig'))
        plan=plan_batches(payload,max_sections=a.max_sections_per_batch,char_target=a.char_target,workers=a.workers)
        out=Path(a.output) if a.output else (Path(a.fragments_dir)/'batch_plan.json' if a.fragments_dir else None)
        emit_result(out,plan); return 0

    if a.submit or a.validate_fragment:
        if not (jobs_path and evidence_path): ap.error('--submit/--validate-fragment 需要 --jobs --evidence（或 --output-dir 自动定位）')
        jobs_payload=json.loads(Path(jobs_path).read_text(encoding='utf-8-sig'))
        evidence=json.loads(Path(evidence_path).read_text(encoding='utf-8-sig'))
        if a.submit:
            if not a.fragments_dir: ap.error('--submit 需要 --fragments-dir')
            result,code=submit(a.fragments_dir,jobs_payload,evidence,a.submit,a.mode)
        else:
            result,code=validate_fragment(jobs_payload,evidence,a.validate_fragment,a.mode)
        emit_result(None,result); return code

    if not (jobs_path and evidence_path and a.fragments_dir): ap.error('collect 模式需要 --jobs --evidence --fragments-dir（或 --output-dir 自动定位）与 --output')
    jobs_payload=json.loads(Path(jobs_path).read_text(encoding='utf-8-sig'))
    evidence=json.loads(Path(evidence_path).read_text(encoding='utf-8-sig'))
    result,code=collect(a.fragments_dir,jobs_payload,evidence,a.mode)
    doc=result.pop('document',None)
    if doc is not None and a.output:
        dump_json(doc,a.output); result['section_drafts']=str(Path(a.output).resolve())
    dump_json(result,Path(a.fragments_dir)/'collect_status.json')
    emit_result(None,result)
    return code
if __name__=='__main__': raise SystemExit(main())
