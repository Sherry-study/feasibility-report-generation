#!/usr/bin/env python3
"""Incremental section-draft submission: worker packs + fragment collection.

Why: the host LLM's single-turn output (thinking + one Write tool call) has a hard size
ceiling; a complete section_drafts.json for a full report often exceeds it and the tool
call gets truncated mid-JSON. The pipeline contract, however, only needs one complete
valid file. This module assembles that file from worker batches with fail-fast
validation, without touching any stage logic, exit-code contract or schema. The
full-file path (--section-drafts) remains the primary interface; fragments are an
optional assembly aid.

Worker sizing (deterministic, no LLM): default to 3 non-empty worker packs (or fewer
when fewer jobs exist). Jobs are assigned by greedy load balancing on slimmed context
incremental bytes plus estimated output chars. A section remains the validation atom,
but a worker submits one batch_worker_XX.json containing all drafts it owns.

CLI (also usable standalone by the host):
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

Fragment file format (draft_fragments/batch_worker_01.json, legacy batch_01.json ...):
either a bare JSON array of draft objects, {"drafts": [...]}, or a single draft object.
Any *.json in the directory is collected (sorted by name); batch_plan.json itself is
skipped. The worker plan is advisory guidance for the host, not enforced at collect time.

Submission channel (parallel sub-agent drafting): --submit validates one draft or a
worker batch immediately and persists each accepted section to
draft_fragments/validated/<section_id>.json. Validation is the same
validate_draft_entries gate collect uses; accepted files are not re-validated at
collect time (only the final merged-document gate covers the assembled whole).
One file per section_id means concurrent submitters share no mutable state — no
locks needed. A section present both as a raw fragment and under validated/ is
resolved in favour of the submitted one and reported in `shadowed`.

Exit codes (local to this utility, disjoint from pipeline stage codes):
  0  complete: every job section covered, merged+validated section_drafts.json written
     (check/submit: the file passed the same hard validation gate)
  2  invalid:  one or more fragments failed hard validation (issue names file+section)
  4  partial: fragments valid so far but coverage incomplete (missing list reported;
               keep writing the remaining batches and rerun)
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
    """Target Chinese prose budget per section; keeps worker generations concise."""
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
    """Estimated output chars used for worker load balancing."""
    return _section_char_budget(job.get('section_id'))


def _stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def _path_depth(path):
    return len([x for x in str(path).split('.') if x])


def _is_parent_path(parent, child):
    parent=str(parent)
    child=str(child)
    return parent == child or child.startswith(parent + '.')


def _values_for_token(value, token):
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
    if str(parent_path)==str(child_path):
        return ''
    return str(child_path)[len(str(parent_path))+1:]


def _job_context_entries(job):
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
    return str(item.get('evidence_id') or _stable_json(item))


def _build_evidence_store(section_jobs):
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
    pack=_build_worker_pack(jobs_payload, worker_id, jobs)
    return _json_bytes(pack)+sum(_estimate(j) for j in jobs)


def _job_ordered_copy(jobs):
    copied=[]
    for i,job in enumerate(jobs):
        item=dict(job)
        item['_worker_order']=f'{i:06d}'
        copied.append(item)
    return copied


def plan_worker_batches(jobs_payload, worker_count=DEFAULT_WORKER_COUNT):
    """Deterministic worker plan from an llm_jobs payload. Pure function."""
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
    """Compatibility wrapper: default planning now returns worker batches."""
    return plan_worker_batches(jobs_payload, worker_count=workers)


def plan_section_batches(jobs_payload, max_sections=MAX_SECTIONS_PER_BATCH, char_target=BATCH_CHAR_TARGET, char_min=BATCH_CHAR_MIN):
    """Legacy top-level-chapter batch plan. Kept for callers that need old guidance."""
    jobs=[j for j in (jobs_payload.get('jobs') or [])]
    def chapter(sid): return str(sid).split('.',1)[0]
    groups=[]
    for j in jobs:
        ch=chapter(j.get('section_id'))
        if groups and groups[-1][0]==ch: groups[-1][1].append(j)
        else: groups.append((ch,[j]))
    # Greedy merge of adjacent chapter groups under the size/section ceilings.
    batches=[]
    for ch,items in groups:
        est=sum(_estimate(j) for j in items); count=len(items)
        if batches and batches[-1]['sections']+count<=max_sections and batches[-1]['est_chars']+est<=char_target:
            b=batches[-1]; b['sections']+=count; b['est_chars']+=est; b['section_ids'].extend(str(j.get('section_id')) for j in items)
        else:
            batches.append({'sections':count,'est_chars':est,'section_ids':[str(j.get('section_id')) for j in items]})
    # Split any oversized batch (a chapter group larger than the ceiling).
    final=[]
    for b in batches:
        ids=b['section_ids']
        if b['sections']<=max_sections: final.append(ids); continue
        for i in range(0,len(ids),max_sections): final.append(ids[i:i+max_sections])
    # Post-pass: absorb lone-section batches into a neighbour when ceilings allow.
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
    """Deterministic slimming of a bound context value for host-facing packs.

    Drops keys that never feed report prose: `_meta` (internal provenance,
    forbidden in body text anyway), `composition` (per-stream component detail
    rendered by the table layer, not narrative input), `streams` (the full
    intermediate-stream register; narrative sections consume external feeds /
    product streams / equipment keys, and stream-level numbers reach the report
    through tables and topology paragraphs), `report_rows` (table-layer rows
    rendered by report_tables from the facts directly) and `solution_sets`
    (algorithm-internal iteration states the Hard Rules exclude from prose).
    No guessing, no summarizing — only removal of structurally identified
    noise; the untrimmed values remain in llm_jobs.json.
    """
    if isinstance(value,dict):
        return {k:_slim(v) for k,v in value.items() if k not in PACK_SLIM_KEYS}
    if isinstance(value,list):
        return [_slim(x) for x in value]
    return value


def _pack_for(j):
    """Build the host-facing pack from a job: deep-copied, context-slimmed."""
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
                # Size backstop: a path still exceeding the limit after slimming gets
                # its long arrays truncated (keeping order) rather than silently
                # ballooning the pack; untrimmed values remain in llm_jobs.json.
                if len(json.dumps(s,ensure_ascii=False))>PACK_PATH_CHAR_LIMIT and isinstance(s,list):
                    s=s[:50]+[{'_truncated':True,'note':'列表已截断，完整数据见 llm_jobs.json 同章节'}]
                slimmed[k]=s
            ctx['paths']=slimmed
    # Dedupe: project_context repeats the chapter-level bindings verbatim in every
    # job (it is what makes llm_jobs.json carry the same payload N times). In the
    # pack, a project path identical to its chapter-level twin is dropped.
    cc=pack.get('chapter_context'); pc=pack.get('project_context')
    if isinstance(cc,dict) and isinstance(pc,dict) and isinstance(cc.get('paths'),dict) and isinstance(pc.get('paths'),dict):
        pc['paths']={k:v for k,v in pc['paths'].items() if not (k in cc['paths'] and cc['paths'][k]==v)}
    return pack


def write_context_packs(jobs_payload, frag_dir):
    """Legacy split: llm_jobs -> one self-contained context pack per section + digest.

    Why: the host LLM drafting one section only needs ~2% of llm_jobs.json /
    confirmed_project_facts.json; making each agent read the full files costs
    context budget and blocks parallel sub-agent drafting. Each pack
    (draft_fragments/contexts/job_<section_id>.json, '.' replaced by '_') embeds
    the system instruction, the facts-bound chapter context (slimmed: internal
    provenance and per-stream composition detail are stripped — the narrative
    never consumes them), research evidence, allowed headings, required
    structure and the output contract. draft_fragments/digest.md lists one line
    per section for batch planning without parsing the full jobs file.
    llm_jobs.json itself is left untouched (contract artifact, full fidelity).

    Pure function; caller (stage 3, on needs_llm exit) owns persistence order.
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
    """Write only the worker packs needed by the host; no redundant digest/manifest files."""
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
    """Single host entry for parallel draft workers; intentionally terse."""
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
      '4. 全部 worker 完成后只运行一次 collect；collect exit 0 后立即携带 section_drafts.json 重跑 Skill 到最终报告，中间不总结、不重新规划。','',
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
      f'collect exit 0 后立即用 `--section-drafts "{outdir}\\section_drafts.json"` 重跑原 Skill 命令。'
    ]
    wf=fdir/'HOST_WORKFLOW.md'; wf.write_text('\n'.join(L)+'\n',encoding='utf-8')
    return str(wf)


def _fragment_entries(path):
    """Load one fragment file -> drafts list. Raises ValueError with file context.

    Accepts a bare JSON array of drafts, {"drafts": [...]}, or a single draft
    object ({"section_id": ...}) — the last form is the natural output of a
    per-section submission.
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
    """Validate a single draft/fragment file. No state change, no merging.

    Same gate collect applies per fragment (production guard + validate_draft_entries).
    Returns (result_dict, exit_code): 0 valid, 2 invalid (issues name the file).
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
    """Validate a worker batch section-by-section and persist every passing draft.

    V0.14.8 changes failure semantics only, not the validation gate: one bad section
    no longer discards 4-6 already-good sections from the same worker batch. Each
    entry is checked by the same production guard + validate_draft_entries rules.
    The command returns exit 2 when any section fails, but `accepted_sections` have
    already been persisted and must NOT be regenerated; `retry_sections` names only
    the drafts that need repair.
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
    """Merge fragments -> per-fragment validation -> coverage check.

    Two input channels, same validation gate:
      * raw fragments (draft_fragments/*.json): validated here, per file;
      * per-section submissions (draft_fragments/validated/<sid>.json): already
        passed the identical gate at --submit time, so they are only re-checked
        by the production guard here; the final merged-document gate covers the
        assembled whole. When a section exists in both channels, the submitted
        version wins and the shadowed raw fragment is reported.
    A missing section can be delivered through either channel.

    Returns (result_dict, exit_code). exit 0/2/4 as documented in the module docstring.
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
    ap=argparse.ArgumentParser(description='Incremental section-draft worker batches: plan + collect.')
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
