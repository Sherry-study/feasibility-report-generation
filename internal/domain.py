"""领域规则与纯计算函数的统一入口。

纪律：
- 不做文件 IO，不打印，不导入同级模块。`common.py` 可以单向导入本模块，
  但本模块不能反向导入，避免循环依赖。
- 合并后的 helper 必须与历史重复实现保持行为等价；调用点差异通过关键字
  参数保留，例如 `annual_tonnes(strict=...)`、`deep_merge(copy=...)`、
  `resolve_annual_hours(...)` 的策略开关。
"""
from __future__ import annotations
from copy import deepcopy
from typing import Any
import re


# ===== 纯计算区 =====

def normalize_media_name(name):
    return str(name or '').strip().lower().replace(' ', '')


def factor_index(lib):
    """把折标系数库映射为 normalized name -> (factor_key, factor_item)。"""
    idx = {}
    for key, item in (lib.get('factors') or {}).items():
        names = [key, item.get('display_name'), *((item.get('aliases') or []))]
        for n in names:
            if n:
                idx[normalize_media_name(n)] = (key, item)
    return idx


def factor_alias_map(lib):
    """把折标系数库映射为 normalized name -> factor_key，仅用于介质名查找。"""
    return {norm: key for norm, (key, _item) in factor_index(lib).items()}


# ===== 能耗折标规则区（双体系：折标准煤 / 折标准油） =====

CONVERSION_TYPE_ALIASES = {
    'standard_coal': ('standard_coal', '折标准煤', '煤标', '标准煤', 'tce', 'kgce',
                      'coal equivalent', 'standard coal'),
    'standard_oil': ('standard_oil', '折标准油', '油标', '标准油', 'toe', 'kgoe',
                     'oil equivalent', 'standard oil'),
}

CONVERSION_TYPE_LABELS = {
    'standard_coal': '折标准煤',
    'standard_oil': '折标准油',
}


def normalize_conversion_type(value):
    """把折标口径提示归一为 `standard_coal`、`standard_oil` 或 None。"""
    if value in (None, ''):
        return None
    v = normalize_media_name(value)
    for ctype, aliases in CONVERSION_TYPE_ALIASES.items():
        if v == ctype or v in {normalize_media_name(a) for a in aliases}:
            return ctype
    return None


def resolve_conversion_type(user_value, profile_value):
    """解析能耗核算体系，优先级为 user_input > project_profile > 默认标准煤。

    返回 `(conversion_type, source)`，source 取值为
    `user_input`、`project_profile` 或 `default`。
    """
    ctype = normalize_conversion_type(user_value)
    if ctype:
        return ctype, 'user_input'
    ctype = normalize_conversion_type(profile_value)
    if ctype:
        return ctype, 'project_profile'
    return 'standard_coal', 'default'


def energy_rule_index(rules, conversion_type):
    """按折标体系生成 normalized media name -> exact rule dict 的索引。"""
    block = (rules or {}).get(conversion_type) or {}
    idx = {}
    for item in (block.get('exact') or []):
        names = [item.get('energy_name'), item.get('energy_code'), *((item.get('aliases') or []))]
        for n in names:
            if n:
                idx[normalize_media_name(n)] = item
    return idx


def energy_steam_block(rules, conversion_type):
    """读取某折标体系下的蒸汽范围规则块；不存在时返回 None。"""
    block = (rules or {}).get(conversion_type) or {}
    steam = block.get('steam')
    return steam if steam and steam.get('rules') else None


def steam_alias_names(steam_block):
    """生成蒸汽介质的归一化查找名称集合。"""
    if not steam_block:
        return []
    names = [steam_block.get('energy_name'), steam_block.get('energy_code'),
             *((steam_block.get('aliases') or []))]
    return {normalize_media_name(n) for n in names if n}


def match_steam_label(steam_block, name):
    """按可信压力等级标签（如 5.0MPa级蒸汽）匹配蒸汽规则。"""
    if not steam_block:
        return None
    norm = normalize_media_name(name)
    for rule in steam_block['rules']:
        labels = [rule.get('label'), rule.get('pressure_grade')]
        for lab in labels:
            if lab and normalize_media_name(lab) == norm:
                return rule
    # 兜底：从介质名中提取等级数字，例如 3.5MPa蒸汽 -> 3.5MPa级。
    m = _extract_grade_number(name)
    if m is not None:
        for rule in steam_block['rules']:
            grade = _extract_grade_number(rule.get('pressure_grade') or '')
            if grade is not None and abs(grade - m) < 1e-9:
                return rule
    return None


def _extract_grade_number(text):
    m = re.search(r'(\d+(?:\.\d+)?)\s*mpa', str(text or '').lower())
    return float(m.group(1)) if m else None


def parse_pressure(value):
    """从自由文本中提取蒸汽表压 MPa；无法提取时返回 None。"""
    if value in (None, ''):
        return None
    m = re.search(r'-?\d+(?:\.\d+)?', str(value))
    if not m:
        return None
    try:
        p = float(m.group(0))
    except ValueError:
        return None
    return p if p >= 0 else None


def _rule_min(rule):
    v = rule.get('min')
    return None if v in (None, '') else float(v)


def _rule_max(rule):
    v = rule.get('max')
    if v in (None, ''):
        return None
    if isinstance(v, str) and v.strip() in ('+inf', 'inf', 'infinity'):
        return None
    return float(v)


def match_steam_pressure(steam_block, pressure):
    """按数值表压 MPa 匹配蒸汽范围规则，端点开闭遵守规则标记。"""
    if not steam_block or pressure is None:
        return None
    p = float(pressure)
    for rule in steam_block['rules']:
        lo, hi = _rule_min(rule), _rule_max(rule)
        if lo is not None:
            if p < lo if rule.get('min_inclusive', True) else p <= lo:
                continue
        if hi is not None:
            if p > hi if rule.get('max_inclusive', True) else p >= hi:
                continue
        return rule
    return None


def match_steam_rule(steam_block, name, pressure):
    """先按可信等级标签匹配蒸汽规则，再按表压范围匹配。"""
    if not steam_block:
        return None
    if name:
        rule = match_steam_label(steam_block, name)
        if rule:
            return rule
    if pressure not in (None, ''):
        p = parse_pressure(pressure) if isinstance(pressure, str) else pressure
        if p is not None:
            return match_steam_pressure(steam_block, p)
    return None


_UNIT_ALIASES = {
    'kwh': 'kWh', '千瓦时': 'kWh', '度': 'kWh',
    't': 't', '吨': 't', 'tha': 't/a', 't/a': 't/a', '吨/年': 't/a',
    'kg': 'kg', '公斤': 'kg', 'kgh': 'kg', 'kg/h': 'kg', '公斤/小时': 'kg',
    'mpa': 'MPa', 'mpa级': 'MPa',
}


def harmonize_unit(input_unit, rule_unit):
    """确定性协调输入单位与规则单位，目前只支持质量 kg/t 互转。

    返回 `(scale, canonical_unit)`；无法确定性换算时 scale 为 None，
    调用方必须将该项视为 blocked。
    """
    u_in = _UNIT_ALIASES.get(normalize_media_name(input_unit), str(input_unit or '').strip())
    u_rule = _UNIT_ALIASES.get(normalize_media_name(rule_unit), str(rule_unit or '').strip())
    if not u_in or not u_rule:
        return None, u_rule or u_in
    if u_in == u_rule:
        return 1.0, u_rule
    mass = {'kg', 't'}
    if u_in in mass and u_rule in mass:
        return (0.001, u_rule) if u_in == 'kg' else (1000.0, u_rule)
    return None, u_rule


def annual_tonnes(flow_kg_h, hours, strict=False):
    """把 kg/h 小时量年化为 t/a：小时量 x 年运行时长 / 1000。

    `strict=True` 传播转换异常，供 Tool CLI 使用；`strict=False`
    吞掉异常并返回 None，供事实构建器保持兼容。
    """
    if flow_kg_h is None or hours in (None, ''):
        return None
    if strict:
        return float(flow_kg_h) * float(hours) / 1000.0
    try:
        return float(flow_kg_h) * float(hours) / 1000.0
    except Exception:
        return None


def first_product_stream(facts, case, default=None):
    arr = ((facts.get('process') or {}).get(case) or {}).get('product_streams') or []
    return arr[0] if arr else default


def deep_merge(target, patch, copy=False):
    if not isinstance(patch, dict):
        return target
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            deep_merge(target[k], v, copy)
        else:
            target[k] = deepcopy(v) if copy else v
    return target


def resolve_annual_hours(ui_value, plant_info_hours, manifest_value,
                         *, reject_nonpositive=False, reset_source_on_error=False):
    """解析年运行时长，优先级为 user_input > plant_info > project_manifest。

    `reject_nonpositive/reset_source_on_error=True` 是 generic facts 策略：
    非正数会被拒绝，转换失败时重置来源。默认保持 legacy fact-builder 策略：
    不拒绝 <=0，转换失败时仍保留来源。
    """
    if ui_value not in (None, ''):
        hours = ui_value; source = 'user_input'
    elif plant_info_hours is not None:
        hours = plant_info_hours; source = 'plant_info'
    else:
        hours = manifest_value
        source = 'project_manifest' if manifest_value not in (None, '') else None
    try:
        hours = float(hours) if hours is not None else None
        if reject_nonpositive and hours is not None and hours <= 0:
            hours = None; source = None
        if hours is not None and hours.is_integer():
            hours = int(hours)
    except Exception:
        hours = None
        if reset_source_on_error:
            source = None
    return hours, source


# ===== 章节状态区 =====

DISABLED_PREFIXES = ('disabled', 'not_applicable')


def is_disabled_status(status):
    return any(status.startswith(p) for p in DISABLED_PREFIXES)


def plan_status_map(plan):
    return {str(x.get('section')): str(x.get('status') or '') for x in ((plan or {}).get('chapter_plan') or [])}


def plan_entry_map(chapter_plan):
    return {str(x['section']): x for x in chapter_plan}


# ===== 设备与方案术语区 =====

def equipment_report_name(name):
    """Table-builder form: non-str passthrough, parallel suffix first."""
    if not isinstance(name, str):
        return name
    return name.replace('_并联', '（并联新增）').replace('_串联', '（串联新增）')


def equipment_display_name(name):
    """Report-model form: str coercion, series suffix first."""
    return str(name or '').replace('_串联', '（串联新增）').replace('_并联', '（串联新增）')


def reactor_action_category(group):
    return {'reuse': '利旧', 'addition': '改造', 'parallel': '无变化',
            'heat_transfer': '改造', 'no_retrofit': '无变化'}.get(group, '待确认')


def confirmed_status_category(status, default):
    s = str(status or '').strip()
    if s.startswith('新增'):
        return '新增'
    if s.startswith('改造'):
        return '改造'
    if s.startswith('利旧'):
        return '利旧'
    if s.startswith('无变化') or s.startswith('不改造'):
        return '无变化'
    return default


def fa_utility_margin_blocked():
    return {'status': 'blocked', 'reason': 'enterprise utility capacity/current load missing'}


# ===== 缺口路由区 =====

# Gap field -> affected report sections (single routing table shared by the
# generic fact builder and the gap analyzer).
GAP_AFFECTED_SECTIONS = {
    'construction_unit': ['1.1.1', '1.1.2'],
    'annual_operating_hours': ['3.1', '5.1', '10', '19', '21'],
    'process_case_result': ['3.1', '4.2.1', '4.2.2', '5.1'],
    'diagnosis_result': ['1.1.3', '4.1.3'],
    'adopted_topology_or_scheme': ['4.1.3', '4.1.4', '4.2.1'],
    'equipment_result': ['4.2.4', '19'],
    'utility_system_capacity/current_load': ['8.1', '10'],
    'equipment_tags_for_new_objects': ['4.2.4'],
    'energy_consumption_physical_quantities_and_boundary': ['10.2', '10.5', '10.6'],
    'general_layout_storage_pipe_network': ['7.1', '7.2', '7.3'],
    'investment_finance_rules': ['19', '20', '21', '26'],
}


# ===== 禁项规则区（原 production_safety.py，生产/测试隔离守卫） =====

TEST_MARKERS = (
    'test only', 'test publisher', 'test official publisher', 'test_fixture',
    'urn:test:', 'test_only', '测试正文', '测试证据',
    '仅用于验证host agent', '仅用于skill状态机', '不代表真实可研内容'
)


def iter_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from iter_strings(v)


def find_test_markers(obj: Any):
    hits=[]
    for s in iter_strings(obj):
        low=s.lower()
        for marker in TEST_MARKERS:
            if marker.lower() in low:
                hits.append((marker, s[:160]))
    return hits


def production_guard(obj: Any, label: str, mode: str='production'):
    if mode == 'test':
        return
    issues=[]
    if isinstance(obj, dict) and obj.get('test_only') is True:
        issues.append(f'{label}: top-level test_only=true')
    hits=find_test_markers(obj)
    for marker, sample in hits[:12]:
        issues.append(f'{label}: contains test marker {marker!r}: {sample!r}')
    if issues:
        raise ValueError('PRODUCTION_SAFETY_BLOCK: ' + ' | '.join(issues))
