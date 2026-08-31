from __future__ import annotations
import json
import re
from pathlib import Path
import yaml

from internal.domain import deep_merge

SKILL_ROOT=Path(__file__).resolve().parents[1]

EXIT_GENERATED=0
EXIT_NEEDS_RESOLUTION=2
EXIT_CONSISTENCY_BLOCKED=3
EXIT_NEEDS_USER_INPUT=9
EXIT_NEEDS_RESEARCH=10
EXIT_NEEDS_LLM=11
EXIT_NEEDS_CONFIRMATION=12


def load_data(path):
    p=Path(path)
    if p.suffix.lower() in ('.yaml','.yml'):
        return yaml.safe_load(p.read_text(encoding='utf-8-sig')) or {}
    return json.loads(p.read_text(encoding='utf-8-sig'))


def load_json_loose(path):
    p=Path(path)
    txt=p.read_text(encoding="utf-8-sig", errors="replace")
    if p.suffix.lower()==".jsonc":
        txt=re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
        txt=re.sub(r"(^|\s)//.*?$", r"\1", txt, flags=re.M)
    return json.loads(txt)


def dump_json(data, path):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return p


def emit_result(path, data):
    if path is not None:
        dump_json(data, path)
    print(json.dumps(data,ensure_ascii=False,indent=2))


def merge_dict(target, patch):
    return deep_merge(target, patch)
