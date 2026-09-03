#!/usr/bin/env python3
"""阶段③：章节规划、缺口分析、Research 与 LLM 草稿路由。

本模块是 `chapter_planning` Tool 的进程内实现。它镜像 Tool 契约：
确认后的事实基线 -> 章节规划 -> 缺口分析 -> 编制信息一次性提问
-> Research 任务 -> `needs_research` / `needs_llm` 宿主路由
-> Evidence 校验 -> LLM 章节任务 -> 草稿校验。

这里不启动 workflow runner，也不改写章节正文；它只告诉宿主 Agent
下一步应该调用哪个 Tool 或执行哪个分片收集辅助脚本。
"""
from __future__ import annotations
import time
from pathlib import Path

from internal.common import EXIT_GENERATED, EXIT_NEEDS_LLM, EXIT_NEEDS_RESEARCH, EXIT_NEEDS_USER_INPUT, REFERENCES_ROOT, SKILL_ROOT, data_digest, dump_json, file_digest, load_data
from internal.planning.chapters import decide
from internal.planning.gaps import analyze as analyze_gaps
from internal.planning.research_tasks import build_tasks
from internal.planning.llm_jobs import build_jobs, compact_jobs_for_validation
from internal.planning.draft_fragments import write_host_workflow, write_worker_packs
from internal.planning.research_fragments import write_research_packs, write_research_host_workflow
from internal.planning.validate_evidence import validate as validate_evidence
from internal.report.validate_drafts import validate as validate_drafts


PLANNING_DEPENDENCIES=(
    SKILL_ROOT/'internal/domain.py',
    SKILL_ROOT/'internal/planning/chapters.py',
    SKILL_ROOT/'internal/planning/gaps.py',
    SKILL_ROOT/'internal/planning/research_tasks.py',
    SKILL_ROOT/'internal/planning/chapter_rules.py',
)


def _registry_project_id(out):
    """从解析登记文件中取 project_id；缺失时由调用方再降级到 profile/facts。"""
    try:
        reg=load_data(out/'_resolved'/'source_registry.json')
        return (reg.get('project') or {}).get('project_id')
    except Exception:
        return None


def _rule_set_digest():
    items=[]
    for path in sorted((REFERENCES_ROOT/'chapter_rules').glob('*.yaml')):
        items.append({'path':path.name,'sha256':file_digest(path)})
    return data_digest(items)


def _planning_dependency_digest():
    return data_digest([{'path':str(path.relative_to(SKILL_ROOT)).replace('\\','/'),'sha256':file_digest(path)} for path in PLANNING_DEPENDENCIES])


def _planning_fingerprint(profile_data, facts_data, args):
    return {
        'confirmed_project_facts_sha256':data_digest(facts_data),
        'project_profile_sha256':data_digest(profile_data),
        'chapter_rules_sha256':_rule_set_digest(),
        'planning_dependencies_sha256':_planning_dependency_digest(),
        'ai_mode':getattr(args,'ai_mode',None),
        'run_mode':getattr(args,'run_mode',None),
        'skip_user_inputs':bool(getattr(args,'skip_user_inputs',False)),
    }


def _artifact_digest(path):
    p=Path(path)
    if not p.is_file():
        return None
    try:
        return data_digest(load_data(p))
    except Exception:
        return None


def _load_valid_planning_snapshot(snapshot_path, fingerprint):
    try:
        snap=load_data(snapshot_path)
    except Exception:
        return None
    if snap.get('fingerprint')!=fingerprint:
        return None
    artifacts=snap.get('artifacts') or {}
    for key in ('chapter_plan','gap_analysis','research_tasks'):
        item=artifacts.get(key) or {}
        path=item.get('path')
        if not path or _artifact_digest(path)!=item.get('sha256'):
            return None
    return {
        'chapter_plan_path':Path(artifacts['chapter_plan']['path']),
        'chapter_plan':load_data(artifacts['chapter_plan']['path']),
        'gap_analysis_path':Path(artifacts['gap_analysis']['path']),
        'gap_analysis':load_data(artifacts['gap_analysis']['path']),
        'research_tasks_path':Path(artifacts['research_tasks']['path']),
        'research_tasks':load_data(artifacts['research_tasks']['path']),
    }


def _write_planning_snapshot(snapshot_path, fingerprint, plan_path, gaps_path, research_tasks_path):
    dump_json({
        'contract_version':'1.0',
        'fingerprint':fingerprint,
        'artifacts':{
            'chapter_plan':{'path':str(plan_path),'sha256':_artifact_digest(plan_path)},
            'gap_analysis':{'path':str(gaps_path),'sha256':_artifact_digest(gaps_path)},
            'research_tasks':{'path':str(research_tasks_path),'sha256':_artifact_digest(research_tasks_path)},
        },
    }, snapshot_path)


def _worker_performance(worker_manifest):
    if not worker_manifest:
        return {}
    batches=worker_manifest.get('batches') or []
    return {
        'worker_count':len(batches),
        'worker_total_pack_bytes':worker_manifest.get('worker_total_pack_bytes',worker_manifest.get('total_pack_bytes')),
        'worker_max_pack_bytes':worker_manifest.get('worker_max_pack_bytes',worker_manifest.get('max_pack_bytes')),
        'workers':[{
            'worker_id':b.get('worker_id'),
            'section_ids':b.get('section_ids') or [],
            'sections':b.get('sections'),
            'pack_bytes':b.get('pack_bytes'),
            'output_budget_chars':b.get('output_budget_chars'),
            'allowed_heading_count':b.get('allowed_heading_count'),
            'requirement_count':b.get('requirement_count'),
            'section_complexity_weight':b.get('section_complexity_weight'),
            'estimated_load':b.get('estimated_load'),
        } for b in batches],
        'max_to_min_load_ratio':worker_manifest.get('max_to_min_load_ratio'),
    }


def _worker_performance_from_summary(perf):
    if not (isinstance(perf,dict) and perf.get('draft_worker_metrics_available') and perf.get('workers')):
        return None
    return {
        'worker_count':perf.get('worker_count'),
        'worker_total_pack_bytes':perf.get('worker_total_pack_bytes'),
        'worker_max_pack_bytes':perf.get('worker_max_pack_bytes'),
        'workers':perf.get('workers') or [],
        'max_to_min_load_ratio':perf.get('max_to_min_load_ratio'),
    }


def _load_current_worker_performance(out, fingerprint):
    """只复用同一 planning fingerprint 下已落盘的 worker 指标。"""
    path=out/'performance_summary.json'
    expected=data_digest(fingerprint)
    try:
        perf=load_data(path)
    except Exception:
        return None
    if perf.get('planning_fingerprint_sha256')!=expected:
        return None
    return _worker_performance_from_summary(perf)


def _finalize_summary(out, summary, start_time, planning_cache_hit, worker_manifest=None, worker_metrics=None, planning_fingerprint=None):
    perf={
        'planning_cache_hit':bool(planning_cache_hit),
        'planning_elapsed_ms':int((time.perf_counter()-start_time)*1000),
        'draft_worker_metrics_available':bool(worker_manifest or worker_metrics),
    }
    if planning_fingerprint is not None:
        perf['planning_fingerprint_sha256']=data_digest(planning_fingerprint)
    if worker_manifest:
        perf.update(_worker_performance(worker_manifest))
    elif worker_metrics:
        perf.update(worker_metrics)
    summary.update(perf)
    try:
        path=out/'performance_summary.json'
        dump_json(perf,path)
        summary['performance_summary']=str(path)
    except Exception as e:
        summary['performance_summary_error']=str(e)
    return summary


def run_chapter_planning_stage(args, output_dir):
    """执行阶段③，返回 (exit_code, summary_dict)，不直接打印结果。"""
    start_time=time.perf_counter()
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    facts_path=Path(args.facts).resolve(); profile_path=Path(args.profile).resolve()
    profile_data=load_data(profile_path)
    facts_data=load_data(facts_path)
    plan_path=out/'chapter_plan.json'
    gaps=out/'gap_analysis.json'
    research_tasks=out/'research_tasks.json'
    snapshot_path=out/'planning_snapshot.json'
    fingerprint=_planning_fingerprint(profile_data,facts_data,args)
    cached=_load_valid_planning_snapshot(snapshot_path,fingerprint)
    planning_cache_hit=bool(cached)
    if cached:
        plan_path=cached['chapter_plan_path']
        plan_data=cached['chapter_plan']
        gaps=cached['gap_analysis_path']
        gapdata=cached['gap_analysis']
        research_tasks=cached['research_tasks_path']
        tasks_payload=cached['research_tasks']
    else:
        # 章节规划在确认 Gate 之后执行：decide 只消费 project_profile（层级/类型/变更边界），
        # 确定性生成，不依赖确认前事实，因此确认后生成即最终版本。
        plan_data=decide(profile_data); dump_json(plan_data,plan_path)
        # 缺口分析必须基于确认后事实，保证 Research/LLM 路由看到的是冻结基线。
        gapdata=analyze_gaps(facts_data,plan_data); dump_json(gapdata,gaps)
        tasks_payload=build_tasks(profile_data,plan_data,facts_data); dump_json(tasks_payload,research_tasks)
        _write_planning_snapshot(snapshot_path,fingerprint,plan_path,gaps,research_tasks)
    blocked_sections=gapdata.get('blocked_sections') or []

    # 编制信息一次性提问（确认 Gate 之后）：项目名称/建设单位/年运行时长等
    # 仍缺失时在此一次性收集，不再于阶段①前置打断。
    deferred=gapdata.get('deferred_questions') or []
    if deferred and not getattr(args,'skip_user_inputs',False):
        summary={'status':'needs_user_input','resume_exit_code':EXIT_NEEDS_USER_INPUT,'facts':str(facts_path),'chapter_plan':str(plan_path),'gap_analysis':str(gaps),'user_questions':deferred,'next_action':'编制信息一次性提问：如需补充项目名称、建设单位、年运行时长等信息，先通过 engineering_facts Tool 的 user_inputs 或对应显式参数重新生成事实，并重新完成 engineering_confirmation 后再调用 chapter_planning；若决定暂缺并保留缺口，可再次调用 chapter_planning Tool 并设置 skip_user_inputs=true。'}
        return EXIT_NEEDS_USER_INPUT, _finalize_summary(out,summary,start_time,planning_cache_hit,planning_fingerprint=fingerprint)

    tasks=tasks_payload.get('tasks') or []
    evidence=Path(args.research_evidence).resolve() if args.research_evidence else None
    # 轻量 Research 缓存：同一输出目录存在且通过当前任务集校验时才复用。
    # 这只减少重复验证/调试时的联网工作，不影响首次运行路径。
    cache_candidate=out/'research_evidence.json'
    research_cache_hit=False
    if evidence is None and tasks and cache_candidate.is_file():
        try:
            cached_payload=load_data(cache_candidate)
            cached_result,cached_code=validate_evidence(tasks_payload,cached_payload,args.run_mode)
            if cached_code==0:
                evidence=cache_candidate
                research_cache_hit=True
                dump_json(cached_result,out/'research_cache_validation.json')
        except Exception:
            # 无效或过期缓存直接忽略，不能让缓存问题阻断正常 Research 路由。
            evidence=None
    drafts=Path(args.section_drafts).resolve() if args.section_drafts else None
    llm_jobs=out/'llm_jobs.json'
    summary={'status':'planning_ready','resume_exit_code':EXIT_GENERATED,'facts':str(facts_path),'chapter_plan':str(plan_path),'gap_analysis':str(gaps),'research_tasks':str(research_tasks),'blocked_sections':blocked_sections,'blocked_section_count':len(blocked_sections),'research_cache_hit':research_cache_hit,'planning_snapshot':str(snapshot_path)}
    if args.ai_mode=='host_agent':
        if evidence is None:
            if tasks:
                rfrag=out/'research_fragments'
                rpacks=write_research_packs(tasks_payload,rfrag)
                rwf=write_research_host_workflow(tasks_payload,rfrag,out,SKILL_ROOT,rpacks['manifest_data'])
                summary={'status':'needs_research','resume_exit_code':EXIT_NEEDS_RESEARCH,'facts':str(facts_path),'research_tasks':str(research_tasks),'blocked_sections':blocked_sections,'research_evidence_schema':str(SKILL_ROOT/'schemas/research_evidence.schema.json'),'research_fragments_dir':str(rfrag),'research_contexts_dir':rpacks['contexts_dir'],'research_worker_count':rpacks['workers'],'research_worker_total_pack_bytes':rpacks['total_pack_bytes'],'host_workflow':rwf,'research_cache_hit':False,'planning_snapshot':str(snapshot_path),'next_action':'严格按 research_fragments/HOST_WORKFLOW.md 执行：一次性派发固定 research worker，搜够即停；collect 成功生成 research_evidence.json 后，立即再次调用 chapter_planning Tool，并传入 research_evidence 参数。'}
                return EXIT_NEEDS_RESEARCH, _finalize_summary(out,summary,start_time,planning_cache_hit,planning_fingerprint=fingerprint)
            evidence_payload={'contract_version':'1.0','project_id':tasks_payload.get('project_id'),'items':[]}
        else:
            evidence_payload=load_data(evidence)
            ev_result,ev_code=validate_evidence(load_data(research_tasks),evidence_payload,args.run_mode)
            if ev_code!=0:
                raise RuntimeError('research_evidence validation failed: '+'; '.join(ev_result.get('issues') or []))
            dump_json(ev_result,out/'research_validation.json'); summary['research_validation']=str(out/'research_validation.json')
        jobs_payload=build_jobs(profile_data,plan_data,facts_data,load_data(research_tasks),evidence_payload)
        jobs=jobs_payload.get('jobs') or []
        # worker pack 使用完整内存 payload；落盘的 llm_jobs.json 只保留轻量校验契约。
        dump_json(compact_jobs_for_validation(jobs_payload),llm_jobs)
        summary['llm_jobs']=str(llm_jobs); summary['research_evidence']=str(evidence) if evidence else None; summary['skipped_sections']=jobs_payload.get('skipped_sections') or []
        if jobs and drafts is None:
            frag_dir=out/'draft_fragments'; frag_dir.mkdir(parents=True,exist_ok=True)
            ctx=write_worker_packs(jobs_payload,frag_dir)
            wf=write_host_workflow(jobs_payload,frag_dir,out,evidence,SKILL_ROOT,ctx['manifest_data'])
            summary={'status':'needs_llm','resume_exit_code':EXIT_NEEDS_LLM,'llm_jobs':str(llm_jobs),'blocked_sections':blocked_sections,'section_drafts_schema':str(SKILL_ROOT/'schemas/section_drafts.schema.json'),'research_evidence':str(evidence) if evidence else None,'research_cache_hit':research_cache_hit,'draft_fragments_dir':str(frag_dir),'worker_contexts_dir':ctx['contexts_dir'],'contexts_dir':ctx['contexts_dir'],'host_workflow':wf,'worker_count':ctx['workers'],'worker_total_pack_bytes':ctx['total_pack_bytes'],'worker_max_pack_bytes':ctx['max_pack_bytes'],'planning_snapshot':str(snapshot_path),'next_action':'严格按 draft_fragments/HOST_WORKFLOW.md 执行：一次性并行派发固定 worker；正文遵守 length_budget_chars；collect 成功生成 section_drafts.json 后，立即再次调用 chapter_planning Tool，并传入 section_drafts 参数。'}
            return EXIT_NEEDS_LLM, _finalize_summary(out,summary,start_time,planning_cache_hit,ctx['manifest_data'],planning_fingerprint=fingerprint)
        if jobs:
            dr_result,dr_code=validate_drafts(jobs_payload,evidence_payload,load_data(drafts),args.run_mode)
            if dr_code!=0:
                raise RuntimeError('section_drafts validation failed: '+'; '.join(dr_result.get('issues') or []))
            dump_json(dr_result,out/'section_drafts_validation.json'); summary['section_drafts_validation']=str(out/'section_drafts_validation.json')
    else:
        project_id=_registry_project_id(out)
        if project_id is None:
            p=profile_data.get('project_profile') or {}
            project_id=p.get('project_id') or (facts_data.get('project') or {}).get('project_id')
        dump_json({'contract_version':'1.0','project_id':project_id,'jobs':[]},llm_jobs)
        summary['llm_jobs']=str(llm_jobs)
    summary['ai_mode']=args.ai_mode
    worker_metrics=_load_current_worker_performance(out,fingerprint) if drafts else None
    return EXIT_GENERATED, _finalize_summary(out,summary,start_time,planning_cache_hit,worker_metrics=worker_metrics,planning_fingerprint=fingerprint)
