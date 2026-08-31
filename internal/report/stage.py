#!/usr/bin/env python3
"""Stage 4 (report generation) in-process orchestration.

Behavior contract mirrors run_skill.py stage 4 (L143-144 prerequisites +
L166-178): process topology -> report tables -> report model -> consistency
gate (exit 3 on failed) -> DOCX/Markdown export -> report trace. All internal
calls are in-process; no subprocess.
"""
from __future__ import annotations
from pathlib import Path

from internal.common import EXIT_CONSISTENCY_BLOCKED, EXIT_GENERATED, SKILL_ROOT, dump_json, load_data
from internal.report.topology import analyze as analyze_topology
from internal.report.tables import build as build_tables
from internal.report.model import build_report_model
from internal.report.gates import check as check_consistency
from internal.report.exporters import export_model, export_model_markdown


def _registry_project_id(out, profile_data, facts_data):
    try:
        reg=load_data(out/'_resolved'/'source_registry.json')
        pid=(reg.get('project') or {}).get('project_id')
        if pid: return pid
    except Exception:
        pass
    p=profile_data.get('project_profile',profile_data) or {}
    return p.get('project_id') or (facts_data.get('project') or {}).get('project_id')


def run_report_generation_stage(args, output_dir):
    """Run stage 4. Returns (exit_code, summary_dict) without printing."""
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    facts_path=Path(args.facts).resolve(); profile_path=Path(args.profile).resolve(); plan_path=Path(args.chapter_plan).resolve()
    facts_data=load_data(facts_path); profile_data=load_data(profile_path); plan_data=load_data(plan_path)
    gap_path=out/'gap_analysis.json'
    gap_data=load_data(gap_path) if gap_path.exists() else {}
    blocked_sections=gap_data.get('blocked_sections') or []
    drafts=Path(args.section_drafts).resolve() if args.section_drafts else None

    topology=out/'process_topology_analysis.json'
    topo_data=analyze_topology(facts_data); dump_json(topo_data,topology)
    tables=out/'report_tables.json'; tables_data=build_tables(facts_data); dump_json(tables_data,tables)

    model=build_report_model(profile_data,facts_data,tables_data,topo_data,load_data(SKILL_ROOT/'knowledge/standards_library.yaml'),chapter_plan=plan_data,drafts=load_data(drafts) if drafts else None)
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

    # Trace fields mirror run_skill.py L177; paths point into this output dir.
    unconfirmed_facts=out/'project_facts.json'
    facts_trace=str(unconfirmed_facts) if unconfirmed_facts.exists() else str(facts_path)
    trace={'project_id':_registry_project_id(out,profile_data,facts_data),'source_registry':str(out/'_resolved'/'source_registry.json'),'facts':facts_trace,'chapter_plan':str(plan_path),'gap_analysis':str(out/'gap_analysis.json'),'blocked_sections':blocked_sections,'annualization':str(out/'annualization_result.json'),'process_topology_analysis':str(topology),'report_tables':str(tables),'research_tasks':str(out/'research_tasks.json'),'llm_jobs':str(out/'llm_jobs.json'),'consistency_check':str(check),'adopted_scheme':(facts_data.get('adopted_scheme') or {}).get('scheme_name'),'report_confirmation':str(out/'report_confirmation.json'),'report_confirmation_markdown':str(out/'report_confirmation.md'),'confirmed_report_confirmation':str(out/'confirmed_report_confirmation.json'),'confirmed_facts':str(facts_path),'docx':str(docx),'markdown':str(markdown)}
    dump_json(trace,out/'report_trace.json')
    completion_status='generated_with_blocked_sections' if blocked_sections else 'generated'
    return EXIT_GENERATED, {'status':'generated','completion_status':completion_status,'blocked_section_count':len(blocked_sections),'blocked_sections':blocked_sections,'resume_exit_code':EXIT_GENERATED,'docx':str(docx),'markdown':str(markdown),'facts':str(facts_path),'report_model':str(report_model),'consistency_check':str(check),'report_trace':str(out/'report_trace.json')}
