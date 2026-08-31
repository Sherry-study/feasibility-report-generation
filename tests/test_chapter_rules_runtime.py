from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from argparse import Namespace
from pathlib import Path

import yaml

from internal.common import EXIT_NEEDS_LLM, EXIT_NEEDS_RESEARCH, dump_json, load_data
from internal.planning.chapter_rules import (
    ChapterRuleError,
    LLM_NARRATIVE_SECTIONS,
    TEMPLATE_RENDERED_SECTIONS,
    bind_section_context,
    load_chapter_rules,
    resolve_fact_path,
    rule_entry_map,
    select_active_rules,
)
from internal.planning.gaps import analyze
from internal.planning.llm_jobs import build_jobs
from internal.planning.research_tasks import build_tasks
from internal.facts.sources import infer_project_profile
from internal.planning.draft_fragments import (
    collect,
    plan_batches,
    plan_worker_batches,
    submit,
    validate_fragment,
    write_context_packs,
    write_worker_packs,
)
from internal.planning.research_fragments import collect_research, submit_research, write_research_packs, write_research_host_workflow
from internal.planning.stage import run_chapter_planning_stage
from internal.report.stage import run_report_generation_stage
from internal.report.validate_drafts import validate as validate_drafts


PROFILE = {
    "project_profile": {
        "project_id": "P-001",
        "project_name": "示例改造项目",
        "project_type": "capacity_expansion",
        "project_level": "unit",
        "changes": {"capacity_changed": True},
    }
}


RESEARCH_TASKS = {
    "contract_version": "1.0",
    "project_id": "P-001",
    "tasks": [
        {
            "task_id": "R-A",
            "section_id": "1.2",
            "research_type": "enterprise_profile",
            "subject": "示例企业",
            "questions": ["企业基本情况？"],
            "suggested_queries": ["示例企业 官网"],
            "preferred_sources": ["官方网站"],
            "minimum_sources": 2,
            "requires_official_source": False,
            "target_sections": ["1.2"],
        },
        {
            "task_id": "R-B",
            "section_id": "2",
            "research_type": "market_forecast",
            "subject": "示例产品",
            "questions": ["市场情况？"],
            "suggested_queries": ["示例产品 市场"],
            "preferred_sources": ["政府统计"],
            "minimum_sources": 1,
            "requires_official_source": True,
            "target_sections": ["2"],
        },
    ],
}


def _ev(eid, tid, url, stype="industry_report"):
    return {
        "evidence_id": eid,
        "task_id": tid,
        "claim": "示例论断",
        "source_title": "来源-" + eid,
        "publisher": "发布方-" + eid,
        "source_url": url,
        "source_type": stype,
        "content_summary": "内容摘要",
    }


PLAN = {
    "chapter_plan": [
        {"section": "1.1", "status": "full"},
        {"section": "2", "status": "full"},
        {"section": "3.1", "status": "full"},
        {"section": "4.1.1", "status": "concise"},
        {"section": "4.1.2", "status": "concise"},
        {"section": "4.1.3", "status": "full"},
        {"section": "4.4", "status": "conditional"},
        {"section": "5.1", "status": "full"},
        {"section": "7.1", "status": "concise"},
        {"section": "7.2", "status": "concise"},
        {"section": "7.3", "status": "concise"},
        {"section": "8.1", "status": "screening_required"},
        {"section": "10.1-10.6", "status": "full"},
        {"section": "18", "status": "full_or_blocked"},
        {"section": "19", "status": "full_or_blocked"},
        {"section": "20", "status": "full_or_blocked"},
        {"section": "21", "status": "full_or_blocked"},
        {"section": "26", "status": "summary_gate"},
    ]
}


FACTS = {
    "project": {"project_id": "P-001", "project_name": "示例改造项目", "construction_unit": "示例公司"},
    "process": {"retrofit": {"product_streams": [{"name": "产品"}]}},
    "adopted_scheme": {"scheme_name": "采用方案", "description": "已确认"},
    "scheme_analysis": {"summary": "方案比较"},
    "equipment": {"reactor": {"utility_requirements": [{"medium": "电"}]}},
    "energy": {"consumption": [{"medium": "电力"}], "accounting_media": ["电力"]},
    "fa": {},
    "user": {"annual_operating_hours": 8000},
}


def _write_registry_docs(base, docs):
    for index, doc in enumerate(docs, start=1):
        data = {k: v for k, v in doc.items() if k != "_file"}
        name = doc.get("_file") or f"rule_{index}.yaml"
        (base / name).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


class ChapterRulesRuntimeTests(unittest.TestCase):
    def test_rules_load_and_cover_current_writing_surface(self):
        registry = load_chapter_rules()
        mapping = rule_entry_map(registry)
        self.assertEqual(set(mapping["coverage"]["llm_narrative"]), LLM_NARRATIVE_SECTIONS)
        self.assertEqual(len(mapping["coverage"]["deterministic_only"]), 14)
        self.assertEqual(set(mapping["coverage"]["template_rendered"]), TEMPLATE_RENDERED_SECTIONS)

    def test_template_rendered_sections_are_excluded_from_jobs(self):
        jobs = build_jobs(PROFILE, PLAN, FACTS, {"tasks": []}, {"items": []})
        job_sections = {x["section_id"] for x in jobs["jobs"]}
        self.assertFalse(job_sections & TEMPLATE_RENDERED_SECTIONS)
        self.assertFalse(
            {x["section_id"] for x in jobs["skipped_sections"]} & TEMPLATE_RENDERED_SECTIONS
        )

    def test_legacy_draft_for_template_rendered_section_is_ignored_not_rejected(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [{"section_id": "1.2", "allowed_headings": ["1.2 研究结论"], "research_task_ids": []}]}
        evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
        drafts = {"contract_version": "1.0", "project_id": "P-001", "drafts": [
            {"section_id": "1.2", "draft_status": "draft", "subsections": [{"heading": "1.2 研究结论", "paragraphs": ["正文"]}], "source_evidence_ids": [], "claims": [], "open_items": []},
            {"section_id": "7.1", "draft_status": "draft", "subsections": [{"heading": "7.1 总图运输", "paragraphs": ["历史草稿"]}], "source_evidence_ids": [], "claims": [], "open_items": []},
        ]}
        result, code = validate_drafts(jobs, evidence, drafts, "production")
        self.assertEqual(code, 0)
        self.assertTrue(result["valid"])

    def test_disabled_parent_suppresses_child_rules(self):
        registry = load_chapter_rules()
        plan = {"chapter_plan": [{"section": "26", "status": "disabled"}]}
        selected = select_active_rules(registry, plan)
        self.assertNotIn("26.1", {x["section_id"] for x in selected})
        self.assertNotIn("26.2", {x["section_id"] for x in selected})

    def test_allowed_heading_change_flows_to_job(self):
        registry = load_chapter_rules()
        rule = next(x for x in select_active_rules(registry, PLAN) if x["section_id"] == "1.2")
        rule["output"]["allowed_headings"] = ["1.2 修改后的研究结论"]
        original = load_chapter_rules
        try:
            import internal.planning.llm_jobs as llm_jobs
            llm_jobs.load_chapter_rules = lambda: {"documents": [{"rule_id": rule["rule_id"], "rule_status": rule["rule_status"], "contract_version": "1.0", "plan_section": "1.1", "sections": [rule]}]}
            jobs = llm_jobs.build_jobs(PROFILE, PLAN, FACTS, {"tasks": []}, {"items": []})
            self.assertIn("1.2 修改后的研究结论", jobs["jobs"][0]["allowed_headings"])
        finally:
            llm_jobs.load_chapter_rules = original

    def test_missing_blocked_fact_skips_job(self):
        facts = dict(FACTS)
        facts.pop("adopted_scheme", None)
        facts.pop("scheme_analysis", None)
        jobs = build_jobs(PROFILE, PLAN, facts, {"tasks": []}, {"items": []})
        self.assertNotIn("4.1.3", {x["section_id"] for x in jobs["jobs"]})
        self.assertNotIn("4.1.3", {x["section_id"] for x in jobs["skipped_sections"]})

    def test_deterministic_102_keeps_gap_without_llm_job(self):
        facts = dict(FACTS)
        facts["energy"] = {}
        jobs = build_jobs(PROFILE, PLAN, facts, {"tasks": []}, {"items": []})
        self.assertNotIn("10.2", {x["section_id"] for x in jobs["jobs"]})
        gaps = analyze(facts, PLAN)
        self.assertIn("10.2", {x.get("section_id") for x in gaps["rule_missing_facts"]})

    def test_gap_analysis_records_rule_missing_and_blocked_sections(self):
        facts = dict(FACTS)
        facts.pop("adopted_scheme", None)
        facts.pop("scheme_analysis", None)
        result = analyze(facts, PLAN)
        self.assertTrue(result["rule_missing_facts"])
        self.assertIn("4.1.3", {x["section_id"] for x in result["blocked_sections"]})

    def test_invalid_legacy_or_raw_rule_fails_fast(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "bad.yaml").write_text("chapter_id: bad\noutput_structure: []\n", encoding="utf-8")
            with self.assertRaises(ChapterRuleError):
                load_chapter_rules(base)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            doc = {
                "contract_version": "1.0",
                "rule_id": "bad",
                "rule_status": "active",
                "plan_section": "1.2",
                "sections": [{
                    "section_id": sid,
                    "title": sid,
                    "coverage_class": "llm_narrative",
                    "required_facts": [{"fact_id": "x", "source_paths": ["scheme.value"], "responsibility": ["U"], "required": True, "missing_action": "ask_user"}],
                    "context_paths": [],
                    "output": {"allowed_headings": [f"{sid} 标题"], "required_structure": [{"heading": f"{sid} 标题", "requirements": ["x"]}]},
                    "research": {"mode": "none"},
                } for sid in sorted(LLM_NARRATIVE_SECTIONS)]
            }
            (base / "bad.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
            with self.assertRaises(ChapterRuleError):
                load_chapter_rules(base)

    def test_validator_rejects_missing_research_contract_and_bad_coverage(self):
        docs = deepcopy(load_chapter_rules()["documents"])
        docs[0]["sections"][0]["research"].pop("task_templates", None)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _write_registry_docs(base, docs)
            with self.assertRaises(ChapterRuleError):
                load_chapter_rules(base)

        docs = deepcopy(load_chapter_rules()["documents"])
        docs[0]["sections"][0]["coverage_class"] = "deterministic_only"
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _write_registry_docs(base, docs)
            with self.assertRaises(ChapterRuleError):
                load_chapter_rules(base)

    def test_generic_ask_user_finding_creates_deduped_deferred_question_and_skips_job(self):
        facts = deepcopy(FACTS)
        facts["user"].pop("annual_operating_hours", None)
        result = analyze(facts, PLAN)
        annual = [x for x in result["deferred_questions"] if x["field"] == "annual_operating_hours"]
        self.assertEqual(len(annual), 1)
        self.assertIn("3.1", annual[0]["affected_sections"])

        jobs = build_jobs(PROFILE, PLAN, facts, {"tasks": []}, {"items": []})
        self.assertNotIn("3.1", {x["section_id"] for x in jobs["jobs"]})

    def test_confirmation_required_routes_to_existing_user_input_gate(self):
        facts = deepcopy(FACTS)
        facts.pop("adopted_scheme", None)
        facts.pop("scheme_analysis", None)

        result = analyze(facts, PLAN)
        questions = [
            item for item in result["deferred_questions"]
            if item.get("missing_action") == "confirmation_required"
        ]

        self.assertEqual(len(questions), 1)
        self.assertTrue(questions[0]["blocking"])
        self.assertEqual(questions[0]["affected_sections"], ["4.1.3"])
        self.assertIn("4.1.3", {x["section_id"] for x in result["blocked_sections"]})

    def test_upstream_and_calculation_actions_block_without_creating_jobs(self):
        import internal.planning.llm_jobs as llm_jobs

        original = llm_jobs.load_chapter_rules
        try:
            for action in ("waiting_upstream", "calculation_blocked"):
                rule = {
                    "rule_id": f"test_{action}",
                    "rule_status": "active",
                    "contract_version": "1.0",
                    "plan_section": "1.2",
                    "sections": [{
                        "section_id": "1.2",
                        "title": "研究结论",
                        "coverage_class": "llm_narrative",
                        "required_facts": [{
                            "fact_id": "required_input",
                            "description": "必需输入",
                            "source_paths": ["user.required_input"],
                            "responsibility": ["PA"] if action == "waiting_upstream" else ["FA"],
                            "required": True,
                            "missing_action": action,
                        }],
                        "context_paths": [],
                        "output": {
                            "allowed_headings": ["1.2 研究结论"],
                            "required_structure": [{
                                "heading": "1.2 研究结论",
                                "requirements": ["仅使用已确认输入。"],
                            }],
                        },
                        "research": {
                            "mode": "none",
                            "task_templates": [],
                            "condition_key": None,
                            "policy_hook": None,
                            "target_sections": [],
                        },
                    }],
                }
                llm_jobs.load_chapter_rules = lambda rule=rule: {"documents": [rule]}
                result = llm_jobs.build_jobs(
                    PROFILE, PLAN, FACTS, {"tasks": []}, {"items": []}
                )
                self.assertFalse(result["jobs"])
                self.assertEqual(
                    result["skipped_sections"][0]["missing_facts"][0]["missing_action"],
                    action,
                )
        finally:
            llm_jobs.load_chapter_rules = original

    def test_representative_planning_run_reaches_research_gate_with_rule_ids(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            profile = out / "profile.json"
            facts = out / "facts.json"
            dump_json(PROFILE, profile)
            dump_json(FACTS, facts)
            args = Namespace(
                profile=str(profile),
                facts=str(facts),
                skip_user_inputs=True,
                research_evidence=None,
                section_drafts=None,
                ai_mode="host_agent",
                run_mode="test",
            )

            code, summary = run_chapter_planning_stage(args, out)
            tasks = load_data(summary["research_tasks"])["tasks"]

            self.assertEqual(code, EXIT_NEEDS_RESEARCH)
            self.assertEqual(summary["status"], "needs_research")
            self.assertTrue(tasks)
            self.assertTrue(all(task.get("rule_id") for task in tasks))

    def test_chapter_104_does_not_create_external_energy_benchmark_task(self):
        profile = deepcopy(PROFILE)
        profile["project_profile"]["project_type"] = "energy_saving"
        profile["project_profile"]["objectives"] = ["energy_saving", "efficiency"]
        profile["project_profile"]["changes"]["utility_demand_changed"] = True

        registry = load_chapter_rules()
        rule = next(
            section
            for doc in registry["documents"]
            for section in doc["sections"]
            if section["section_id"] == "10.4"
        )
        self.assertEqual(rule["research"]["mode"], "none")
        self.assertIsNone(rule["research"]["policy_hook"])

        tasks = build_tasks(profile, PLAN, FACTS)
        self.assertNotIn("R-ENE-104-01", {task["task_id"] for task in tasks["tasks"]})
        self.assertNotIn("10.4", {task["section_id"] for task in tasks["tasks"]})

        jobs = build_jobs(profile, PLAN, FACTS, tasks, {"items": []})
        self.assertNotIn("10.4", {item["section_id"] for item in jobs["jobs"]})

    def test_413_parent_is_deterministic_and_has_no_research_task(self):
        tasks = build_tasks(PROFILE, PLAN, FACTS)
        self.assertNotIn("R-TECH-0413-01", {task["task_id"] for task in tasks["tasks"]})
        jobs = build_jobs(PROFILE, PLAN, FACTS, tasks, {"items": []})
        sections = {job["section_id"] for job in jobs["jobs"]}
        self.assertNotIn("4.1.3", sections)
        self.assertIn("4.1.3.2", sections)

    def test_chapter_18_keeps_research_while_19_and_21_are_writing_only(self):
        tasks = build_tasks(PROFILE, PLAN, FACTS)
        task_ids = {task["task_id"] for task in tasks["tasks"]}
        self.assertIn("R-IMP-0182-01", task_ids)
        self.assertNotIn("R-INV-019-01", task_ids)
        self.assertNotIn("R-FIN-021-01", task_ids)

        jobs = build_jobs(PROFILE, PLAN, FACTS, tasks, {"items": []})
        by_section = {job["section_id"]: job for job in jobs["jobs"]}
        self.assertEqual(by_section["18.2"]["research_task_ids"], ["R-IMP-0182-01"])
        self.assertNotIn("19", by_section)
        self.assertNotIn("21", by_section)

    def test_algorithm_outputs_can_infer_profile_without_external_profile_file(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "plant_info.json"
            dump_json({
                "plant_info": {
                    "plant_name": "示例异戊烯装置",
                    "annual_operating_hours": 8000,
                    "design_case": {"products": [{"name": "异戊烯", "is_main_product": True, "annual_capacity": {"value": 10000}}]},
                    "retrofit_case": {"products": [{"name": "异戊烯", "is_main_product": True, "annual_capacity": {"value": 20000}}]},
                }
            }, source)
            registry = {
                "project": {
                    "project_id": "P-001", "project_name": "workspace",
                    "project_name_source": "workspace_dir", "project_level": "unit", "project_type": "mixed",
                },
                "selected_sources": {"plant_info": {"source_type": "plant_info", "path": str(source)}},
            }
            profile = infer_project_profile(registry)
            self.assertEqual(profile["project_name"], "示例异戊烯装置")
            self.assertEqual(profile["project_name_source"], "algorithm_output")
            self.assertEqual(profile["project_type"], "capacity_expansion")
            self.assertTrue(profile["changes"]["capacity_changed"])

    def test_research_required_template_blocks_without_evidence_and_allows_with_evidence(self):
        rule = {
            "rule_id": "chapter_test_research",
            "rule_status": "active",
            "contract_version": "1.0",
            "plan_section": "1.2",
            "sections": [{
                "section_id": "1.2",
                "title": "研究结论",
                "coverage_class": "llm_narrative",
                "required_facts": [{
                    "fact_id": "external_basis",
                    "description": "外部研究依据",
                    "source_paths": ["user.external_basis"],
                    "responsibility": ["R"],
                    "required": True,
                    "missing_action": "research_required",
                }],
                "context_paths": ["project.project_name"],
                "output": {"allowed_headings": ["1.2 研究结论"], "required_structure": [{"heading": "1.2 研究结论", "requirements": ["基于研究依据写作。"]}]},
                "research": {
                    "mode": "required",
                    "task_templates": [{
                        "task_id": "R-GEN-001",
                        "section_id": "1.2",
                        "research_type": "external_basis",
                        "subject": "{project_name}",
                        "questions": ["需要补充什么外部依据？"],
                        "suggested_queries": ["{project_name} 外部依据"],
                        "preferred_sources": ["官方资料"],
                        "minimum_sources": 1,
                        "target_sections": ["1.2"],
                    }],
                    "condition_key": None,
                    "policy_hook": None,
                    "target_sections": ["1.2"],
                },
                "generation_rules": {"must_include": [], "forbidden": []},
                "quality_checks": [],
            }],
        }
        fake_registry = {"documents": [rule]}
        import internal.planning.research_tasks as research_tasks
        import internal.planning.llm_jobs as llm_jobs
        old_tasks_loader = research_tasks.load_chapter_rules
        old_jobs_loader = llm_jobs.load_chapter_rules
        try:
            research_tasks.load_chapter_rules = lambda: fake_registry
            llm_jobs.load_chapter_rules = lambda: fake_registry
            tasks = build_tasks(PROFILE, PLAN, FACTS)
            self.assertEqual(tasks["tasks"][0]["rule_id"], "chapter_test_research")
            no_evidence = build_jobs(PROFILE, PLAN, FACTS, tasks, {"items": []})
            self.assertIn("1.2", {x["section_id"] for x in no_evidence["skipped_sections"]})
            evidence = {"items": [{"task_id": "R-GEN-001", "evidence_id": "E-1"}]}
            with_evidence = build_jobs(PROFILE, PLAN, FACTS, tasks, evidence)
            self.assertIn("1.2", {x["section_id"] for x in with_evidence["jobs"]})
        finally:
            research_tasks.load_chapter_rules = old_tasks_loader
            llm_jobs.load_chapter_rules = old_jobs_loader

    def test_resolve_fact_path_handles_arrays(self):
        data = {"equipment": {"reactor": {"rows": [{"tag": "R1"}, {"tag": "R2"}]}}}
        self.assertEqual(resolve_fact_path(data, "equipment.reactor.rows[].tag"), ["R1", "R2"])
        rule = {"context_paths": ["project.project_name", "equipment.reactor.rows[].tag"]}
        ctx = bind_section_context(rule, PROFILE, data)
        self.assertEqual(ctx["paths"]["project.project_name"], "示例改造项目")

    def test_context_packs_are_self_contained_and_digest_lists_all_jobs(self):
        jobs = build_jobs(PROFILE, PLAN, FACTS, {"tasks": []}, {"items": []})
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "draft_fragments"
            info = write_context_packs(jobs, fdir)
            self.assertEqual(info["packs"], len(jobs["jobs"]))
            cdir = Path(info["contexts_dir"])
            digest = Path(info["digest"]).read_text(encoding="utf-8")
            for job in jobs["jobs"]:
                sid = job["section_id"]
                pack = load_data(cdir / f"job_{sid.replace('.', '_')}.json")
                # 自包含：系统指令、标题约束、章节上下文与输出契约齐备，撰写无需其他文件
                self.assertEqual(pack["section_id"], sid)
                self.assertIn("system_instruction", pack)
                self.assertIn("allowed_headings", pack)
                self.assertIn("chapter_context", pack)
                self.assertIn("output_contract", pack)
                self.assertIn(f"| {sid} ", digest)
            # contexts/ 是子目录：不被收集器当作草稿分片读入
            result, code = collect(fdir, jobs, {"items": []}, "production")
            self.assertEqual(code, 4)
            self.assertTrue(result["missing"])
            self.assertNotIn("digest.md", result["fragments"])

    def test_worker_planning_is_deterministic_and_uses_three_non_empty_workers(self):
        def job(sid, payload_size, structures=1):
            return {
                "section_id": sid,
                "title": sid,
                "system_instruction": "sys",
                "chapter_context": {"paths": {"project.project_name": "P", f"process.section_{sid.replace('.', '_')}": "x" * payload_size}, "open_items": []},
                "project_context": {"paths": {"project.project_name": "P"}, "open_items": []},
                "research_evidence": [],
                "required_structure": [{"heading": f"{sid} 标题", "requirements": ["x"]} for _ in range(structures)],
                "allowed_headings": [f"{sid} 标题"],
                "generation_rules": {},
                "quality_checks": [],
            }
        payload = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            job("1.1", 12000, 3),
            job("1.2", 300, 1),
            job("1.3", 300, 1),
            job("1.4", 300, 1),
            job("1.5", 300, 1),
            job("1.6", 300, 1),
        ]}
        first = plan_worker_batches(payload)
        second = plan_worker_batches(payload)
        self.assertEqual(first, second)
        self.assertEqual(len(first["batches"]), 3)
        self.assertTrue(all(batch["section_ids"] for batch in first["batches"]))
        self.assertNotEqual(sorted(len(batch["section_ids"]) for batch in first["batches"]), [2, 2, 2])
        self.assertEqual(plan_batches(payload), first)
        self.assertEqual(first["assignment_strategy"], "greedy_by_slimmed_context_increment_plus_estimated_output")

    def test_worker_planning_uses_fewer_workers_when_jobs_are_fewer_than_three(self):
        payload = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            {"section_id": "1.2", "title": "1.2", "system_instruction": "sys", "chapter_context": {"paths": {}, "open_items": []}, "project_context": {"paths": {}, "open_items": []}, "research_evidence": [], "required_structure": [], "allowed_headings": []},
            {"section_id": "2", "title": "2", "system_instruction": "sys", "chapter_context": {"paths": {}, "open_items": []}, "project_context": {"paths": {}, "open_items": []}, "research_evidence": [], "required_structure": [], "allowed_headings": []},
        ]}
        plan = plan_worker_batches(payload)
        self.assertEqual(len(plan["batches"]), 2)
        self.assertEqual(plan["jobs_total"], 2)
        self.assertTrue(all(batch["section_ids"] for batch in plan["batches"]))

    def test_worker_packs_dedupe_context_values_and_parent_paths(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            {
                "section_id": "1.2",
                "title": "研究结论",
                "system_instruction": "sys",
                "chapter_context": {
                    "paths": {
                        "process": {"design": {"capacity": 10}, "retrofit": {"mode": "reuse"}},
                        "project.project_name": "同值",
                    },
                    "open_items": [],
                },
                "project_context": {"paths": {"project.project_name": "同值"}, "open_items": []},
                "research_evidence": [],
                "required_structure": [],
                "allowed_headings": ["1.2 研究结论"],
                "generation_rules": {},
                "quality_checks": [],
            },
            {
                "section_id": "3.1",
                "title": "生产规模",
                "system_instruction": "sys",
                "chapter_context": {
                    "paths": {
                        "process.design": {"capacity": 10},
                        "user.owner": "同值",
                    },
                    "open_items": [],
                },
                "project_context": {"paths": {"user.owner": "同值"}, "open_items": []},
                "research_evidence": [],
                "required_structure": [],
                "allowed_headings": ["3.1 生产规模"],
                "generation_rules": {},
                "quality_checks": [],
            },
        ]}
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "draft_fragments"
            info = write_worker_packs(jobs, fdir, worker_count=1)
            self.assertEqual(info["workers"], 1)
            pack = load_data(Path(info["contexts_dir"]) / "worker_01.json")
            self.assertEqual(Path(info["contexts_dir"]).name, "worker_contexts")
            self.assertFalse((fdir / "contexts" / "worker_01.json").exists())
            self.assertEqual(pack["pack_type"], "draft_worker")
            self.assertIn("common_output_contract", pack)
            self.assertNotIn("chapter_context", pack["sections"][0])
            context_items = pack["shared_context"]["items"]
            self.assertEqual(len([x for x in context_items if "process" in x["source_paths"]]), 1)
            self.assertTrue(any(set(x["source_paths"]) >= {"project.project_name", "user.owner"} for x in context_items))
            section_refs = {s["section_id"]: s["context_refs"] for s in pack["sections"]}
            self.assertTrue(any(ref.get("selector") == "design" for ref in section_refs["3.1"]))

    def test_worker_pack_keeps_context_when_job_contexts_are_aliased(self):
        ctx = {
            "paths": {
                "process": {"design": {"capacity": 10}, "retrofit": {"mode": "reuse"}},
                "process.design": {"capacity": 10},
            },
            "open_items": [],
        }
        job = {
            "section_id": "3.1",
            "title": "生产规模",
            "system_instruction": "sys",
            "chapter_context": ctx,
            "project_context": ctx,
            "research_evidence": [],
            "required_structure": [],
            "allowed_headings": ["3.1 生产规模"],
            "generation_rules": {},
            "quality_checks": [],
        }
        self.assertIs(job["chapter_context"], job["project_context"])
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "draft_fragments"
            info = write_worker_packs({"contract_version": "1.0", "project_id": "P-001", "jobs": [job]}, fdir)
            pack = load_data(Path(info["contexts_dir"]) / "worker_01.json")
            context_items = pack["shared_context"]["items"]
            self.assertTrue(context_items)

            by_id = {item["context_id"]: item for item in context_items}
            refs = pack["sections"][0]["context_refs"]
            design_ref = next(ref for ref in refs if ref["source_path"] == "process.design")
            item = by_id[design_ref["context_id"]]
            resolved = item["value"]
            for token in design_ref.get("selector", "").split("."):
                if token:
                    resolved = resolved[token]
            self.assertEqual(resolved, {"capacity": 10})

    def test_planning_stage_exit_writes_contexts_and_digest(self):
        # 证据校验（最小来源数等）已由 evidence 相关逻辑覆盖；本测试聚焦
        # needs_llm 退出时的上下文包与 digest 落盘，故 mock 掉证据校验。
        from unittest.mock import patch as mock_patch
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            dump_json(PROFILE, out / "profile.json")
            dump_json(FACTS, out / "facts.json")
            evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
            dump_json(evidence, out / "evidence.json")
            args = Namespace(
                profile=str(out / "profile.json"),
                facts=str(out / "facts.json"),
                skip_user_inputs=True,
                research_evidence=str(out / "evidence.json"),
                section_drafts=None,
                ai_mode="host_agent",
                run_mode="test",
            )
            with mock_patch("internal.planning.stage.validate_evidence", return_value=({"issues": []}, 0)):
                code, summary = run_chapter_planning_stage(args, out)
            self.assertEqual(code, EXIT_NEEDS_LLM)
            jobs_payload = load_data(out / "llm_jobs.json")
            self.assertTrue(jobs_payload["jobs"])
            cdir = Path(summary["worker_contexts_dir"])
            self.assertTrue((cdir / "worker_01.json").exists())
            self.assertEqual(cdir.name, "worker_contexts")
            self.assertEqual(summary["contexts_dir"], summary["worker_contexts_dir"])
            self.assertLessEqual(summary["worker_count"], 3)
            self.assertFalse((Path(summary["draft_fragments_dir"]) / "digest.md").exists())
            self.assertFalse((Path(summary["draft_fragments_dir"]) / "batch_plan.json").exists())
            # 强制工作流：唯一入口 + 并行派发 + next_action 指向它
            wf = Path(summary["host_workflow"])
            self.assertTrue(wf.exists())
            text = wf.read_text(encoding="utf-8")
            self.assertIn("不要读取 llm_jobs.json", text)
            self.assertIn("并行派发", text)
            self.assertIn("batch_worker_01.json", text)
            self.assertIn("--submit", text)
            self.assertIn("HOST_WORKFLOW.md", summary["next_action"])

    def _draft(self, sid, heading, paragraphs=("正文",)):
        return {
            "section_id": sid,
            "draft_status": "draft",
            "subsections": [{"heading": heading, "paragraphs": list(paragraphs)}],
            "source_evidence_ids": [],
            "claims": [],
            "open_items": [],
        }

    def test_validate_fragment_channel(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            {"section_id": "1.2", "allowed_headings": ["1.2 研究结论"], "research_task_ids": []}]}
        evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.json"; dump_json(self._draft("1.2", "1.2 研究结论"), good)
            bad = Path(td) / "bad.json"; dump_json(self._draft("1.2", "1.2 错误标题"), bad)
            r, c = validate_fragment(jobs, evidence, good)
            self.assertEqual(c, 0); self.assertEqual(r["sections"], ["1.2"])
            r, c = validate_fragment(jobs, evidence, bad)
            self.assertEqual(c, 2); self.assertTrue(r["issues"])

    def test_submit_channel_persists_and_collect_merges(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            {"section_id": "1.2", "allowed_headings": ["1.2 研究结论"], "research_task_ids": []}]}
        evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "draft_fragments"; fdir.mkdir()
            bad = Path(td) / "bad.json"; dump_json(self._draft("1.2", "1.2 错误标题"), bad)
            good = Path(td) / "good.json"; dump_json(self._draft("1.2", "1.2 研究结论", ["提交版正文"]), good)
            # 无效草稿：拒绝且不落盘
            r, c = submit(fdir, jobs, evidence, bad)
            self.assertEqual(c, 2)
            self.assertFalse((fdir / "validated").exists() or any((fdir / "validated").glob("*.json")))
            # 有效草稿：接受并持久化到 validated/
            r, c = submit(fdir, jobs, evidence, good)
            self.assertEqual(c, 0); self.assertEqual(r["submitted"], ["1.2"])
            self.assertTrue((fdir / "validated" / "1_2.json").exists())
            # 收集：validated/ 计入覆盖，无需原始分片，最终合并文档通过门禁
            result, code = collect(fdir, jobs, evidence)
            self.assertEqual(code, 0)
            self.assertEqual(result["covered"], ["1.2"])
            self.assertEqual(result["draft_count"], 1)
            self.assertEqual(result["document"]["drafts"][0]["subsections"][0]["paragraphs"], ["提交版正文"])

    def test_worker_batch_submit_persists_each_section_and_collect_merges(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            {"section_id": "1.2", "allowed_headings": ["1.2 研究结论"], "research_task_ids": []},
            {"section_id": "3.1", "allowed_headings": ["3.1 生产规模"], "research_task_ids": []},
        ]}
        evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "draft_fragments"; fdir.mkdir()
            worker_batch = Path(td) / "batch_worker_01.json"
            dump_json({"drafts": [
                self._draft("1.2", "1.2 研究结论", ["结论正文"]),
                self._draft("3.1", "3.1 生产规模", ["规模正文"]),
            ]}, worker_batch)

            result, code = submit(fdir, jobs, evidence, worker_batch)
            self.assertEqual(code, 0)
            self.assertEqual(result["submitted"], ["1.2", "3.1"])
            self.assertTrue((fdir / "validated" / "1_2.json").exists())
            self.assertTrue((fdir / "validated" / "3_1.json").exists())

            result, code = collect(fdir, jobs, evidence)
            self.assertEqual(code, 0)
            self.assertEqual(result["covered"], ["1.2", "3.1"])
            self.assertEqual(result["draft_count"], 2)

    def test_worker_batch_partial_submit_keeps_good_sections(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            {"section_id": "1.2", "allowed_headings": ["1.2 研究结论"], "research_task_ids": []},
            {"section_id": "3.1", "allowed_headings": ["3.1 生产规模"], "research_task_ids": []},
        ]}
        evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "draft_fragments"; fdir.mkdir()
            batch = Path(td) / "batch_worker_01.json"
            dump_json({"drafts": [
                self._draft("1.2", "1.2 研究结论", ["有效正文"]),
                self._draft("3.1", "3.1 错误标题", ["需修正文"]),
            ]}, batch)
            result, code = submit(fdir, jobs, evidence, batch)
            self.assertEqual(code, 2)
            self.assertEqual(result["accepted_sections"], ["1.2"])
            self.assertEqual(result["retry_sections"], ["3.1"])
            self.assertTrue((fdir / "validated" / "1_2.json").exists())
            self.assertFalse((fdir / "validated" / "3_1.json").exists())

    def test_collect_validated_shadows_raw_fragment(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [
            {"section_id": "1.2", "allowed_headings": ["1.2 研究结论"], "research_task_ids": []}]}
        evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "draft_fragments"; fdir.mkdir()
            dump_json([self._draft("1.2", "1.2 研究结论", ["原始分片正文"])], fdir / "batch_01.json")
            vdir = fdir / "validated"; vdir.mkdir()
            dump_json(self._draft("1.2", "1.2 研究结论", ["提交版正文"]), vdir / "1_2.json")
            result, code = collect(fdir, jobs, evidence)
            self.assertEqual(code, 0)
            self.assertEqual(result["shadowed"], [
                {"section_id": "1.2", "fragment": "batch_01.json", "replaced_by": "1_2.json"}])
            self.assertEqual(
                result["document"]["drafts"][0]["subsections"][0]["paragraphs"], ["提交版正文"])

    def test_draft_validation_rejects_unexpected_heading(self):
        jobs = {"contract_version": "1.0", "project_id": "P-001", "jobs": [{"section_id": "1.2", "allowed_headings": ["1.2 研究结论"], "research_task_ids": []}]}
        evidence = {"contract_version": "1.0", "project_id": "P-001", "items": []}
        drafts = {"contract_version": "1.0", "project_id": "P-001", "drafts": [{"section_id": "1.2", "draft_status": "draft", "subsections": [{"heading": "1.2 错误标题", "paragraphs": ["正文"]}], "source_evidence_ids": [], "claims": [], "open_items": []}]}
        result, code = validate_drafts(jobs, evidence, drafts, "production")
        self.assertEqual(code, 2)
        self.assertIn("unexpected subsection heading", result["issues"][0])

    def test_all_blocked_report_propagates_not_data_closed_status(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            profile = out / "profile.json"
            facts = out / "facts.json"
            plan = out / "chapter_plan.json"
            dump_json(PROFILE, profile)
            dump_json({"project": {"project_id": "P-001", "project_name": "示例改造项目"}, "adopted_scheme": {}}, facts)
            dump_json({"chapter_plan": []}, plan)
            dump_json({"blocked_sections": [{"section_id": "4.1.3", "rule_id": "chapter_4", "fact_id": "adopted_scheme", "reason": "missing"}]}, out / "gap_analysis.json")
            code, summary = run_report_generation_stage(Namespace(facts=str(facts), profile=str(profile), chapter_plan=str(plan), section_drafts=None, strict_consistency=False), out)
            self.assertEqual(code, 0)
            self.assertEqual(summary["completion_status"], "generated_with_blocked_sections")
            check = yaml.safe_load((out / "consistency_check.json").read_text(encoding="utf-8"))
            self.assertIn("CHAPTERS_BLOCKED_BY_REQUIRED_FACTS", {x["code"] for x in check["warnings"]})

    def test_research_packs_and_mandatory_workflow_are_self_contained(self):
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "research_fragments"
            info = write_research_packs(RESEARCH_TASKS, fdir)
            self.assertEqual(info["packs"], 2)
            cdir = Path(info["contexts_dir"])
            market = load_data(cdir / "research_worker_01.json")
            general = load_data(cdir / "research_worker_02.json")
            self.assertEqual(market["lane"], "market_long_pole")
            self.assertEqual(general["lane"], "general_research")
            all_tasks = [t for pack in (market, general) for t in pack["tasks"]]
            self.assertEqual({t["task_id"] for t in all_tasks}, {"R-A", "R-B"})
            self.assertTrue(all("search_budget" in t for t in all_tasks))
            self.assertFalse((fdir / "digest.md").exists())
            self.assertFalse((fdir / "worker_plan.json").exists())
            wf = write_research_host_workflow(RESEARCH_TASKS, fdir, Path(td), "SKILL_ROOT", info["manifest_data"])
            text = Path(wf).read_text(encoding="utf-8")
            self.assertIn("不要读取 research_tasks.json", text)
            self.assertIn("一次性并行派发", text)
            self.assertIn("search_budget", text)
            self.assertIn("--submit", text)

    def test_research_submit_validates_per_task_and_collect_merges(self):
        with tempfile.TemporaryDirectory() as td:
            fdir = Path(td) / "research_fragments"
            write_research_packs(RESEARCH_TASKS, fdir)
            # 不足 minimum_sources=2 → exit 2，不入库
            frag = Path(td) / "ev_A.json"
            dump_json({"task_id": "R-A", "items": [_ev("EV-1", "R-A", "https://a.example/1")]}, frag)
            result, code = submit_research(RESEARCH_TASKS, fdir, frag, "production")
            self.assertEqual(code, 2)
            self.assertFalse((fdir / "validated" / "task_R_A.json").exists())
            # 补足第二个来源 → exit 0 入库
            dump_json({"task_id": "R-A", "items": [
                _ev("EV-1", "R-A", "https://a.example/1"),
                _ev("EV-2", "R-A", "https://b.example/2"),
            ]}, frag)
            result, code = submit_research(RESEARCH_TASKS, fdir, frag, "production")
            self.assertEqual(code, 0)
            self.assertTrue((fdir / "validated" / "task_R_A.json").exists())
            # 只交了 R-A → 收集 exit 4，缺 R-B
            result, code = collect_research(RESEARCH_TASKS, fdir, Path(td) / "research_evidence.json", "production")
            self.assertEqual(code, 4)
            self.assertEqual(result["missing_tasks"], ["R-B"])
            # R-B 需官方来源，提交一条 official_government → 收集 exit 0
            frag_b = Path(td) / "ev_B.json"
            dump_json({"task_id": "R-B", "items": [_ev("EV-3", "R-B", "https://gov.example/3", "official_government")]}, frag_b)
            result, code = submit_research(RESEARCH_TASKS, fdir, frag_b, "production")
            self.assertEqual(code, 0)
            merged = Path(td) / "research_evidence.json"
            result, code = collect_research(RESEARCH_TASKS, fdir, merged, "production")
            self.assertEqual(code, 0)
            ev = load_data(merged)
            self.assertEqual(len(ev["items"]), 3)
            self.assertEqual(ev["project_id"], "P-001")

    def test_stage_reuses_valid_research_evidence_from_same_output_dir(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            profile = out / "profile.json"
            facts = out / "facts.json"
            dump_json(PROFILE, profile)
            dump_json(FACTS, facts)
            # Build the exact task set first, then create a fully valid test-mode
            # evidence file at the automatic cache path.
            tasks = build_tasks(PROFILE, PLAN, FACTS)
            items = []
            for task in tasks["tasks"]:
                minimum = int(task.get("minimum_sources") or 1)
                for i in range(minimum):
                    items.append(_ev(
                        f"EV-{task['task_id']}-{i+1}",
                        task["task_id"],
                        f"https://cache.example/{task['task_id']}/{i+1}",
                        "official_government",
                    ))
            dump_json({
                "contract_version": "1.0",
                "project_id": tasks["project_id"],
                "test_only": True,
                "items": items,
            }, out / "research_evidence.json")
            args = Namespace(
                profile=str(profile),
                facts=str(facts),
                skip_user_inputs=True,
                research_evidence=None,
                section_drafts=None,
                ai_mode="host_agent",
                run_mode="test",
            )
            code, summary = run_chapter_planning_stage(args, out)
            self.assertEqual(code, EXIT_NEEDS_LLM)
            self.assertTrue(summary["research_cache_hit"])
            self.assertEqual(summary["status"], "needs_llm")

    def test_planning_research_gate_writes_packs_and_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            profile = out / "profile.json"
            facts = out / "facts.json"
            dump_json(PROFILE, profile)
            dump_json(FACTS, facts)
            args = Namespace(
                profile=str(profile),
                facts=str(facts),
                skip_user_inputs=True,
                research_evidence=None,
                section_drafts=None,
                ai_mode="host_agent",
                run_mode="test",
            )
            code, summary = run_chapter_planning_stage(args, out)
            self.assertEqual(code, EXIT_NEEDS_RESEARCH)
            self.assertFalse((Path(summary["research_fragments_dir"]) / "digest.md").exists())
            self.assertFalse((Path(summary["research_fragments_dir"]) / "worker_plan.json").exists())
            self.assertLessEqual(summary["research_worker_count"], 2)
            wf = Path(summary["host_workflow"])
            self.assertTrue(wf.exists())
            self.assertIn("research_fragments", str(wf))
            self.assertIn("HOST_WORKFLOW.md", summary["next_action"])
            packs = list(Path(summary["research_contexts_dir"]).glob("research_worker_*.json"))
            self.assertTrue(packs)

    def test_v0149_slims_default_llm_surface_to_ten_or_less_jobs(self):
        tasks = build_tasks(PROFILE, PLAN, FACTS)
        jobs = build_jobs(PROFILE, PLAN, FACTS, tasks, {"items": []})
        sections = {x["section_id"] for x in jobs["jobs"]}
        self.assertLessEqual(len(sections), 10)
        self.assertFalse({"3.1", "5.1", "8.1", "10.2", "10.4", "19", "21"} & sections)

    def test_v0149_compact_llm_jobs_drops_context_payload(self):
        from internal.planning.llm_jobs import compact_jobs_for_validation
        tasks = build_tasks(PROFILE, PLAN, FACTS)
        jobs = build_jobs(PROFILE, PLAN, FACTS, tasks, {"items": []})
        compact = compact_jobs_for_validation(jobs)
        self.assertTrue(compact["jobs"])
        self.assertNotIn("chapter_context", compact["jobs"][0])
        self.assertNotIn("project_context", compact["jobs"][0])
        self.assertIn("allowed_headings", compact["jobs"][0])

    def test_draft_validation_accepts_no_claims_or_open_items(self):
        jobs = {"contract_version":"1.0","project_id":"P-001","jobs":[{"section_id":"1.2","allowed_headings":["1.2 研究结论"],"research_task_ids":[]}]}
        evidence = {"contract_version":"1.0","project_id":"P-001","items":[]}
        drafts = {"contract_version":"1.0","project_id":"P-001","drafts":[{
            "section_id":"1.2","draft_status":"draft",
            "subsections":[{"heading":"1.2 研究结论","paragraphs":["正文"]}],
            "source_evidence_ids":[]
        }]}
        result, code = validate_drafts(jobs, evidence, drafts, "production")
        self.assertEqual(code, 0)
        self.assertTrue(result["valid"])


    def test_success_cleanup_keeps_only_delivery_and_reuse_files(self):
        from run_skill import cleanup_runtime_artifacts
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)
            for name in [
                "confirmed_project_facts.json","research_evidence.json",
                "可行性研究报告_初稿.docx","可行性研究报告_初稿.md",
                "project_facts.json","llm_jobs.json","report_model.json","report_trace.json"
            ]:
                (out/name).write_text("x",encoding="utf-8")
            (out/"draft_fragments").mkdir(); (out/"draft_fragments"/"x.json").write_text("{}",encoding="utf-8")
            cleanup_runtime_artifacts(out,False)
            self.assertTrue((out/"confirmed_project_facts.json").exists())
            self.assertTrue((out/"research_evidence.json").exists())
            self.assertTrue((out/"可行性研究报告_初稿.docx").exists())
            self.assertFalse((out/"project_facts.json").exists())
            self.assertFalse((out/"llm_jobs.json").exists())
            self.assertFalse((out/"draft_fragments").exists())



if __name__ == "__main__":
    unittest.main()
