#!/usr/bin/env python3
"""将可研报告交付物与可复用证据文件打包为 zip。

打包范围故意保持克制：只放用户交付和后续复用最常用的文件，不把
`llm_jobs.json`、`report_model.json` 等运行时中间件塞进正式交付包。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


DEFAULT_INCLUDE = (
    "可行性研究报告_初稿.docx",
    "可行性研究报告_初稿.md",
    "confirmed_project_facts.json",
    "research_evidence.json",
    "run_summary.json",
)


def package_result(output_dir: Path, package_path: Path, names: tuple[str, ...] = DEFAULT_INCLUDE) -> dict:
    """按白名单打包文件；缺失项记录到 missing，但不阻断已有文件打包。"""
    included = []
    missing = []
    package_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(package_path, "w", ZIP_DEFLATED) as zf:
        for name in names:
            path = output_dir / name
            if path.is_file():
                zf.write(path, arcname=name)
                included.append(name)
            else:
                missing.append(name)
    return {"package": str(package_path), "included": included, "missing": missing}


def main(argv: list[str] | None = None) -> int:
    """命令行入口；至少打包到一个文件时返回 0。"""
    parser = argparse.ArgumentParser(description="打包可研报告交付物。")
    parser.add_argument("output_dir", help="report_generation 返回的输出目录")
    parser.add_argument("--output", required=True, help="目标 zip 路径")
    args = parser.parse_args(argv)
    result = package_result(Path(args.output_dir), Path(args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["included"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
