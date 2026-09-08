#!/usr/bin/env python3
"""校验 report_generation 输出目录中的关键交付物。

该脚本只检查最终交付物和两 Tool 链路可复用 JSON 文件是否存在、是否可解析；
它不重新生成报告，也不接管 Skill 主流程。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


REQUIRED_DELIVERABLES = (
    "可行性研究报告_初稿.docx",
    "可行性研究报告_初稿.md",
)
# 可选 JSON 是调试和追溯资产：存在则校验可解析，不存在不视为失败。
OPTIONAL_JSON = (
    "engineering_facts.json",
    "work_package.json",
    "work_results.json",
)


def load_structured(path: Path):
    """按文件后缀读取 JSON/YAML，供输出目录检查复用。"""
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_output_dir(output_dir: Path) -> tuple[dict, int]:
    """检查交付物存在性和 JSON/YAML 可解析性。"""
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
    return {"valid": not issues, "output_dir": str(output_dir), "present": sorted(set(present)), "issues": issues}, 0 if not issues else 1


def main(argv: list[str] | None = None) -> int:
    """命令行入口；返回码 0 表示输出目录满足基本交付要求。"""
    parser = argparse.ArgumentParser(description="校验可研报告输出目录中的关键交付物。")
    parser.add_argument("output_dir", help="report_generation 返回的输出目录")
    args = parser.parse_args(argv)
    result, code = validate_output_dir(Path(args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
