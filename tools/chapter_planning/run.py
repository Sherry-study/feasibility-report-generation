#!/usr/bin/env python3
from __future__ import annotations
import argparse,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from internal.common import emit_result
from internal.planning.stage import run_chapter_planning_stage


def main():
    ap=argparse.ArgumentParser(description='Stage 3 chapter planning: generate chapter plan on confirmed facts, gap analysis, deferred questions, research tasks and host-agent research/LLM routing (in-process).')
    ap.add_argument('--facts',required=True,help='confirmed_project_facts.json 路径'); ap.add_argument('--profile',required=True); ap.add_argument('--output-dir',required=True)
    ap.add_argument('--research-evidence'); ap.add_argument('--section-drafts')
    ap.add_argument('--ai-mode',choices=['host_agent','disabled'],default='host_agent'); ap.add_argument('--run-mode',choices=['production','test'],default='production')
    ap.add_argument('--skip-user-inputs',action='store_true',help='跳过编制信息一次性提问（deferred_questions），保留缺口继续；仅当用户明确无法提供时使用')
    a=ap.parse_args()
    if a.run_mode=='test' and os.environ.get('FEASIBILITY_SKILL_DEV_TEST')!='1': ap.error('test mode requires FEASIBILITY_SKILL_DEV_TEST=1')
    code,summary=run_chapter_planning_stage(a,a.output_dir)
    emit_result(None,summary)
    return code
if __name__=='__main__': raise SystemExit(main())
