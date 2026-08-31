#!/usr/bin/env python3
from __future__ import annotations
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from internal.common import emit_result
from internal.confirmation import run_confirmation_stage


def main():
    ap=argparse.ArgumentParser(description='Stage 2 human confirmation: annualization + merge, report confirmation generation and apply gate (in-process).')
    ap.add_argument('--facts',required=True,help='project_facts.json 路径（将被年化 merge 回写）')
    ap.add_argument('--output-dir',required=True)
    ap.add_argument('--confirm-as-is',action='store_true',help='显式确认 report_confirmation.json 当前内容，不做修改')
    ap.add_argument('--confirmation-response',help='用户确认/修正 report_confirmation.json 后形成的确认响应 JSON/YAML')
    a=ap.parse_args()
    code,summary=run_confirmation_stage(a,a.output_dir)
    emit_result(None,summary)
    return code
if __name__=='__main__': raise SystemExit(main())
