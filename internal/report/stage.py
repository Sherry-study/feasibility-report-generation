#!/usr/bin/env python3
"""阶段④：报告模型构建、门禁检查与 DOCX/Markdown 导出。

本模块是 `report_generation` Tool 的进程内实现。执行顺序为：
工艺拓扑分析 -> 报告表格 -> report model -> 一致性门禁
-> DOCX/Markdown 导出 -> report trace。所有内部调用都在进程内完成。
"""
from __future__ import annotations
from pathlib import Path

from internal.common import EXIT_CONSISTENCY_BLOCKED, EXIT_GENERATED, REPORT_RULES_ROOT, data_digest, dump_json, load_data
from internal.report.topology import analyze as analyze_topology
from internal.report.tables import build as build_tables
from internal.report.model import build_report_model
from internal.report.gates import check as check_consistency
from internal.report.exporters import export_model, export_model_markdown
from internal.planning.validate_evidence import validate as validate_evidence
from internal.report.validate_drafts import validate as validate_drafts


def _registry_project_id(out, profile_data, facts_data):
    """优先使用 source_registry 中的 project_id，缺失时回退到 profile/facts。"""
    try:
        reg=load_data(out/'_resolved'/'source_registry.json')
        pid=(reg.get('project') or {}).get('project_id')
        if pid: return pid
    except Exception:
        pass
    p=profile_data.get('project_profile',profile_data) or {}
    return p.get('project_id') or (facts_data.get('project') or {}).get('project_id')


def _artifact_digest(path):
    p=Path(path)
    if not p.is_file():
        return None
    try:
        return data_digest(load_data(p))
    except Exception:
        return None


def _blocked(validation_stage, issues):
    return EXIT_CONSISTENCY_BLOCKED, {'status':'consistency_blocked','validation_stage':validation_stage,'issues':issues}


def _resolved(path):
    try:
        return Path(path).resolve()
    except Exception:
        return None


def _require_arg_artifact(artifacts, key, expected_path):
    item=artifacts.get(key) or {}
    recorded=_resolved(item.get('path')) if item.get('path') else None
    expected=Path(expected_path).resolve()
    if recorded!=expected:
        return f'{key} path does not match report_generation argument'
    if _artifact_digest(expected)!=item.get('sha256'):
        return f'{key} does not match planning_delivery_manifest digest'
    return None


def _require_output_artifact(out, artifacts, key, filename):
    item=artifacts.get(key) or {}
    recorded=_resolved(item.get('path')) if item.get('path') else None
    expected=(out/filename).resolve()
    if recorded!=expected:
        return None,f'{key} path must be {filename} in current output_dir'
    if _artifact_digest(expected)!=item.get('sha256'):
        return None,f'{key} missing or digest mismatch'
    return expected,None


def _validate_planning_delivery(out, facts_path, profile_path, plan_path, drafts_path, mode='production'):
    manifest_path=out/'planning_delivery_manifest.json'
    try:
        manifest=load_data(manifest_path)
    except Exception as e:
        return _blocked('planning_delivery_manifest',[f'planning_delivery_manifest unavailable: {e}'])
    if not isinstance(manifest,dict):
        return _blocked('planning_delivery_manifest',['planning_delivery_manifest must be an object'])
    if manifest.get('contract_version')!='1.0':
        return _blocked('planning_delivery_manifest',['planning_delivery_manifest contract_version must be 1.0'])
    manifest_fp=manifest.get('planning_fingerprint_sha256')
    if not manifest_fp:
        return _blocked('planning_delivery_manifest',['planning_fingerprint_sha256 is required'])
    try:
        snapshot=load_data(out/'planning_snapshot.json')
        snapshot_fp=data_digest(snapshot.get('fingerprint'))
    except Exception as e:
        return _blocked('planning_snapshot',[f'planning_snapshot unavailable: {e}'])
    if snapshot_fp!=manifest_fp:
        return _blocked('planning_snapshot',['planning_snapshot fingerprint does not match planning_delivery_manifest'])
    artifacts=manifest.get('artifacts') or {}
    for key,path in {'facts':facts_path,'profile':profile_path,'chapter_plan':plan_path}.items():
        issue=_require_arg_artifact(artifacts,key,path)
        if issue:
            return _blocked('planning_delivery_manifest',[issue])
    research_tasks_path,issue=_require_output_artifact(out,artifacts,'research_tasks','research_tasks.json')
    if issue:
        return _blocked('planning_delivery_manifest',[issue])
    llm_jobs_path,issue=_require_output_artifact(out,artifacts,'llm_jobs','llm_jobs.json')
    if issue:
        return _blocked('planning_delivery_manifest',[issue])
    try:
        tasks=load_data(research_tasks_path)
        llm_jobs=load_data(llm_jobs_path)
    except Exception as e:
        return _blocked('planning_delivery_manifest',[f'planning artifact unreadable: {e}'])
    if llm_jobs.get('jobs') and not drafts_path:
        return _blocked('section_drafts',['section_drafts is required when llm_jobs is non-empty'])
    if drafts_path and not Path(drafts_path).is_file():
        return _blocked('section_drafts',[f'section_drafts not found: {drafts_path}'])
    evidence_item=artifacts.get('research_evidence')
    if (tasks.get('tasks') or []) and not evidence_item:
        return _blocked('research_evidence',['research_evidence is required when research_tasks is non-empty'])
    if evidence_item:
        evidence_path,issue=_require_output_artifact(out,artifacts,'research_evidence','research_evidence.json')
        if issue:
            return _blocked('research_evidence',[issue])
        try:
            evidence=load_data(evidence_path)
        except Exception as e:
            return _blocked('research_evidence',[f'research_evidence unreadable: {e}'])
    else:
        evidence={'contract_version':'1.0','project_id':tasks.get('project_id'),'items':[]}
        if mode=='test':
            evidence['test_only']=True
    ev_result,ev_code=validate_evidence(tasks,evidence,mode)
    if ev_code!=0:
        return _blocked('research_evidence',ev_result.get('issues') or ['research_evidence validation failed'])
    if drafts_path:
        try:
            drafts=load_data(drafts_path)
        except Exception as e:
            return _blocked('section_drafts',[f'section_drafts unreadable: {e}'])
        dr_result,dr_code=validate_drafts(llm_jobs,evidence,drafts,mode)
        if dr_code!=0:
            return _blocked('section_drafts',dr_result.get('issues') or ['section_drafts validation failed'])
    return None


def run_report_generation_stage(args, output_dir):
    """执行阶段④，返回 (exit_code, summary_dict)，不直接打印结果。"""
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    facts_path=Path(args.facts).resolve(); profile_path=Path(args.profile).resolve(); plan_path=Path(args.chapter_plan).resolve()
    facts_data=load_data(facts_path); profile_data=load_data(profile_path); plan_data=load_data(plan_path)
    gap_path=out/'gap_analysis.json'
    gap_data=load_data(gap_path) if gap_path.exists() else {}
    blocked_sections=gap_data.get('blocked_sections') or []
    drafts=Path(args.section_drafts).resolve() if args.section_drafts else None
    delivery_error=_validate_planning_delivery(out,facts_path,profile_path,plan_path,drafts,getattr(args,'run_mode','production'))
    if delivery_error:
        return delivery_error

    topology=out/'process_topology_analysis.json'
    topo_data=analyze_topology(facts_data); dump_json(topo_data,topology)
    tables=out/'report_tables.json'; tables_data=build_tables(facts_data); dump_json(tables_data,tables)

    model=build_report_model(profile_data,facts_data,tables_data,topo_data,load_data(REPORT_RULES_ROOT/'standards_library.yaml'),chapter_plan=plan_data,drafts=load_data(drafts) if drafts else None)
    report_model=out/'report_model.json'; dump_json(model,report_model)

    check=out/'consistency_check.json'
    cdata=check_consistency(facts_data,model,load_data(drafts) if drafts else None,plan_data,args.strict_consistency)
    if blocked_sections:
        warning={'severity':'medium','code':'CHAPTERS_BLOCKED_BY_REQUIRED_FACTS','message':'部分章节因必需事实缺失未生成LLM正文；报告不是数据闭合版本。','blocked_sections':[x.get('section_id') for x in blocked_sections]}
        cdata.setdefault('warnings',[]).append(warning)
        if cdata.get('status')=='passed':
            cdata['status']='warning'
    dump_json(cdata,check)
    if cdata.get('status')=='failed':
        return EXIT_CONSISTENCY_BLOCKED, {'status':'consistency_blocked','consistency_check':str(check),'report_model':str(report_model),'issues':cdata.get('issues')}

    docx=out/'可行性研究报告_初稿.docx'; markdown=out/'可行性研究报告_初稿.md'
    export_model(model,docx); export_model_markdown(model,markdown)

    # trace 记录最终产物和中间证据路径，供调试与交付追溯使用。
    unconfirmed_facts=out/'project_facts.json'
    facts_trace=str(unconfirmed_facts) if unconfirmed_facts.exists() else str(facts_path)
    trace={'project_id':_registry_project_id(out,profile_data,facts_data),'source_registry':str(out/'_resolved'/'source_registry.json'),'facts':facts_trace,'chapter_plan':str(plan_path),'gap_analysis':str(out/'gap_analysis.json'),'blocked_sections':blocked_sections,'annualization':str(out/'annualization_result.json'),'process_topology_analysis':str(topology),'report_tables':str(tables),'research_tasks':str(out/'research_tasks.json'),'llm_jobs':str(out/'llm_jobs.json'),'consistency_check':str(check),'adopted_scheme':(facts_data.get('adopted_scheme') or {}).get('scheme_name'),'report_confirmation':str(out/'report_confirmation.json'),'report_confirmation_markdown':str(out/'report_confirmation.md'),'confirmed_report_confirmation':str(out/'confirmed_report_confirmation.json'),'confirmed_facts':str(facts_path),'docx':str(docx),'markdown':str(markdown)}
    dump_json(trace,out/'report_trace.json')
    completion_status='generated_with_blocked_sections' if blocked_sections else 'generated'
    return EXIT_GENERATED, {'status':'generated','completion_status':completion_status,'blocked_section_count':len(blocked_sections),'blocked_sections':blocked_sections,'resume_exit_code':EXIT_GENERATED,'docx':str(docx),'markdown':str(markdown),'facts':str(facts_path),'report_model':str(report_model),'consistency_check':str(check),'report_trace':str(out/'report_trace.json')}
