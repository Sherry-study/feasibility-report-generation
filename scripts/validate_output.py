#!/usr/bin/env python3
"""校验本地 Host 导出的报告交付物。

该脚本检查最终交付物和三 Tool 链路可复用 JSON 文件，并核验 manifest；
它不重新生成报告，也不接管 Skill 主流程。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

import jsonschema
import yaml


REQUIRED_DELIVERABLES = (
    "report_manifest.json",
    "可行性研究报告_初稿.docx",
    "可行性研究报告_初稿.md",
)
# 可选 JSON 是调试和追溯资产：存在则校验可解析，不存在不视为失败。
OPTIONAL_JSON = (
    "engineering_facts.json",
    "work_package.json",
    "work_results.json",
)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "report_manifest.schema.json"


def load_structured(path: Path):
    """按文件后缀读取 JSON/YAML，供输出目录检查复用。"""
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _validate_manifest(output_dir: Path, issues: list[str]) -> None:
    """Validate manifest schema, path association, and local artifact bytes."""
    manifest_path = output_dir / "report_manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = load_structured(manifest_path)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(manifest, schema)
    except Exception as exc:  # validation script must report, not crash
        issues.append(f"report_manifest.json: Schema 或解析失败: {exc}")
        return

    markdown_ref = PurePosixPath(manifest["markdown"]["path"].replace("\\", "/"))
    docx_ref = PurePosixPath(manifest["docx"]["path"].replace("\\", "/"))
    if markdown_ref.parent != docx_ref.parent:
        issues.append("report_manifest.json: Markdown 与 DOCX 不属于同一逻辑路径前缀")

    expected_names = {
        "markdown": "可行性研究报告_初稿.md",
        "docx": "可行性研究报告_初稿.docx",
    }
    for field, expected_name in expected_names.items():
        ref = PurePosixPath(manifest[field]["path"].replace("\\", "/"))
        if ref.name != expected_name:
            issues.append(f"report_manifest.json: {field}.path 未关联当前交付物")
            continue
        local_path = output_dir / expected_name
        if not local_path.is_file():
            continue
        payload = local_path.read_bytes()
        if manifest[field]["size_bytes"] != len(payload):
            issues.append(f"report_manifest.json: {field}.size_bytes 不匹配")
        if manifest[field]["sha256"] != hashlib.sha256(payload).hexdigest():
            issues.append(f"report_manifest.json: {field}.sha256 不匹配")


def validate_output_dir(output_dir: Path) -> tuple[dict, int]:
    """检查交付物存在性、可解析性和 manifest 完整性。"""
    issues = []
    present = []
    for name in REQUIRED_DELIVERABLES:
        path = output_dir / name
        if not path.is_file():
            issues.append(f"缺少必需交付物: {name}")
        else:
            present.append(name)
    for name in OPTIONAL_JSON:
        path = output_dir / name
        if path.exists():
            try:
                load_structured(path)
            except Exception as exc:
                issues.append(f"{name}: 解析失败: {exc}")
            else:
                present.append(name)
    _validate_manifest(output_dir, issues)
    return {"valid": not issues, "output_dir": str(output_dir), "present": sorted(set(present)), "issues": issues}, 0 if not issues else 1


def main(argv: list[str] | None = None) -> int:
    """命令行入口；返回码 0 表示输出目录满足基本交付要求。"""
    parser = argparse.ArgumentParser(description="校验可研报告输出目录中的关键交付物。")
    parser.add_argument("output_dir", help="本地 Host 中某次 report_finalize 的逻辑路径目录")
    args = parser.parse_args(argv)
    result, code = validate_output_dir(Path(args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
