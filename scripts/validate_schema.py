#!/usr/bin/env python3
"""校验仓库内 Schema 文件，或按指定 JSON Schema 校验一个实例文件。

本脚本只做确定性检查，不参与可研三 Tool 业务编排。默认模式用于快速发现
`schemas/` 下 JSON Schema 是否可解析、是否至少包含 `$schema` 或 `type`
这类基本标记；当同时传入 `--schema` 与 `--instance` 时，才进入实例校验。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_structured(path: Path):
    """按文件后缀读取 JSON/YAML，统一处理 UTF-8 BOM。"""
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    if suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def validate_schema_files(schema_dir: Path) -> list[str]:
    """校验目录中的 Schema 文件是否可解析、形态是否像 JSON Schema。"""
    issues = []
    for path in sorted(schema_dir.glob("*.json")):
        try:
            data = load_structured(path)
        except Exception as exc:
            issues.append(f"{path}: 解析失败: {exc}")
            continue
        if not isinstance(data, dict):
            issues.append(f"{path}: schema 根节点必须是 object")
        if "$schema" not in data and "type" not in data:
            issues.append(f"{path}: 缺少 $schema/type 标记")
    return issues


def validate_instance(schema_path: Path, instance_path: Path) -> list[str]:
    """使用 jsonschema 对一个实例文件执行 Draft 2020-12 校验。"""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return ["未安装 jsonschema，无法执行实例校验"]
    schema = load_structured(schema_path)
    instance = load_structured(instance_path)
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{instance_path}: {error.message}" for error in validator.iter_errors(instance)]


def main(argv: list[str] | None = None) -> int:
    """命令行入口；返回码 0 表示通过，1 表示存在校验问题。"""
    parser = argparse.ArgumentParser(description="校验内置 schemas，或按指定 schema 校验一个实例文件。")
    parser.add_argument("--schema-dir", default=str(ROOT / "schemas"), help="待扫描的 schema 目录，默认使用仓库 schemas/")
    parser.add_argument("--schema", help="用于实例校验的 JSON Schema 文件")
    parser.add_argument("--instance", help="待校验的 JSON/YAML 实例文件")
    args = parser.parse_args(argv)

    issues = []
    if args.schema or args.instance:
        if not (args.schema and args.instance):
            parser.error("--schema 与 --instance 必须同时提供")
        issues.extend(validate_instance(Path(args.schema), Path(args.instance)))
    else:
        issues.extend(validate_schema_files(Path(args.schema_dir)))

    if issues:
        print(json.dumps({"valid": False, "issues": issues}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"valid": True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
