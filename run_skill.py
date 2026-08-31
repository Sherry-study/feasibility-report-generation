#!/usr/bin/env python3
"""One-shot Skill orchestrator: four in-process stages.

Stage 1 engineering facts -> internal.facts.stage.run_engineering_facts_stage
Stage 2 confirmation gate -> internal.confirmation.run_confirmation_stage
Stage 3 chapter planning  -> internal.planning.stage.run_chapter_planning_stage
Stage 4 report generation -> internal.report.stage.run_report_generation_stage

Every stage runs in-process in this interpreter and shares the internal.*
contract modules that also back the four stage tools (tools/engineering_facts,
tools/engineering_confirmation, tools/chapter_planning, tools/report_generation).
Exit-code sequence and run_summary.json field contracts are unchanged.
"""
from __future__ import annotations
import argparse,os,sys,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from internal.common import EXIT_CONSISTENCY_BLOCKED,EXIT_GENERATED,EXIT_NEEDS_CONFIRMATION,EXIT_NEEDS_LLM,EXIT_NEEDS_RESEARCH,EXIT_NEEDS_RESOLUTION,EXIT_NEEDS_USER_INPUT,emit_result
from internal.facts.stage import run_engineering_facts_stage
from internal.confirmation import run_confirmation_stage
from internal.planning.stage import run_chapter_planning_stage
from internal.report.stage import run_report_generation_stage


def summary(out,data,code=0):
    data={'skill_version':'0.14.9',**data}; emit_result(out/'run_summary.json', data); return code


def cleanup_runtime_artifacts(out, keep_runtime=False):
    """Keep the final output directory small after a successful run.

    Runtime files remain available at every needs_* gate. Cleanup happens only
    after DOCX/Markdown generation, so resume/retry semantics are unchanged.
    """
    if keep_runtime:
        return []
    removed=[]
    for name in ('_resolved','research_fragments','draft_fragments'):
        path=out/name
        if path.exists():
            shutil.rmtree(path,ignore_errors=True); removed.append(name+'/')
    transient=[
      'project_facts.json','annualization_result.json','energy_conversion_result.json',
      'report_confirmation.json','report_confirmation.md','confirmed_report_confirmation.json',
      'chapter_plan.json','gap_analysis.json','research_tasks.json','llm_jobs.json',
      'research_validation.json','research_cache_validation.json','section_drafts_validation.json',
      'section_drafts.json','process_topology_analysis.json','report_tables.json','report_model.json',
      'consistency_check.json','report_trace.json'
    ]
    for name in transient:
        path=out/name
        if path.exists() and path.is_file():
            path.unlink(); removed.append(name)
    return removed


def main():
    ap=argparse.ArgumentParser(description='Run the generic feasibility-report generation Skill.')
    ap.add_argument('--workspace'); ap.add_argument('--source-manifest'); ap.add_argument('--profile'); ap.add_argument('--project-name'); ap.add_argument('--project-id'); ap.add_argument('--project-level',choices=['equipment','unit','system','plant'],default='unit'); ap.add_argument('--project-type',default='mixed'); ap.add_argument('--output-dir',required=True)
    ap.add_argument('--user-inputs'); ap.add_argument('--construction-unit'); ap.add_argument('--annual-operating-hours',type=float); ap.add_argument('--implementation-schedule',help='可选覆盖：宿主提供的实施进度估算 JSON 文件（packages[{name,duration_months}]，total_duration_months 可选，单位月），随阶段①注入工程事实并用于 18.2；未提供时 18.2 由 LLM 按改造工程量直接起草，无需向用户提问'); ap.add_argument('--project-location'); ap.add_argument('--skip-user-inputs',action='store_true')
    ap.add_argument('--ai-mode',choices=['host_agent','disabled'],default='host_agent'); ap.add_argument('--run-mode',choices=['production','test'],default='production'); ap.add_argument('--research-evidence'); ap.add_argument('--section-drafts'); ap.add_argument('--strict-consistency',action='store_true')
    ap.add_argument('--confirmation-response',help='用户确认/修正 report_confirmation.json 后形成的确认响应 JSON/YAML')
    ap.add_argument('--keep-runtime-artifacts',action='store_true',help='成功生成报告后保留全部中间调试文件；默认清理，仅保留最终报告、确认后工程事实、研究证据和run_summary')
    ap.add_argument('--confirm-as-is',action='store_true',help='显式确认 report_confirmation.json 当前内容，不做修改')
    a=ap.parse_args()
    if not a.workspace and not a.source_manifest: ap.error('至少提供 --workspace 或 --source-manifest')
    if a.run_mode=='test' and os.environ.get('FEASIBILITY_SKILL_DEV_TEST')!='1': ap.error('test mode requires FEASIBILITY_SKILL_DEV_TEST=1')
    out=Path(a.output_dir).resolve()

    # Stage 1: source registry -> profile/user inputs -> engineering facts.
    # 本阶段不提问、不排章节：确认 Gate 紧邻事实整理，章节规划/缺口分析/一次性提问延后到阶段③。
    code,s1=run_engineering_facts_stage(a,out)
    if code==EXIT_NEEDS_RESOLUTION: return summary(out,s1,code)
    profile=s1['project_profile']; facts=out/'project_facts.json'

    # Stage 2: deterministic annualization merge + pre-writing key-fact confirmation gate.
    gate=argparse.Namespace(facts=str(facts),confirmation_response=a.confirmation_response,confirm_as_is=a.confirm_as_is)
    code,s2=run_confirmation_stage(gate,out)
    if code==EXIT_NEEDS_CONFIRMATION: return summary(out,s2,code)
    active_facts=out/'confirmed_project_facts.json'

    # Stage 3 (after confirmation): chapter plan generation -> gap analysis on frozen facts
    # -> deferred compilation-info questions (asked once) -> research tasks -> research/LLM routing.
    routing=argparse.Namespace(facts=str(active_facts),profile=profile,research_evidence=a.research_evidence,section_drafts=a.section_drafts,ai_mode=a.ai_mode,run_mode=a.run_mode,skip_user_inputs=a.skip_user_inputs)
    code,s3=run_chapter_planning_stage(routing,out)
    if code in (EXIT_NEEDS_USER_INPUT,EXIT_NEEDS_RESEARCH,EXIT_NEEDS_LLM): return summary(out,s3,code)

    # Stage 4: topology/tables -> report model -> consistency gate -> DOCX/Markdown + trace.
    # chapter_plan.json 由阶段③产出，此处读取。
    generation=argparse.Namespace(facts=str(active_facts),profile=profile,chapter_plan=str(out/'chapter_plan.json'),section_drafts=a.section_drafts,strict_consistency=a.strict_consistency)
    code,s4=run_report_generation_stage(generation,out)
    if code==EXIT_CONSISTENCY_BLOCKED: return summary(out,s4,code)

    removed=cleanup_runtime_artifacts(out,a.keep_runtime_artifacts)
    return summary(out,{'status':'generated','completion_status':s4.get('completion_status','generated'),'blocked_section_count':s4.get('blocked_section_count',0),'blocked_sections':s4.get('blocked_sections',[]),'docx':s4['docx'],'markdown':s4['markdown'],'facts':str(active_facts),'research_evidence':str(out/'research_evidence.json') if (out/'research_evidence.json').exists() else None,'runtime_artifacts_cleaned':not a.keep_runtime_artifacts,'cleaned_count':len(removed),'ai_mode':a.ai_mode,'run_mode':a.run_mode},EXIT_GENERATED)

if __name__=='__main__': raise SystemExit(main())
