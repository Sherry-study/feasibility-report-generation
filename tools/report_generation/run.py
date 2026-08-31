#!/usr/bin/env python3
from __future__ import annotations
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from internal.common import emit_result
from internal.report.stage import run_report_generation_stage


def main():
    ap=argparse.ArgumentParser(description='Stage 4 report generation: topology, tables, report model, consistency gate and DOCX/Markdown export (in-process).')
    ap.add_argument('--facts',required=True,help='confirmed_project_facts.json 路径'); ap.add_argument('--profile',required=True); ap.add_argument('--chapter-plan',required=True); ap.add_argument('--output-dir',required=True)
    ap.add_argument('--section-drafts'); ap.add_argument('--strict-consistency',action='store_true')
    a=ap.parse_args()
    code,summary=run_report_generation_stage(a,a.output_dir)
    emit_result(None,summary)
    return code
if __name__=='__main__': raise SystemExit(main())
