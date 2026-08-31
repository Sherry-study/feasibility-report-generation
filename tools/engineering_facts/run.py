#!/usr/bin/env python3
from __future__ import annotations
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from internal.common import emit_result
from internal.facts.stage import run_engineering_facts_stage


def main():
    ap=argparse.ArgumentParser(description='Stage 1 engineering facts: resolve sources and build project facts (in-process; no chapter planning, no questions).')
    ap.add_argument('--workspace'); ap.add_argument('--source-manifest'); ap.add_argument('--profile'); ap.add_argument('--project-name'); ap.add_argument('--project-id'); ap.add_argument('--project-level',choices=['equipment','unit','system','plant'],default='unit'); ap.add_argument('--project-type',default='mixed'); ap.add_argument('--output-dir',required=True)
    ap.add_argument('--user-inputs'); ap.add_argument('--construction-unit'); ap.add_argument('--annual-operating-hours',type=float); ap.add_argument('--project-location'); ap.add_argument('--skip-user-inputs',action='store_true')
    a=ap.parse_args()
    if not a.workspace and not a.source_manifest: ap.error('至少提供 --workspace 或 --source-manifest')
    code,summary=run_engineering_facts_stage(a,a.output_dir)
    emit_result(None,summary)
    return code
if __name__=='__main__': raise SystemExit(main())
