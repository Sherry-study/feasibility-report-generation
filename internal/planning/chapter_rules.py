#!/usr/bin/env python3
"""Executable Chapter Rules runtime.

This module is the single runtime boundary between YAML chapter rules and
planning-stage consumers. It validates rule shape, selects rules through the
chapter plan, binds only declared fact paths, and evaluates missing required
facts without turning missing data into prose.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from internal.common import SKILL_ROOT, load_data
from internal.domain import is_disabled_status, plan_status_map

CONTRACT_VERSION = "1.0"
RULE_STATUSES = {"active", "provisional"}
RESPONSIBILITIES = {"U", "PA", "EA", "FA", "R", "C"}
MISSING_ACTIONS = {
    "ask_user",
    "confirmation_required",
    "research_required",
    "waiting_upstream",
    "calculation_blocked",
    "allow_open_item",
}
RESEARCH_MODES = {"none", "required", "conditional"}
COVERAGE_CLASSES = {"llm_narrative", "deterministic_only", "plan_parent_or_range", "template_rendered"}
IGNORED_RULE_FILES = {"_template.yaml", "README.md", "fact_binding_spec.yaml"}
RAW_ALGORITHM_ROOTS = {"plant_level_result", "retrofit_equipment", "scheme"}
ALLOWED_ROOTS = {
    "adopted_scheme",
    "consistency",
    "diagnosis",
    "energy",
    "enterprise",
    "equipment",
    "fa",
    "finance",
    "implementation_schedule",
    "process",
    "project",
    "scheme_analysis",
    "user",
}

LLM_NARRATIVE_SECTIONS = {
    "1.1.2",
    "1.1.3",
    "1.2",
    "2",
    "4.1.1",
    "4.1.2",
    "4.1.3.2",
    "18.2",
    "26.1",
    "26.2",
}
# 规范引用型章节：正文以规范引用、依托既有条件与专业校核口径为主，无项目特异叙述
# 价值，由 Report Model 的确定性模板文字渲染，不进入 llm_jobs。
TEMPLATE_RENDERED_SECTIONS = {"4.4", "7.1", "7.2", "7.3", "10.3", "10.6"}
DETERMINISTIC_ONLY_SECTIONS = {"3.1", "4.1.3", "4.1.4", "4.2.4", "5.1", "5.4", "8.1", "10.1", "10.2", "10.4", "10.5", "19", "20", "21"}
PLAN_PARENT_OR_RANGE_SECTIONS = {"1.1", "10.1-10.6", "18", "26"}
APPROVED_COVERAGE = {
    **{sid: "llm_narrative" for sid in LLM_NARRATIVE_SECTIONS},
    **{sid: "template_rendered" for sid in TEMPLATE_RENDERED_SECTIONS},
    **{sid: "deterministic_only" for sid in DETERMINISTIC_ONLY_SECTIONS},
    **{sid: "plan_parent_or_range" for sid in PLAN_PARENT_OR_RANGE_SECTIONS},
}

PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[\])?)*$")
HEADING_RE = re.compile(r"^\d+(?:\.\d+)*\s+\S")


class ChapterRuleError(ValueError):
    """Raised when executable chapter rules are invalid."""


def load_chapter_rules(rule_dir=None):
    """Load and validate executable rule files from knowledge/chapter_rules."""
    base = Path(rule_dir) if rule_dir else SKILL_ROOT / "knowledge" / "chapter_rules"
    if not base.is_dir():
        raise ChapterRuleError(f"chapter rule directory not found: {base}")
    docs = []
    for path in sorted(base.iterdir()):
        if path.name in IGNORED_RULE_FILES or path.suffix.lower() not in (".yaml", ".yml"):
            continue
        data = load_data(path)
        if "chapter_id" in data or "output_structure" in data:
            raise ChapterRuleError(f"{path.name}: legacy chapter rule shape is not executable")
        data["_file"] = path.name
        docs.append(data)
    registry = {"contract_version": CONTRACT_VERSION, "rule_dir": str(base), "documents": docs}
    validate_chapter_rules(registry)
    return registry


def _err(errors, file_name, section_id, message):
    prefix = file_name or "<unknown>"
    if section_id:
        prefix += f"[{section_id}]"
    errors.append(f"{prefix}: {message}")


def _validate_path(path, errors, file_name, section_id):
    if not isinstance(path, str) or not PATH_RE.match(path):
        _err(errors, file_name, section_id, f"invalid fact path syntax: {path!r}")
        return
    root = path.split(".", 1)[0].replace("[]", "")
    if root in RAW_ALGORITHM_ROOTS:
        _err(errors, file_name, section_id, f"raw algorithm root is forbidden: {root}")
    if root not in ALLOWED_ROOTS:
        _err(errors, file_name, section_id, f"unsupported fact path root: {root}")


def _validate_section(doc, section, errors, seen_sections):
    file_name = doc.get("_file")
    sid = str(section.get("section_id") or "")
    if not sid:
        _err(errors, file_name, None, "section_id is required")
        return
    if sid in seen_sections:
        _err(errors, file_name, sid, "duplicate section_id")
    seen_sections.add(sid)

    coverage = section.get("coverage_class")
    if coverage not in COVERAGE_CLASSES:
        _err(errors, file_name, sid, f"invalid coverage_class: {coverage!r}")
    expected_coverage = APPROVED_COVERAGE.get(sid)
    if expected_coverage is None:
        _err(errors, file_name, sid, "section_id is outside the approved coverage surface")
    elif coverage != expected_coverage:
        _err(errors, file_name, sid, f"coverage_class must be {expected_coverage}, got {coverage}")

    has_research_required_fact = False
    for fact in section.get("required_facts") or []:
        resp = fact.get("responsibility")
        if not isinstance(resp, list) or not resp:
            _err(errors, file_name, sid, f"{fact.get('fact_id')}: responsibility must be a non-empty list")
        else:
            bad = [x for x in resp if x not in RESPONSIBILITIES]
            if bad:
                _err(errors, file_name, sid, f"{fact.get('fact_id')}: invalid responsibility values: {bad}")
        action = fact.get("missing_action")
        if action not in MISSING_ACTIONS:
            _err(errors, file_name, sid, f"{fact.get('fact_id')}: invalid missing_action: {action!r}")
        if action == "research_required":
            has_research_required_fact = True
        paths = fact.get("source_paths") or []
        if not paths:
            _err(errors, file_name, sid, f"{fact.get('fact_id')}: source_paths is required")
        for path in paths:
            _validate_path(path, errors, file_name, sid)

    for path in section.get("context_paths") or []:
        _validate_path(path, errors, file_name, sid)

    research = section.get("research")
    if not isinstance(research, dict):
        _err(errors, file_name, sid, "research must be an explicit mapping")
        research = {}
    required_research_keys = {"mode", "task_templates", "condition_key", "policy_hook", "target_sections"}
    missing_research_keys = sorted(required_research_keys - set(research))
    if missing_research_keys:
        _err(errors, file_name, sid, f"research missing required keys: {missing_research_keys}")
    if research.get("mode") not in RESEARCH_MODES:
        _err(errors, file_name, sid, f"invalid research.mode: {research.get('mode')!r}")
    if not isinstance(research.get("task_templates"), list):
        _err(errors, file_name, sid, "research.task_templates must be a list")
    if research.get("condition_key") is not None and not isinstance(research.get("condition_key"), str):
        _err(errors, file_name, sid, "research.condition_key must be null or string")
    if research.get("policy_hook") is not None and not isinstance(research.get("policy_hook"), str):
        _err(errors, file_name, sid, "research.policy_hook must be null or string")
    if not isinstance(research.get("target_sections"), list):
        _err(errors, file_name, sid, "research.target_sections must be a list")
    if has_research_required_fact:
        has_task_source = bool(research.get("task_templates") or research.get("policy_hook"))
        if research.get("mode") == "none" or not has_task_source:
            _err(errors, file_name, sid, "research_required facts require research.mode and task_templates or policy_hook")

    output = section.get("output") or {}
    if coverage == "llm_narrative":
        allowed = output.get("allowed_headings") or []
        structure = output.get("required_structure") or []
        if not allowed or not structure:
            _err(errors, file_name, sid, "active narrative section requires allowed_headings and required_structure")
        if len(allowed) != len(set(allowed)):
            _err(errors, file_name, sid, "duplicate allowed_headings")
        for heading in allowed:
            if not HEADING_RE.match(str(heading)):
                _err(errors, file_name, sid, f"heading must be exact numbered text: {heading!r}")
        if doc.get("rule_status") == "provisional":
            forbidden_terms = ("IRR", "NPV", "投资回收期", "项目总投资", "利润", "收入", "成本")
            text = "\n".join(str(x) for x in allowed + [r for item in structure for r in item.get("requirements", [])])
            if any(term in text for term in forbidden_terms):
                _err(errors, file_name, sid, "provisional rule contains deterministic financial conclusion wording")


def validate_chapter_rules(registry):
    errors = []
    seen_rules = set()
    seen_sections = set()
    classified = set()
    for doc in registry.get("documents") or []:
        file_name = doc.get("_file")
        if doc.get("contract_version") != CONTRACT_VERSION:
            _err(errors, file_name, None, f"contract_version must be {CONTRACT_VERSION}")
        rid = doc.get("rule_id")
        if not rid:
            _err(errors, file_name, None, "rule_id is required")
        elif rid in seen_rules:
            _err(errors, file_name, None, f"duplicate rule_id: {rid}")
        seen_rules.add(rid)
        if doc.get("rule_status") not in RULE_STATUSES:
            _err(errors, file_name, None, f"invalid rule_status: {doc.get('rule_status')!r}")
        if not doc.get("plan_section"):
            _err(errors, file_name, None, "plan_section is required")
        sections = doc.get("sections")
        if not isinstance(sections, list) or not sections:
            _err(errors, file_name, None, "sections must be a non-empty list")
            continue
        for section in sections:
            _validate_section(doc, section, errors, seen_sections)
            if section.get("coverage_class") != "plan_parent_or_range":
                classified.add(str(section.get("section_id")))

    missing = sorted(LLM_NARRATIVE_SECTIONS - seen_sections)
    if missing:
        errors.append(f"missing llm_narrative rule coverage: {missing}")
    missing_det = sorted(DETERMINISTIC_ONLY_SECTIONS - classified)
    if missing_det:
        errors.append(f"missing deterministic_only coverage declarations: {missing_det}")
    missing_tpl = sorted(TEMPLATE_RENDERED_SECTIONS - classified)
    if missing_tpl:
        errors.append(f"missing template_rendered coverage declarations: {missing_tpl}")
    if errors:
        raise ChapterRuleError("; ".join(errors))
    return True


def _section_status(section_id, pmap):
    sid = str(section_id)
    if sid in pmap:
        return pmap[sid]
    candidates = []
    for key, status in pmap.items():
        key = str(key)
        if "-" not in key and (sid == key or sid.startswith(key + ".")):
            candidates.append((len(key), status))
        elif key == "10.1-10.6" and sid.startswith("10."):
            second = sid.split(".")[1]
            if second.isdigit() and 1 <= int(second) <= 6:
                candidates.append((len(key), status))
    return max(candidates, default=(0, ""))[1]


def select_active_rules(registry, chapter_plan):
    """Return section rules not suppressed by disabled exact, parent or range plan entries."""
    pmap = plan_status_map(chapter_plan)
    selected = []
    for doc in registry.get("documents") or []:
        for section in doc.get("sections") or []:
            sid = str(section.get("section_id"))
            coverage = section.get("coverage_class")
            status = _section_status(sid, pmap)
            if status and is_disabled_status(status):
                continue
            item = deepcopy(section)
            item["rule_id"] = doc.get("rule_id")
            item["rule_status"] = doc.get("rule_status")
            item["rule_version"] = doc.get("contract_version")
            item["plan_section"] = doc.get("plan_section")
            item["coverage_class"] = coverage
            item["plan_status"] = status
            item["rule_file"] = doc.get("_file")
            selected.append(item)
    return selected


def _values_for_token(value, token):
    is_array = token.endswith("[]")
    key = token[:-2] if is_array else token
    if isinstance(value, dict):
        nxt = value.get(key)
    elif isinstance(value, list):
        nxt = [x.get(key) for x in value if isinstance(x, dict)]
    else:
        return []
    if is_array:
        if isinstance(nxt, list):
            return nxt
        return [] if nxt in (None, "") else [nxt]
    return [nxt] if not isinstance(nxt, list) else nxt


def resolve_fact_path(facts, path):
    values = [facts]
    for token in str(path).split("."):
        next_values = []
        for value in values:
            next_values.extend(_values_for_token(value, token))
        values = [x for x in next_values if x not in (None, "", [], {})]
        if not values:
            return None
    return values if len(values) != 1 else values[0]


def _facts_with_profile(profile, facts):
    merged = deepcopy(facts or {})
    p = profile.get("project_profile", profile) if isinstance(profile, dict) else {}
    project = dict(merged.get("project") or {})
    for key in ("project_id", "project_name", "project_type", "project_level", "retrofit_scope", "project_location"):
        if project.get(key) in (None, "") and p.get(key) not in (None, ""):
            project[key] = p.get(key)
    if p.get("objectives") and not project.get("objectives"):
        project["objectives"] = p.get("objectives")
    if p.get("changes") and not project.get("changes"):
        project["changes"] = p.get("changes")
    merged["project"] = project
    return merged


def bind_section_context(section_rule, profile, facts):
    merged = _facts_with_profile(profile, facts)
    paths = {}
    for path in section_rule.get("context_paths") or []:
        value = resolve_fact_path(merged, path)
        if value not in (None, "", [], {}):
            paths[path] = value
    return {"paths": paths, "open_items": []}


def evaluate_required_facts(section_rule, facts):
    findings = []
    for fact in section_rule.get("required_facts") or []:
        if fact.get("required") is False:
            continue
        paths = fact.get("source_paths") or []
        present = any(resolve_fact_path(facts, path) not in (None, "", [], {}) for path in paths)
        if present:
            continue
        findings.append({
            "section_id": str(section_rule.get("section_id")),
            "rule_id": section_rule.get("rule_id"),
            "rule_status": section_rule.get("rule_status"),
            "fact_id": fact.get("fact_id"),
            "description": fact.get("description"),
            "responsibility": fact.get("responsibility") or [],
            "missing_action": fact.get("missing_action"),
            "source_paths": paths,
            "reason": fact.get("reason") or "required fact is absent from confirmed project facts",
        })
    return findings


def rule_entry_map(registry):
    entries = {}
    coverage = {"llm_narrative": [], "deterministic_only": [], "plan_parent_or_range": [], "template_rendered": []}
    for doc in registry.get("documents") or []:
        for section in doc.get("sections") or []:
            sid = str(section.get("section_id"))
            cls = section.get("coverage_class")
            entries[sid] = {"rule_id": doc.get("rule_id"), "rule_status": doc.get("rule_status"), "coverage_class": cls}
            if cls in coverage:
                coverage[cls].append(sid)
    return {"entries": entries, "coverage": {k: sorted(v) for k, v in coverage.items()}}


def missing_findings_by_section(rules, facts):
    by_section = {}
    for rule in rules:
        findings = evaluate_required_facts(rule, facts)
        if findings:
            by_section[str(rule.get("section_id"))] = findings
    return by_section
