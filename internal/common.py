from __future__ import annotations
import json
import re
from pathlib import Path
import yaml

from internal.domain import deep_merge

# 这些路径是 Skill 包内的运行时边界：
# - src/ 暴露 MCP Tool；
# - internal/ 执行业务实现；
# - references/ 保存 Agent/Tool 需要读取的领域规则。
# 统一在 common.py 定义，避免各阶段继续引用旧知识目录路径。
SKILL_ROOT=Path(__file__).resolve().parents[1]
REFERENCES_ROOT=SKILL_ROOT/'references'
ENGINEERING_RULES_ROOT=REFERENCES_ROOT/'engineering_rules'
REPORT_RULES_ROOT=REFERENCES_ROOT/'report_rules'

# Tool 阶段用这些退出码表达“下一步由宿主继续推进”的状态。
# 数字值属于既有调用契约，不能为了可读性重排。
EXIT_GENERATED=0
EXIT_NEEDS_RESOLUTION=2
EXIT_CONSISTENCY_BLOCKED=3
EXIT_NEEDS_USER_INPUT=9
EXIT_NEEDS_RESEARCH=10
EXIT_NEEDS_LLM=11
EXIT_NEEDS_CONFIRMATION=12


def load_data(path):
    """读取 JSON/YAML 输入文件；空 YAML 统一返回空 dict，便于阶段逻辑消费。"""
    p=Path(path)
    if p.suffix.lower() in ('.yaml','.yml'):
        return yaml.safe_load(p.read_text(encoding='utf-8-sig')) or {}
    return json.loads(p.read_text(encoding='utf-8-sig'))


def load_json_loose(path):
    """读取 JSON/JSONC；仅用于兼容带注释的本地配置，不用于正式 Schema。"""
    p=Path(path)
    txt=p.read_text(encoding="utf-8-sig", errors="replace")
    if p.suffix.lower()==".jsonc":
        txt=re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
        txt=re.sub(r"(^|\s)//.*?$", r"\1", txt, flags=re.M)
    return json.loads(txt)


def dump_json(data, path):
    """以 UTF-8 写入确定性 JSON，并自动创建父目录。"""
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return p


def emit_result(path, data):
    """把阶段结果打印到 stdout；如传入路径，则同时落盘供宿主读取。"""
    if path is not None:
        dump_json(data, path)
    print(json.dumps(data,ensure_ascii=False,indent=2))


def merge_dict(target, patch):
    """保留旧调用点的合并入口，实际委托给 domain.deep_merge。"""
    return deep_merge(target, patch)
