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
from pathlib import Path

from internal.common import EXIT_GENERATED, EXIT_NEEDS_LLM, EXIT_NEEDS_RESEARCH, EXIT_NEEDS_USER_INPUT, SKILL_ROOT, dump_json, load_data
from internal.planning.chapters import decide
from internal.planning.gaps import analyze as analyze_gaps
from internal.planning.research_tasks import build_tasks
from internal.planning.llm_jobs import build_jobs, compact_jobs_for_validation
from internal.planning.draft_fragments import write_host_workflow, write_worker_packs
from internal.planning.research_fragments import write_research_packs, write_research_host_workflow
from internal.planning.validate_evidence import validate as validate_evidence
from internal.report.validate_drafts import validate as validate_drafts


def _registry_project_id(out):
    """从解析登记文件中取 project_id；缺失时由调用方再降级到 profile/facts。"""
    try:
        reg=load_data(out/'_resolved'/'source_registry.json')
        return (reg.get('project') or {}).get('project_id')
    except Exception:
        return None


def run_chapter_planning_stage(args, output_dir):
    """执行阶段③，返回 (exit_code, summary_dict)，不直接打印结果。"""
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    facts_path=Path(args.facts).resolve(); profile_path=Path(args.profile).resolve()

    # 章节规划在确认 Gate 之后执行：decide 只消费 project_profile（层级/类型/变更边界），
    # 确定性生成，不依赖确认前事实，因此确认后生成即最终版本。
    plan_path=out/'chapter_plan.json'; plan_data=decide(load_data(profile_path)); dump_json(plan_data,plan_path)
    facts_data=load_data(facts_path)

    # 缺口分析必须基于确认后事实，保证 Research/LLM 路由看到的是冻结基线。
    gaps=out/'gap_analysis.json'; gapdata=analyze_gaps(facts_data,plan_data); dump_json(gapdata,gaps)
    blocked_sections=gapdata.get('blocked_sections') or []

    # 编制信息一次性提问（确认 Gate 之后）：项目名称/建设单位/年运行时长等
    # 仍缺失时在此一次性收集，不再于阶段①前置打断。
    deferred=gapdata.get('deferred_questions') or []
    if deferred and not getattr(args,'skip_user_inputs',False):
        return EXIT_NEEDS_USER_INPUT, {'status':'needs_user_input','resume_exit_code':EXIT_NEEDS_USER_INPUT,'facts':str(facts_path),'chapter_plan':str(plan_path),'gap_analysis':str(gaps),'user_questions':deferred,'next_action':'编制信息一次性提问：如需补充项目名称、建设单位、年运行时长等信息，先通过 engineering_facts Tool 的 user_inputs 或对应显式参数重新生成事实，再通过 engineering_confirmation Tool 重新确认，最后调用 chapter_planning Tool；如决定暂缺并保留缺口，可再次调用 chapter_planning Tool 并设置 skip_user_inputs=true。'}

    research_tasks=out/'research_tasks.json'
    tasks_payload=build_tasks(load_data(profile_path),plan_data,facts_data); dump_json(tasks_payload,research_tasks)
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
    summary={'status':'planning_ready','resume_exit_code':EXIT_GENERATED,'facts':str(facts_path),'chapter_plan':str(plan_path),'gap_analysis':str(gaps),'research_tasks':str(research_tasks),'blocked_sections':blocked_sections,'blocked_section_count':len(blocked_sections),'research_cache_hit':research_cache_hit}
    if args.ai_mode=='host_agent':
        if evidence is None:
            if tasks:
                rfrag=out/'research_fragments'
                rpacks=write_research_packs(tasks_payload,rfrag)
                rwf=write_research_host_workflow(tasks_payload,rfrag,out,SKILL_ROOT,rpacks['manifest_data'])
                return EXIT_NEEDS_RESEARCH, {'status':'needs_research','resume_exit_code':EXIT_NEEDS_RESEARCH,'facts':str(facts_path),'research_tasks':str(research_tasks),'blocked_sections':blocked_sections,'research_evidence_schema':str(SKILL_ROOT/'schemas/research_evidence.schema.json'),'research_fragments_dir':str(rfrag),'research_contexts_dir':rpacks['contexts_dir'],'research_worker_count':rpacks['workers'],'research_worker_total_pack_bytes':rpacks['total_pack_bytes'],'host_workflow':rwf,'research_cache_hit':False,'next_action':'严格按 research_fragments/HOST_WORKFLOW.md 执行：一次性派发固定 research worker，搜够即停；collect 成功生成 research_evidence.json 后，立即再次调用 chapter_planning Tool，并传入 research_evidence 参数。'}
            evidence_payload={'contract_version':'1.0','project_id':tasks_payload.get('project_id'),'items':[]}
        else:
            evidence_payload=load_data(evidence)
            ev_result,ev_code=validate_evidence(load_data(research_tasks),evidence_payload,args.run_mode)
            if ev_code!=0:
                raise RuntimeError('research_evidence validation failed: '+'; '.join(ev_result.get('issues') or []))
            dump_json(ev_result,out/'research_validation.json'); summary['research_validation']=str(out/'research_validation.json')
        jobs_payload=build_jobs(load_data(profile_path),plan_data,facts_data,load_data(research_tasks),evidence_payload)
        jobs=jobs_payload.get('jobs') or []
        # worker pack 使用完整内存 payload；落盘的 llm_jobs.json 只保留轻量校验契约。
        dump_json(compact_jobs_for_validation(jobs_payload),llm_jobs)
        summary['llm_jobs']=str(llm_jobs); summary['research_evidence']=str(evidence) if evidence else None; summary['skipped_sections']=jobs_payload.get('skipped_sections') or []
        if jobs and drafts is None:
            frag_dir=out/'draft_fragments'; frag_dir.mkdir(parents=True,exist_ok=True)
            ctx=write_worker_packs(jobs_payload,frag_dir)
            wf=write_host_workflow(jobs_payload,frag_dir,out,evidence,SKILL_ROOT,ctx['manifest_data'])
            return EXIT_NEEDS_LLM, {'status':'needs_llm','resume_exit_code':EXIT_NEEDS_LLM,'llm_jobs':str(llm_jobs),'blocked_sections':blocked_sections,'section_drafts_schema':str(SKILL_ROOT/'schemas/section_drafts.schema.json'),'research_evidence':str(evidence) if evidence else None,'research_cache_hit':research_cache_hit,'draft_fragments_dir':str(frag_dir),'worker_contexts_dir':ctx['contexts_dir'],'contexts_dir':ctx['contexts_dir'],'host_workflow':wf,'worker_count':ctx['workers'],'worker_total_pack_bytes':ctx['total_pack_bytes'],'worker_max_pack_bytes':ctx['max_pack_bytes'],'next_action':'严格按 draft_fragments/HOST_WORKFLOW.md 执行：一次性并行派发固定 worker；正文遵守 length_budget_chars；collect 成功生成 section_drafts.json 后，立即再次调用 chapter_planning Tool，并传入 section_drafts 参数。'}
        if jobs:
            dr_result,dr_code=validate_drafts(jobs_payload,evidence_payload,load_data(drafts),args.run_mode)
            if dr_code!=0:
                raise RuntimeError('section_drafts validation failed: '+'; '.join(dr_result.get('issues') or []))
            dump_json(dr_result,out/'section_drafts_validation.json'); summary['section_drafts_validation']=str(out/'section_drafts_validation.json')
    else:
        project_id=_registry_project_id(out)
        if project_id is None:
            p=load_data(profile_path).get('project_profile') or {}
            project_id=p.get('project_id') or (facts_data.get('project') or {}).get('project_id')
        dump_json({'contract_version':'1.0','project_id':project_id,'jobs':[]},llm_jobs)
        summary['llm_jobs']=str(llm_jobs)
    summary['ai_mode']=args.ai_mode
    return EXIT_GENERATED, summary
