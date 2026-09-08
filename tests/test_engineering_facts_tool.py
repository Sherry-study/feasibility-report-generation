from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.engineering_facts import execute


TOOL_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = TOOL_ROOT / "tests" / "fixtures"
SOURCE_DATA = FIXTURES_DIR / "engineering_source_bundle"

EXPECTED_TOP_LEVEL = {
    "meta",
    "sources",
    "basic_info",
    "unit",
    "component_catalog",
    "process",
    "diagnosis",
    "scheme_analysis",
    "adopted_scheme",
    "equipment",
    "derived_facts",
}

FORBIDDEN_KEYS = {
    "adapter_mode",
    "consistency_issues",
    "energy",
    "enterprise",
    "fa",
    "final_selection",
    "gaps",
    "profile_provenance",
    "project",
    "project_id",
    "project_level",
    "project_name",
    "project_name_source",
    "project_type",
    "raw_markdown",
    "report_category",
    "report_rows",
    "scheme_state",
    "score",
    "section_coverage",
    "user",
}


def all_keys(value: object) -> set[str]:
    """递归收集 JSON 对象中的全部键名。"""
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        for item in value.values():
            keys.update(all_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(all_keys(item))
        return keys
    return set()


class EngineeringFactsToolTests(unittest.TestCase):
    """基于真实上游产物验证新的工程事实契约。"""

    def setUp(self) -> None:
        if not SOURCE_DATA.is_dir():
            self.skipTest("工程事实测试 fixture 不存在")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.bundle = self.root / "source_bundle"
        shutil.copytree(SOURCE_DATA, self.bundle)
        scheme_path = next(self.bundle.rglob("scheme.json"))
        self.scheme_path = scheme_path
        self.artifact_dir = self.root / "artifacts"

    def execute(self, construction_unit: str = "测试建设单位") -> dict:
        """以统一业务输入执行 Tool。"""
        return execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(self.bundle),
                },
                "construction_unit": construction_unit,
            },
            self.artifact_dir,
        )

    def read_facts(self) -> dict:
        return json.loads(
            (self.artifact_dir / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )

    def write_scheme(self, payload: dict) -> None:
        self.scheme_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_real_sources_generate_strict_contract(self) -> None:
        result = self.execute()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["artifact"]["uri"].startswith("file:"))
        self.assertEqual(result["artifact"]["schema_version"], "2.0")

        facts = self.read_facts()
        self.assertEqual(set(facts), EXPECTED_TOP_LEVEL)
        self.assertEqual(set(facts["meta"]), {"schema_version", "generated_at"})
        self.assertEqual(facts["basic_info"], {"construction_unit": "测试建设单位"})
        self.assertEqual(facts["unit"]["name"], "1万吨/年异戊烯联合生产装置")
        self.assertFalse(FORBIDDEN_KEYS.intersection(all_keys(facts)))

        for source in facts["sources"]:
            self.assertTrue(
                set(source).issubset(
                    {"source_id", "source_type", "location", "schema_version"}
                )
            )

    def test_latest_scheme_drives_adopted_scheme(self) -> None:
        result = self.execute()
        facts = self.read_facts()

        self.assertEqual(result["summary"]["selected_names"], ["方案B"])
        self.assertEqual(facts["adopted_scheme"]["selected_names"], ["方案B"])
        self.assertTrue(facts["adopted_scheme"]["optimized"])
        self.assertEqual(
            facts["adopted_scheme"]["schemes"][0]["source_pool"],
            "candidates",
        )
        self.assertEqual(
            facts["adopted_scheme"]["schemes"][0]["name"],
            "方案B",
        )
        self.assertNotEqual(
            facts["adopted_scheme"]["schemes"][0]["name"],
            "方案C（新增设备：前置预反应强化系统）",
        )
        self.assertEqual(len(facts["scheme_analysis"]["user_candidates"]), 1)
        keys = all_keys(facts)
        self.assertNotIn("normalized_brief", keys)
        self.assertNotIn("normalized_description", keys)

    def test_scheme_evaluation_keeps_dynamic_dimensions(self) -> None:
        scheme = json.loads(self.scheme_path.read_text(encoding="utf-8"))
        scheme["candidates"][0]["evaluation"]["technicalFeasibility"]["label"] = (
            "算法自定义技术成熟度"
        )
        scheme["candidates"][0]["evaluation"]["energyEfficiency"] = {
            "level": "高",
            "comment": "节能潜力需结合热量衡算复核",
            "displayName": "能效影响",
            "keyBasis": ["热负荷变化"],
            "keyMetrics": {"steam": "待核实"},
            "pendingItems": ["补充能耗数据"],
        }
        scheme["user_candidates"][0]["evaluation"]["维护便利性"] = {
            "value": "中",
            "comment": "检修边界待确认",
        }
        self.write_scheme(scheme)

        result = self.execute()
        facts = self.read_facts()

        self.assertEqual(result["status"], "completed")
        candidate_eval = facts["scheme_analysis"]["candidates"][0]["evaluation"]
        self.assertIn("technical_feasibility", candidate_eval)
        self.assertEqual(
            candidate_eval["technical_feasibility"]["label"],
            "算法自定义技术成熟度",
        )
        self.assertIn("energy_efficiency", candidate_eval)
        self.assertEqual(candidate_eval["energy_efficiency"]["display_name"], "能效影响")
        self.assertEqual(candidate_eval["energy_efficiency"]["key_basis"], ["热负荷变化"])
        self.assertEqual(candidate_eval["energy_efficiency"]["key_metrics"], {"steam": "待核实"})
        self.assertEqual(candidate_eval["energy_efficiency"]["pending_items"], ["补充能耗数据"])
        self.assertEqual(candidate_eval["energy_efficiency"]["comment"], "节能潜力需结合热量衡算复核")
        user_eval = facts["scheme_analysis"]["user_candidates"][0]["evaluation"]
        self.assertEqual(user_eval["维护便利性"]["value"], "中")
        self.assertEqual(user_eval["维护便利性"]["comment"], "检修边界待确认")

    def test_scheme_evaluation_key_collision_is_fatal(self) -> None:
        scheme = json.loads(self.scheme_path.read_text(encoding="utf-8"))
        scheme["candidates"][0]["evaluation"]["energyEfficiency"] = {"level": "高"}
        scheme["candidates"][0]["evaluation"]["energy_efficiency"] = {"level": "低"}
        self.write_scheme(scheme)

        result = self.execute()

        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "SCHEME_EVALUATION_KEY_COLLISION",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_scheme_evaluation_invalid_field_keys_are_fatal(self) -> None:
        base_scheme = json.loads(self.scheme_path.read_text(encoding="utf-8"))
        cases = {
            "normalized_collision": {"keyBasis": ["A"], "key_basis": ["B"]},
            "symbol_only": {"***": "invalid"},
        }
        for case_name, evaluation in cases.items():
            with self.subTest(case=case_name):
                scheme = copy.deepcopy(base_scheme)
                scheme["candidates"][0]["evaluation"]["energyEfficiency"] = evaluation
                self.write_scheme(scheme)
                self.artifact_dir = self.root / f"artifacts_{case_name}"

                result = self.execute()

                self.assertEqual(result["status"], "failed")
                self.assertIn(
                    "SCHEME_EVALUATION_KEY_COLLISION",
                    {item["code"] for item in result["diagnostics"]},
                )

    def test_derived_values_are_reproducible(self) -> None:
        self.execute()
        facts = self.read_facts()
        hours = facts["unit"]["annual_operating_hours"]
        rows = facts["derived_facts"]["annualized_stream_quantities"]
        product = next(
            row
            for row in rows
            if row["condition"] == "design" and row["stream_id"] == "stream_27"
        )
        expected = round(
            product["input"]["flow_value"] * hours / 1000,
            6,
        )
        self.assertEqual(product["annual_quantity"]["value"], expected)

        materials = facts["derived_facts"]["annualized_material_consumption"]
        feed = next(
            row
            for row in materials
            if row["condition"] == "retrofit" and row["stream_id"] == "stream_2"
        )
        expected_feed = round(feed["input"]["flow_value"] * hours / 1000, 6)
        self.assertEqual(feed["annual_consumption"]["value"], expected_feed)
        self.assertNotIn("status", feed)

    def test_missing_source_still_generates_partial_facts(self) -> None:
        next(self.bundle.rglob("plant_info.json")).unlink()
        result = self.execute()
        self.assertEqual(result["status"], "completed")
        self.assertTrue((self.artifact_dir / "engineering_facts.json").is_file())
        facts = self.read_facts()
        self.assertNotIn(
            "plant_info",
            {item["source_type"] for item in facts["sources"]},
        )
        self.assertEqual(facts["unit"], {})

    def test_single_recognized_source_generates_partial_facts(self) -> None:
        source_dir = self.root / "single_source"
        source_dir.mkdir()
        (source_dir / "equipment.json").write_text(
            json.dumps(
                {"equipment": [{"id": "E-1", "name": "测试设备"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifact_dir = self.root / "single_source_artifacts"

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            artifact_dir,
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (artifact_dir / "engineering_facts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(facts), EXPECTED_TOP_LEVEL)
        self.assertEqual(
            facts["equipment"]["object_catalog"],
            [{"id": "E-1", "name": "测试设备"}],
        )
        self.assertEqual(result["summary"]["source_count"], 1)

    def test_no_recognized_source_fails_without_artifact(self) -> None:
        source_dir = self.root / "empty_source"
        source_dir.mkdir()
        artifact_dir = self.root / "empty_source_artifacts"

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            artifact_dir,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["artifact"])
        self.assertFalse((artifact_dir / "engineering_facts.json").exists())
        self.assertIn(
            "NO_RECOGNIZED_SOURCES",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_missing_related_professional_results_only_warns(self) -> None:
        next(self.bundle.rglob("plant_reactor_result*.json")).unlink()
        next(self.bundle.rglob("example_result_all*.json")).unlink()

        result = self.execute()

        self.assertEqual(result["status"], "completed")
        diagnostics = {item["code"]: item["level"] for item in result["diagnostics"]}
        self.assertEqual(diagnostics["REACTOR_RESULT_MISSING"], "warning")
        self.assertEqual(diagnostics["TOWER_RESULT_MISSING"], "warning")

    def test_missing_user_selection_keeps_scheme_analysis(self) -> None:
        scheme = json.loads(self.scheme_path.read_text(encoding="utf-8"))
        scheme.pop("user_selected", None)
        self.write_scheme(scheme)

        result = self.execute()

        self.assertEqual(result["status"], "completed")
        self.assertIn(
            "USER_SELECTED_MISSING",
            {item["code"] for item in result["diagnostics"]},
        )
        facts = self.read_facts()
        self.assertTrue(facts["scheme_analysis"]["candidates"])
        self.assertEqual(facts["adopted_scheme"], {})

    def test_duplicate_source_role_is_fatal(self) -> None:
        shutil.copy2(self.scheme_path, self.bundle / "duplicate_scheme.json")
        result = self.execute()
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "AMBIGUOUS_SOURCE_ROLE",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_empty_construction_unit_is_allowed(self) -> None:
        result = self.execute("   ")
        self.assertEqual(result["status"], "completed")
        facts = self.read_facts()
        self.assertEqual(facts["basic_info"], {"construction_unit": ""})

    def test_missing_construction_unit_is_allowed(self) -> None:
        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(self.bundle),
                }
            },
            self.artifact_dir,
        )
        self.assertEqual(result["status"], "completed")
        facts = self.read_facts()
        self.assertEqual(facts["basic_info"], {"construction_unit": ""})

    def test_non_string_construction_unit_is_fatal(self) -> None:
        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(self.bundle),
                },
                "construction_unit": 123,
            },
            self.artifact_dir,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "INVALID_CONSTRUCTION_UNIT",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_default_artifact_dir_is_used_when_omitted(self) -> None:
        default_artifact_dir = self.root / "default_artifacts"
        default_artifact = default_artifact_dir / "engineering_facts.json"

        with patch(
            "src.engineering_facts.core.DEFAULT_ARTIFACT_DIR",
            default_artifact_dir,
        ):
            result = execute(
                {
                    "source_location": {
                        "provider": "local_directory",
                        "location": str(self.bundle),
                    }
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(default_artifact.is_file())
        self.assertEqual(result["artifact"]["uri"], default_artifact.resolve().as_uri())

    def test_business_input_rejects_undeclared_fields(self) -> None:
        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(self.bundle),
                    "manifest": [],
                },
                "construction_unit": "测试建设单位",
                "project_name": "不应进入本阶段",
            },
            self.artifact_dir,
        )
        self.assertEqual(result["status"], "failed")
        codes = {item["code"] for item in result["diagnostics"]}
        self.assertIn("UNSUPPORTED_REQUEST_FIELD", codes)
        self.assertIn("UNSUPPORTED_SOURCE_LOCATION_FIELD", codes)

    def test_unknown_selected_scheme_is_fatal(self) -> None:
        scheme = json.loads(self.scheme_path.read_text(encoding="utf-8"))
        scheme["user_selected"] = ["不存在的方案"]
        self.write_scheme(scheme)
        result = self.execute()
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "USER_SELECTED_NOT_FOUND",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_duplicate_selected_name_between_pools_is_fatal(self) -> None:
        scheme = json.loads(self.scheme_path.read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(scheme["candidates"][1])
        duplicate["sourceType"] = "custom"
        scheme.setdefault("user_candidates", []).append(duplicate)
        scheme["user_selected"] = ["方案B"]
        self.write_scheme(scheme)
        result = self.execute()
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "USER_SELECTED_AMBIGUOUS",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_utility_summary_and_standard_coal_conversion_are_deterministic(self) -> None:
        result = self.execute()
        self.assertEqual(result["status"], "completed")
        facts = self.read_facts()
        summary = facts["derived_facts"]["utility_consumption_summary"]
        self.assertEqual(summary["status"], "identified")
        self.assertTrue(
            {
                item["medium_code"]
                for item in summary["items"]
            }.issuperset({"heat_oil", "cooling_water"})
        )
        heat_oil = next(item for item in summary["items"] if item["medium_code"] == "heat_oil")
        expected = round(
            heat_oil["input"]["mass_flow_kg_h"]
            * facts["unit"]["annual_operating_hours"]
            / 1000,
            6,
        )
        self.assertEqual(heat_oil["annual_quantity"]["value"], expected)

        conversion = facts["derived_facts"]["energy_conversion"]
        self.assertEqual(conversion["conversion_type"], "standard_coal")
        self.assertEqual(conversion["status"], "blocked")
        self.assertIn(
            "conversion_factor_missing",
            {item["reason"] for item in conversion["items"]},
        )

    def test_dynamic_utility_media_generate_different_conversion_results(self) -> None:
        source_dir = self.root / "utility_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "用电反应器",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {"medium": "electricity", "power_kw": 10}
                                }
                            },
                        },
                        {
                            "id": "R-2",
                            "name": "用汽反应器",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {"medium": "蒸汽", "mass_flow_kg_h": 1000}
                                }
                            },
                        },
                    ],
                    "combined_schemes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "utility_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "utility_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        conversion = facts["derived_facts"]["energy_conversion"]
        by_medium = {item["medium_code"]: item for item in conversion["items"]}
        self.assertEqual(by_medium["electricity"]["standard_coal_tce"], 9.832)
        self.assertEqual(by_medium["steam"]["standard_coal_tce"], 1028.8)
        self.assertEqual(conversion["total_standard_coal_tce"], 1038.632)

    def test_negative_standard_oil_note_does_not_change_default_coal(self) -> None:
        source_dir = self.root / "negative_standard_oil_note"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {
                    "plant_info": {
                        "annual_operating_hours": 8000,
                        "note": "本项目不采用标准油。",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "用电反应器",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {"medium": "electricity", "power_kw": 10}
                                }
                            },
                        }
                    ],
                    "combined_schemes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "negative_note_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "negative_note_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            facts["derived_facts"]["energy_conversion"]["conversion_type"],
            "standard_coal",
        )

    def test_missing_energy_rules_is_fatal_not_missing_factor(self) -> None:
        source_dir = self.root / "missing_rules_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "用电反应器",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {"medium": "electricity", "power_kw": 10}
                                }
                            },
                        }
                    ],
                    "combined_schemes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch(
            "src.engineering_facts.core.ENERGY_RULES_PATH",
            self.root / "missing_energy_rules.json",
        ):
            result = execute(
                {
                    "source_location": {
                        "provider": "local_directory",
                        "location": str(source_dir),
                    }
                },
                self.root / "missing_rules_artifacts",
            )

        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "ENGINEERING_FACTS_TOOL_FAILED",
            {item["code"] for item in result["diagnostics"]},
        )
        self.assertNotIn(
            "conversion_factor_missing",
            json.dumps(result, ensure_ascii=False),
        )

    def test_selected_reactor_scheme_only_counts_adopted_candidate(self) -> None:
        source_dir = self.root / "selected_candidate_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "selected_combined_scheme": {
                        "scheme_index": 2,
                        "addition": [{"id": "R-1", "scheme_type": "selected"}],
                    },
                    "combined_schemes": [
                        {
                            "scheme_index": 1,
                            "addition": [{"id": "R-1", "scheme_type": "candidate"}],
                        },
                        {
                            "scheme_index": 2,
                            "addition": [{"id": "R-1", "scheme_type": "selected"}],
                        },
                    ],
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "选定反应器",
                            "retrofit": {
                                "schemes": [
                                    {
                                        "scheme_type": "candidate",
                                        "operating_conditions": {
                                            "utility": {
                                                "medium": "electricity",
                                                "power_kw": 10,
                                            }
                                        },
                                    },
                                    {
                                        "scheme_type": "selected",
                                        "operating_conditions": {
                                            "utility": {
                                                "medium": "electricity",
                                                "power_kw": 20,
                                            }
                                        },
                                    },
                                ]
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "selected_candidate_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "selected_candidate_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        utility_items = facts["derived_facts"]["utility_consumption_summary"]["items"]
        self.assertEqual(len(utility_items), 1)
        self.assertEqual(utility_items[0]["annual_quantity"]["value"], 160000.0)
        conversion = facts["derived_facts"]["energy_conversion"]
        self.assertEqual(conversion["total_standard_coal_tce"], 19.664)

    def test_utility_medium_does_not_use_process_mass_flow_as_utility_quantity(self) -> None:
        source_dir = self.root / "utility_medium_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "入口流量设备",
                            "utility_medium": "HO",
                            "mass_flow_kg_h": 9999,
                        }
                    ],
                    "combined_schemes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "utility_medium_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "utility_medium_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        item = facts["derived_facts"]["utility_consumption_summary"]["items"][0]
        self.assertEqual(item["medium_code"], "heat_oil")
        self.assertEqual(item["status"], "clue_only")
        self.assertNotIn("annual_quantity", item)
        self.assertNotIn("total_standard_coal_tce", facts["derived_facts"]["energy_conversion"])

    def test_object_catalog_utility_keeps_retrofit_equipment_source_ref(self) -> None:
        source_dir = self.root / "object_catalog_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "equipment.json").write_text(
            json.dumps(
                {
                    "equipment": [
                        {
                            "id": "E-1",
                            "name": "目录用电设备",
                            "utility": {"medium": "electricity", "power_kw": 2},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "object_catalog_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "object_catalog_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        refs = facts["derived_facts"]["utility_consumption_summary"]["items"][0]["source_refs"]
        self.assertEqual(refs[0]["source_id"], "retrofit_equipment")

    def test_utility_with_duty_generates_single_item_and_deduplicates_paths(self) -> None:
        source_dir = self.root / "utility_duty_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "重复路径反应器",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {
                                        "medium": "蒸汽",
                                        "mass_flow_kg_h": 1000,
                                        "duty": 1200,
                                    }
                                }
                            },
                            "utility": {
                                "medium": "蒸汽",
                                "mass_flow_kg_h": 1000,
                                "duty": 1200,
                            },
                        },
                        {
                            "id": "R-2",
                            "name": "另一台反应器",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {
                                        "medium": "蒸汽",
                                        "mass_flow_kg_h": 1000,
                                        "duty": 1200,
                                    }
                                }
                            },
                        },
                    ],
                    "combined_schemes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "utility_duty_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "utility_duty_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        items = facts["derived_facts"]["utility_consumption_summary"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(
            {item["equipment_id"] for item in items},
            {"R-1", "R-2"},
        )
        self.assertFalse(
            [item for item in items if item.get("medium_code") == "duty"]
        )

    def test_same_equipment_different_utility_subpaths_are_not_dedupliced(self) -> None:
        source_dir = self.root / "subpath_utility_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "equipment.json").write_text(
            json.dumps(
                {
                    "equipment": [
                        {
                            "id": "E-1",
                            "name": "成套设备",
                            "agitator": {
                                "utility": {"medium": "electricity", "power_kw": 10}
                            },
                            "pump": {
                                "utility": {"medium": "electricity", "power_kw": 10}
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "subpath_utility_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "subpath_utility_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        items = facts["derived_facts"]["utility_consumption_summary"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(
            {item["utility_identity"] for item in items},
            {
                "object_catalog[].agitator.utility",
                "object_catalog[].pump.utility",
            },
        )
        self.assertEqual(
            facts["derived_facts"]["energy_conversion"]["total_standard_coal_tce"],
            19.664,
        )

    def test_energy_conversion_totals_are_separated_by_condition(self) -> None:
        source_dir = self.root / "condition_total_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {"plant_info": {"annual_operating_hours": 8000}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "equipment.json").write_text(
            json.dumps(
                {
                    "equipment": [
                        {
                            "id": "E-1",
                            "name": "双工况用电设备",
                            "design": {
                                "utility": {"medium": "electricity", "power_kw": 10}
                            },
                            "retrofit": {
                                "utility": {"medium": "electricity", "power_kw": 20}
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "condition_total_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "condition_total_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        conversion = facts["derived_facts"]["energy_conversion"]
        totals = {
            item["condition"]: item["total_standard_coal_tce"]
            for item in conversion["totals_by_condition"]
        }
        self.assertEqual(totals, {"design": 9.832, "retrofit": 19.664})
        self.assertNotIn("total_standard_coal_tce", conversion)
        self.assertNotIn("29.496", json.dumps(conversion, ensure_ascii=False))

    def test_duty_only_utility_keeps_clue_without_conversion(self) -> None:
        source_dir = self.root / "duty_only_source"
        source_dir.mkdir()
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "负荷线索设备",
                            "selected_parameters": {
                                "operating_conditions": {"heat_duty_kw": 1200}
                            },
                        }
                    ],
                    "combined_schemes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "duty_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "duty_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        item = facts["derived_facts"]["utility_consumption_summary"]["items"][0]
        self.assertEqual(item["status"], "clue_only")
        self.assertEqual(item["reason"], "physical_quantity_missing")
        conversion_item = facts["derived_facts"]["energy_conversion"]["items"][0]
        self.assertEqual(conversion_item["status"], "blocked")
        self.assertEqual(conversion_item["reason"], "physical_quantity_missing")

    def test_standard_oil_steam_requires_pressure_and_calculates_when_available(self) -> None:
        source_dir = self.root / "standard_oil_source"
        source_dir.mkdir()
        (source_dir / "plant_info.json").write_text(
            json.dumps(
                {
                    "plant_info": {
                        "annual_operating_hours": 8000,
                        "energy_accounting_standard": "standard_oil",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (source_dir / "reactor_result.json").write_text(
            json.dumps(
                {
                    "reactors": [
                        {
                            "id": "R-1",
                            "name": "有压蒸汽设备",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {
                                        "medium": "蒸汽",
                                        "mass_flow_kg_h": 1000,
                                        "pressure_mpa": 1.0,
                                    }
                                }
                            },
                        },
                        {
                            "id": "R-3",
                            "name": "等级蒸汽设备",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {
                                        "medium": "蒸汽",
                                        "mass_flow_kg_h": 1000,
                                        "pressure_grade": "3.5MPa级",
                                    }
                                }
                            },
                        },
                        {
                            "id": "R-2",
                            "name": "缺压蒸汽设备",
                            "selected_parameters": {
                                "operating_conditions": {
                                    "utility": {
                                        "medium": "蒸汽",
                                        "mass_flow_kg_h": 1000,
                                    }
                                }
                            },
                        },
                    ],
                    "combined_schemes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = execute(
            {
                "source_location": {
                    "provider": "local_directory",
                    "location": str(source_dir),
                }
            },
            self.root / "standard_oil_artifacts",
        )

        self.assertEqual(result["status"], "completed")
        facts = json.loads(
            (self.root / "standard_oil_artifacts" / "engineering_facts.json").read_text(
                encoding="utf-8"
            )
        )
        conversion = facts["derived_facts"]["energy_conversion"]
        self.assertEqual(conversion["conversion_type"], "standard_oil")
        calculated = next(
            item for item in conversion["items"] if item.get("status") == "calculated"
        )
        grade_matched = next(
            item
            for item in conversion["items"]
            if item.get("equipment_name") == "等级蒸汽设备"
        )
        blocked = next(item for item in conversion["items"] if item.get("status") == "blocked")
        self.assertEqual(calculated["standard_oil_toe"], 544.0)
        self.assertEqual(calculated["rule_id"], "SO-STEAM-007")
        self.assertEqual(grade_matched["rule_id"], "SO-STEAM-005")
        self.assertEqual(blocked["reason"], "steam_pressure_or_grade_missing")


if __name__ == "__main__":
    unittest.main()
