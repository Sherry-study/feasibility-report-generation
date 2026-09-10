"""工程事实整理核心算法。

本模块只做确定性整理：

* 自动发现并识别上游算法固定结构文件；
* 按来源优先级映射工程事实；
* 解析用户在 scheme.json 中已选择的最终采用方案；
* 计算可复算的年化流股量与原辅料消耗。

本模块不调用 LLM，不生成项目名称，不做人审确认，不把 diagnostics 写入
工程事实产物。
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from duck.content import Content
from duck.host_client import HostClient
from src.artifact_paths import new_logical_prefix
from src.errors import HostStorageError


# 输出契约固定常量
SCHEMA_VERSION = "2.0"  # 工程事实 JSON 的 schema 版本
MEDIA_TYPE = "application/json"
ENERGY_RULES_PATH = (
    Path(__file__).resolve().parent
    / "rules"
    / "energy_conversion_rules.json"
)

# 工程事实 JSON 允许保留的一级键，清洗时只保留这些顶层字段
TOP_LEVEL_KEYS = [
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
]

ENGINEERING_INPUT_FIELDS = {
    "provider",
    "root",
    "file_overrides",
    "construction_unit",
}
LEGACY_REQUEST_FIELDS = {"source_location", "construction_unit"}
SOURCE_LOCATION_FIELDS = {"provider", "location", "file_overrides"}
SOURCE_PROVIDERS = {"local_directory", "host_directory", "host_file"}

# HostClient 目前只支持按逻辑路径读文件，不支持枚举目录。host_directory
# 因此按这份清单尝试读取标准文件名；file_overrides 可覆盖单个文件名。
HOST_DIRECTORY_SOURCE_FILES: dict[str, tuple[str, str]] = {
    "reactor_result_path": ("reactor_result", "plant_reactor_result.json"),
    "tower_result_path": ("tower_result", "retrofit_tower_equipment.json"),
    "scheme_path": ("scheme", "scheme.json"),
    "plant_info_path": ("plant_info", "plant_info.json"),
    "diagnosis_report_path": ("plant_diagnosis", "plant_diagnosis_report.md"),
    "new_device_params_path": ("new_device_params", "new_device_params.json"),
    "retrofit_equipment_path": ("retrofit_equipment", "retrofit_equipment.json"),
    "retrofit_topology_path": ("retrofit_topology", "retrofit_topology.json"),
    "plant_result_path": ("plant_level", "plant_level_result.json"),
}

# 禁止进入工程事实的控制/推测/LLM/状态字段，清洗时一律剔除
FORBIDDEN_KEYS = {
    "project",
    "user",
    "enterprise",
    "energy",
    "fa",
    "gaps",
    "consistency_issues",
    "section_coverage",
    "scheme_state",
    "final_selection",
    "adapter_mode",
    "project_id",
    "project_name",
    "project_name_source",
    "project_level",
    "project_type",
    "profile_provenance",
    "score",
    "report_rows",
    "report_category",
    "report_role",
    "raw_markdown",
    "_meta",
    "authority",
}


class OperationCancelled(Exception):
    """协作式取消信号：MCP 层设置 cancel_event 后，核心在检查点抛出。

    该异常必须在所有宽泛 ``except Exception`` 之前透传，不得被包装成
    ``status=failed`` 的业务失败结果。
    """


def _cancelled(cancel_event: threading.Event | None) -> bool:
    """判断协作式取消信号是否已被设置。"""
    return cancel_event is not None and cancel_event.is_set()


def _check_cancel(cancel_event: threading.Event | None) -> None:
    """取消检查点：信号已设置时抛出 OperationCancelled。"""
    if _cancelled(cancel_event):
        raise OperationCancelled("engineering_facts operation cancelled")


@dataclass
class Diagnostic:
    """Tool 返回中的诊断项，不写入工程事实 JSON。"""

    level: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """把诊断项转为 Tool 返回中的普通字典。"""
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass
class Source:
    """已识别来源文件。"""

    source_id: str
    source_type: str
    path: Path
    schema_version: str | None = None
    logical_location: str | None = None

    def location(self, source_root: Path) -> str:
        """返回相对来源根目录的 POSIX 风格路径，无法相对时回退绝对路径。"""
        if self.logical_location is not None:
            return self.logical_location
        try:
            return self.path.relative_to(source_root).as_posix()
        except ValueError:
            return str(self.path)

    def as_fact_source(self, source_root: Path) -> dict[str, Any]:
        """序列化为 sources 清单中的一条，仅保留来源元信息。"""
        data: dict[str, Any] = {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "location": self.location(source_root),
        }
        if self.schema_version:
            data["schema_version"] = self.schema_version
        return data

    def ref(self, path: str | None = None) -> dict[str, str]:
        """构造一条溯源引用对象；可选 path 指向来源内部的具体字段路径。"""
        ref = {"source_id": self.source_id}
        if path:
            ref["path"] = path
        return ref


def execute(
    request: dict[str, Any],
    *,
    content: Content,
    host_client: HostClient,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """执行业务 Tool。

    Args:
        request: 业务输入。正式入口使用 provider/root/file_overrides 分组；
            旧 source_location 结构仅作为核心层兼容输入保留。
        content: 进度通知适配器。
        host_client: 宿主逻辑文件读写适配器，最终产物经它保存。
        cancel_event: 可选协作式取消信号；已设置时核心在检查点抛出
            OperationCancelled，不返回业务失败信封。不传时行为与旧版一致。

    Returns:
        包含 status、artifact、summary、diagnostics 的小对象。

    Raises:
        OperationCancelled: cancel_event 已设置且执行到某个取消检查点。
    """
    diagnostics: list[Diagnostic] = []
    _check_cancel(cancel_event)
    request = _normalize_request(request, diagnostics)
    if _has_fatal(diagnostics):
        return _failed_response(diagnostics)

    fatal = _validate_request(request, diagnostics)
    if fatal:
        return _failed_response(diagnostics)

    source_location = request["source_location"]
    source_root = _source_root_for_facts(source_location)
    construction_unit = _construction_unit(request)

    _check_cancel(cancel_event)  # 来源发现前
    content.report_progress(0, 100, "扫描工程输入来源")
    if source_location["provider"] == "host_file":
        content.report_progress(35, 100, "读取工程输入来源")
        sources, payloads = _load_host_file_source(
            source_location["location"],
            host_client,
            diagnostics,
            cancel_event,
        )
    elif source_location["provider"] == "host_directory":
        content.report_progress(35, 100, "读取工程输入来源")
        sources, payloads = _load_host_directory_sources(
            source_location["location"],
            source_location.get("file_overrides"),
            host_client,
            diagnostics,
            cancel_event,
        )
    else:
        sources = _discover_sources(source_root, diagnostics, cancel_event)
        content.report_progress(35, 100, "读取工程输入来源")
        payloads = _load_payloads(sources, diagnostics, cancel_event)
    _check_cancel(cancel_event)  # 来源发现后
    _validate_source_set(sources, diagnostics)
    _check_cancel(cancel_event)  # 来源读取后
    _warn_related_source_gaps(payloads, diagnostics)

    if _has_fatal(diagnostics):
        return _failed_response(diagnostics)

    _check_cancel(cancel_event)  # 事实组装前
    content.report_progress(65, 100, "组装工程事实")
    facts = _build_facts(
        payloads=payloads,
        sources=sources,
        source_root=source_root,
        construction_unit=construction_unit,
        diagnostics=diagnostics,
    )
    if _has_fatal(diagnostics):
        return _failed_response(diagnostics)
    _check_cancel(cancel_event)  # 事实组装后

    _check_cancel(cancel_event)  # 最终 JSON 写入前：最后取消检查点，之后原子提交
    content.report_progress(90, 100, "保存工程事实")
    _check_cancel(cancel_event)
    artifact = _write_artifact(facts, host_client)
    content.report_progress(100, 100, "工程事实已完成")
    return {
        "status": "completed",
        "artifact": artifact,
        "engineering_facts": facts,
        "summary": _summary(facts),
        "diagnostics": [item.as_dict() for item in diagnostics],
    }


def _normalize_request(
    request: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """把 MCP 新入参归一为核心内部 source_location 结构。

    新公开契约使用 provider/root/file_overrides 分组，避免顶层参数过多；
    旧 source_location 结构用于既有本地测试与少量内部调用兼容。
    """
    if not isinstance(request, dict):
        return request
    if "source_location" in request:
        return request

    extra = sorted(set(request) - ENGINEERING_INPUT_FIELDS)
    if extra:
        diagnostics.append(
            Diagnostic(
                "fatal",
                "UNSUPPORTED_REQUEST_FIELD",
                f"unsupported request fields: {extra}",
            )
        )

    source_location: dict[str, Any] = {
        "provider": request.get("provider"),
        "location": request.get("root"),
    }
    if "file_overrides" in request:
        source_location["file_overrides"] = request.get("file_overrides")

    normalized: dict[str, Any] = {"source_location": source_location}
    if "construction_unit" in request:
        normalized["construction_unit"] = request.get("construction_unit")
    return normalized


def _validate_request(request: dict[str, Any], diagnostics: list[Diagnostic]) -> bool:
    """校验业务输入结构，返回是否存在致命错误。

    核心内部只接受 source_location 与可选 construction_unit 两个业务字段；
    逐一校验 source_location 的 provider/location/file_overrides，并在
    construction_unit 传入时校验其类型。
    不合法项以 fatal 诊断追加到 diagnostics。
    """
    if not isinstance(request, dict):
        diagnostics.append(Diagnostic("fatal", "INVALID_REQUEST", "request must be an object."))
        return True

    extra = sorted(set(request) - LEGACY_REQUEST_FIELDS)
    if extra:
        diagnostics.append(
            Diagnostic(
                "fatal",
                "UNSUPPORTED_REQUEST_FIELD",
                f"unsupported request fields: {extra}",
            )
        )

    source_location = request.get("source_location")
    if not isinstance(source_location, dict):
        diagnostics.append(
            Diagnostic(
                "fatal",
                "INVALID_SOURCE_LOCATION",
                "source_location must be an object.",
            )
        )
    else:
        extra_location_fields = sorted(
            set(source_location) - SOURCE_LOCATION_FIELDS
        )
        if extra_location_fields:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "UNSUPPORTED_SOURCE_LOCATION_FIELD",
                    (
                        "unsupported source_location fields: "
                        f"{extra_location_fields}"
                    ),
                )
            )

    if isinstance(source_location, dict) and (
        source_location.get("provider") not in SOURCE_PROVIDERS
    ):
        diagnostics.append(
            Diagnostic(
                "fatal",
                "UNSUPPORTED_SOURCE_PROVIDER",
                f"unsupported source_location.provider: {source_location.get('provider')}",
            )
        )
    elif isinstance(source_location, dict):
        location = source_location.get("location")
        if not isinstance(location, str) or not location.strip():
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "INVALID_SOURCE_LOCATION",
                    "source_location.location is required.",
                )
            )
        elif source_location.get("provider") == "local_directory" and (
            not Path(location).exists() or not Path(location).is_dir()
        ):
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SOURCE_LOCATION_NOT_FOUND",
                    f"source directory not found: {location}",
                )
            )
        _validate_file_overrides(source_location.get("file_overrides"), diagnostics)

    construction_unit = request.get("construction_unit")
    if construction_unit is not None and not isinstance(construction_unit, str):
        diagnostics.append(
            Diagnostic(
                "fatal",
                "INVALID_CONSTRUCTION_UNIT",
                "construction_unit must be a string when provided.",
            )
        )

    return _has_fatal(diagnostics)


def _validate_file_overrides(
    file_overrides: Any,
    diagnostics: list[Diagnostic],
) -> None:
    """校验来源文件名覆盖对象。"""
    if file_overrides is None:
        return
    if not isinstance(file_overrides, dict):
        diagnostics.append(
            Diagnostic(
                "fatal",
                "INVALID_SOURCE_FILE_OVERRIDES",
                "file_overrides must be an object when provided.",
            )
        )
        return

    extra = sorted(set(file_overrides) - set(HOST_DIRECTORY_SOURCE_FILES))
    if extra:
        diagnostics.append(
            Diagnostic(
                "fatal",
                "UNSUPPORTED_SOURCE_FILE_FIELD",
                f"unsupported file_overrides fields: {extra}",
            )
        )

    for field_name, value in file_overrides.items():
        if field_name not in HOST_DIRECTORY_SOURCE_FILES or value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "INVALID_SOURCE_FILE_PATH",
                    f"file_overrides.{field_name} must be a non-empty string.",
                )
            )
            continue
        if _is_unsafe_relative_path(value):
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "INVALID_SOURCE_FILE_PATH",
                    (
                        f"file_overrides.{field_name} must be a relative path "
                        "under root and must not contain '..'."
                    ),
                )
            )


def _construction_unit(request: dict[str, Any]) -> str:
    """取出建设单位，空值归一化为空字符串。"""
    value = request.get("construction_unit", "")
    if value is None:
        return ""
    return value.strip()


def _failed_response(diagnostics: list[Diagnostic]) -> dict[str, Any]:
    """构造失败返回对象：artifact 为空，summary 为空。"""
    return {
        "status": "failed",
        "artifact": None,
        "summary": {},
        "diagnostics": [item.as_dict() for item in diagnostics],
    }


def _has_fatal(diagnostics: list[Diagnostic]) -> bool:
    """是否存在致命错误诊断。"""
    return any(item.level == "fatal" for item in diagnostics)


def _source_root_for_facts(source_location: dict[str, Any]) -> Path:
    """返回用于 facts.sources 相对路径计算的来源根。"""
    if source_location["provider"] == "local_directory":
        return Path(source_location["location"]).resolve()
    return Path(source_location["location"])


def _read_json(path: Path) -> Any:
    """读取 JSON 文件，兼容 UTF-8 BOM 前缀。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_text(path: Path) -> str:
    """读取文本文件，编码错误用替换符兜底。"""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _classify_json(data: Any) -> tuple[str | None, str | None]:
    """按内容签名识别 JSON 的角色，返回 (role, schema_version)。

    不依赖文件名或版本后缀，只靠结构特征判断来源角色。
    """
    if not isinstance(data, dict):
        return None, None
    keys = set(data)
    schema_version = data.get("schemaVersion")
    # plant_info 顶层含 plant_info 子对象
    if isinstance(data.get("plant_info"), dict):
        return "plant_info", schema_version
    # plant_level 同时含设计/改造工况与组分
    if {"design_case", "retrofit_case", "components"}.issubset(keys):
        return "plant_level", schema_version
    # scheme 的特征是特定 schemaVersion + schemeKind
    if (
        data.get("schemaVersion") == "topology_retrofit_v1"
        and data.get("schemeKind") == "diagnosis"
    ):
        return "scheme", schema_version
    # retrofit_topology 含节点/边/方案信息
    if {"nodes", "edges", "schemeInfo"}.issubset(keys):
        return "retrofit_topology", schema_version
    # retrofit_equipment 顶层 equipment 是列表
    if "equipment" in keys and isinstance(data.get("equipment"), list):
        return "retrofit_equipment", schema_version
    # 专业结果：塔器 / 反应器都带 combined_schemes
    if "separator" in keys and "combined_schemes" in keys:
        return "tower_result", schema_version
    if "reactors" in keys and "combined_schemes" in keys:
        return "reactor_result", schema_version
    # 新增设备参数：同时含 reactors 与 separators
    if "reactors" in keys and "separators" in keys:
        return "new_device_params", schema_version
    return None, schema_version


def _classify_path(
    path: Path,
    source_root: Path,
    diagnostics: list[Diagnostic],
) -> tuple[str | None, str | None]:
    """按扩展名分派到 JSON 或 Markdown 识别；无法识别返回 (None, None)。"""
    suffix = path.suffix.lower()
    if suffix in {".json", ".jsonc"}:
        try:
            return _classify_json(_read_json(path))
        except json.JSONDecodeError as exc:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SOURCE_JSON_INVALID",
                    f"{_relative_location(path, source_root)} is not valid JSON: {exc.msg}",
                )
            )
            return None, None
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SOURCE_READ_FAILED",
                    f"{_relative_location(path, source_root)}: {exc}",
                )
            )
            return None, None
    if suffix in {".md", ".txt"}:
        # scheme_report.md 只是 scheme.json 的展示版，不重复采集
        if path.name == "scheme_report.md":
            return None, None
        text = _read_text(path)[:20000]
        if text.lstrip().startswith("# 工艺诊断报告") or (
            "## 一、装置总体指标" in text
            and "## 三、关键瓶颈分析" in text
        ):
            return "plant_diagnosis", None
        if "diagnosis" in path.name.lower() or "诊断" in path.name:
            return "plant_diagnosis", None
    return None, None


def _discover_sources(
    source_root: Path,
    diagnostics: list[Diagnostic],
    cancel_event: threading.Event | None = None,
) -> dict[str, Source]:
    """递归扫描来源目录，识别并按角色归并来源文件。

    同一角色匹配到多个文件时记为致命诊断（来源歧义），不返回该角色。
    每个文件识别前后检查取消信号，保证长目录扫描可被协作取消。
    """
    candidates: dict[str, list[Source]] = {}
    resolved_root = source_root.resolve()
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        resolved_path = path.resolve()
        if not _is_relative_to(resolved_path, resolved_root):
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "SOURCE_OUTSIDE_ROOT_SKIPPED",
                    f"source outside declared root skipped: {path}",
                )
            )
            continue
        _check_cancel(cancel_event)  # 单个来源文件读取前
        source_type, schema_version = _classify_path(resolved_path, resolved_root, diagnostics)
        if not source_type:
            continue
        source = Source(
            source_id=source_type,
            source_type=source_type,
            path=resolved_path,
            schema_version=schema_version,
        )
        candidates.setdefault(source_type, []).append(source)

    selected: dict[str, Source] = {}
    for source_type, items in candidates.items():
        if len(items) > 1:
            locations = [item.location(source_root) for item in items]
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "AMBIGUOUS_SOURCE_ROLE",
                    f"role {source_type} matched multiple files: {locations}",
                )
            )
        else:
            selected[source_type] = items[0]
    return selected


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_payloads(
    sources: dict[str, Source],
    diagnostics: list[Diagnostic],
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """读取各来源文件内容；Markdown/文本按文本读，其余按 JSON 读。

    每个来源文件读取前检查取消信号；取消异常不会被逐文件异常边界吞掉。
    """
    payloads: dict[str, Any] = {}
    for source_type, source in sources.items():
        _check_cancel(cancel_event)  # 单个来源文件读取前
        try:
            if source.path.suffix.lower() in {".md", ".txt"}:
                payloads[source_type] = _read_text(source.path)
            else:
                payloads[source_type] = _read_json(source.path)
        except Exception as exc:
            diagnostics.append(Diagnostic("fatal", "SOURCE_READ_FAILED", f"{source_type}: {exc}"))
    return payloads


def _load_host_file_source(
    logical_path: str,
    host_client: HostClient,
    diagnostics: list[Diagnostic],
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, Source], dict[str, Any]]:
    """通过 HostClient 读取一个宿主逻辑文件，并识别为上游来源。"""
    _check_cancel(cancel_event)
    try:
        payload = host_client.get_file(logical_path, kind="bytes")
    except FileNotFoundError:
        diagnostics.append(
            Diagnostic(
                "fatal",
                "SOURCE_LOCATION_NOT_FOUND",
                f"source file not found: {logical_path}",
            )
        )
        return {}, {}
    except Exception as exc:  # noqa: BLE001 - storage adapter boundary
        raise HostStorageError(
            f"HostClient failed to read engineering source file: {logical_path}. Cause: {exc}",
            code="ENGINEERING_FACTS_HOST_STORAGE_ERROR",
        ) from exc

    _check_cancel(cancel_event)
    source_type, schema_version, decoded = _classify_host_file_payload(
        logical_path,
        payload,
        diagnostics,
    )
    if not source_type:
        return {}, {}

    source = Source(
        source_id=source_type,
        source_type=source_type,
        path=Path(logical_path),
        schema_version=schema_version,
        logical_location=logical_path,
    )
    return {source_type: source}, {source_type: decoded}


def _load_host_directory_sources(
    logical_root: str,
    file_overrides: dict[str, Any] | None,
    host_client: HostClient,
    diagnostics: list[Diagnostic],
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, Source], dict[str, Any]]:
    """按标准文件清单读取宿主逻辑目录中的工程来源文件。"""
    sources: dict[str, Source] = {}
    payloads: dict[str, Any] = {}
    overrides = file_overrides if isinstance(file_overrides, dict) else {}

    for field_name, (expected_type, default_name) in HOST_DIRECTORY_SOURCE_FILES.items():
        relative_path = overrides.get(field_name) or default_name
        logical_path = _join_host_logical_path(logical_root, relative_path)

        _check_cancel(cancel_event)
        try:
            payload = host_client.get_file(logical_path, kind="bytes")
        except FileNotFoundError:
            if field_name in overrides:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "SOURCE_FILE_NOT_FOUND_SKIPPED",
                        f"optional source file not found: {logical_path}",
                    )
                )
            continue
        except Exception as exc:  # noqa: BLE001 - storage adapter boundary
            raise HostStorageError(
                (
                    "HostClient failed to read engineering source file: "
                    f"{logical_path}. Cause: {exc}"
                ),
                code="ENGINEERING_FACTS_HOST_STORAGE_ERROR",
            ) from exc

        _check_cancel(cancel_event)
        before = len(diagnostics)
        source_type, schema_version, decoded = _classify_host_file_payload(
            logical_path,
            payload,
            diagnostics,
        )
        new_fatal = any(item.level == "fatal" for item in diagnostics[before:])
        if not source_type:
            if not new_fatal:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "SOURCE_FILE_UNRECOGNIZED_SKIPPED",
                        f"source file is not recognized and was skipped: {logical_path}",
                    )
                )
            continue
        if source_type != expected_type:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SOURCE_ROLE_MISMATCH",
                    (
                        f"{logical_path} was expected to be {expected_type}, "
                        f"but was recognized as {source_type}."
                    ),
                )
            )
            continue
        if source_type in sources:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "AMBIGUOUS_SOURCE_ROLE",
                    f"role {source_type} matched multiple host files.",
                )
            )
            continue

        source = Source(
            source_id=source_type,
            source_type=source_type,
            path=Path(logical_path),
            schema_version=schema_version,
            logical_location=logical_path,
        )
        sources[source_type] = source
        payloads[source_type] = decoded

    return sources, payloads


def _join_host_logical_path(logical_root: str, relative_path: str) -> str:
    """拼接 Host 逻辑目录和相对文件名，统一为 POSIX 风格路径。"""
    root = logical_root.strip().replace("\\", "/").strip("/")
    child = relative_path.strip().replace("\\", "/").strip("/")
    if not root:
        return child
    return f"{root}/{child}"


def _is_unsafe_relative_path(value: str) -> bool:
    """判断 file_overrides 是否试图脱离声明的 root。"""
    normalized = value.strip().replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return True
    parts = [part for part in normalized.split("/") if part]
    return any(part == ".." for part in parts)


def _classify_host_file_payload(
    logical_path: str,
    payload: Any,
    diagnostics: list[Diagnostic],
) -> tuple[str | None, str | None, Any]:
    """识别 HostClient 读到的单文件内容，返回来源角色、版本和已解码内容。"""
    suffix = Path(logical_path).suffix.lower()
    if isinstance(payload, dict):
        source_type, schema_version = _classify_json(payload)
        return source_type, schema_version, payload

    try:
        text = _host_payload_text(payload)
    except (TypeError, UnicodeDecodeError) as exc:
        diagnostics.append(
            Diagnostic(
                "fatal",
                "SOURCE_READ_FAILED",
                f"{logical_path}: cannot decode as UTF-8 text: {exc}",
            )
        )
        return None, None, None

    if suffix in {".json", ".jsonc"}:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SOURCE_JSON_INVALID",
                    f"{logical_path} is not valid JSON: {exc.msg}",
                )
            )
            return None, None, None
        source_type, schema_version = _classify_json(data)
        return source_type, schema_version, data

    if suffix in {".md", ".txt"}:
        if Path(logical_path).name == "scheme_report.md":
            return None, None, text
        head = text[:20000]
        if head.lstrip().startswith("# 工艺诊断报告") or (
            "## 一、装置总体指标" in head
            and "## 三、关键瓶颈分析" in head
        ):
            return "plant_diagnosis", None, text
        if "diagnosis" in logical_path.lower() or "诊断" in logical_path:
            return "plant_diagnosis", None, text
    return None, None, text


def _host_payload_text(payload: Any) -> str:
    """把 HostClient 返回值归一化为文本，供单文件来源识别使用。"""
    if isinstance(payload, str):
        return payload.lstrip("\ufeff")
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8-sig")
    raise TypeError(f"unsupported payload type: {type(payload).__name__}")


def _validate_source_set(
    sources: dict[str, Source],
    diagnostics: list[Diagnostic],
) -> None:
    """至少需要识别到一个来源，但不限定具体来源角色。"""
    if not sources and not _has_fatal(diagnostics):
        diagnostics.append(
            Diagnostic(
                "fatal",
                "NO_RECOGNIZED_SOURCES",
                "no supported engineering source was recognized.",
            )
        )


def _warn_related_source_gaps(
    payloads: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> None:
    """提示已识别来源所暴露的关联资料缺口，不阻断事实整理。"""
    plant_level = payloads.get("plant_level") or {}
    design = plant_level.get("design_case") or {}
    retrofit = plant_level.get("retrofit_case") or {}
    has_reactors = bool(
        (design.get("reactor_config") or [])
        or (retrofit.get("reactor_config") or [])
    )
    has_towers = bool(
        (design.get("separator_config") or [])
        or (retrofit.get("separator_config") or [])
    )
    if has_reactors and "reactor_result" not in payloads:
        diagnostics.append(
            Diagnostic(
                "warning",
                "REACTOR_RESULT_MISSING",
                "plant_level contains reactors but reactor_result is missing.",
            )
        )
    if has_towers and "tower_result" not in payloads:
        diagnostics.append(
            Diagnostic(
                "warning",
                "TOWER_RESULT_MISSING",
                "plant_level contains separators but tower_result is missing.",
            )
        )
    if _has_new_equipment(payloads) and "new_device_params" not in payloads:
        diagnostics.append(
            Diagnostic(
                "warning",
                "NEW_DEVICE_PARAMS_MISSING",
                "new equipment exists but new_device_params is missing.",
            )
        )


def _has_new_equipment(payloads: dict[str, Any]) -> bool:
    """判断候选方案或拓扑中是否存在新增设备。"""
    scheme = payloads.get("scheme") or {}
    for pool in ("candidates", "user_candidates"):
        for item in scheme.get(pool) or []:
            if isinstance(item, dict) and item.get("newEquipment_list"):
                return True
    topology = payloads.get("retrofit_topology") or {}
    for item in topology.get("schemeInfo") or []:
        if isinstance(item, dict) and (item.get("newEquipment") or item.get("newEquipment_list")):
            return True
    return False


def _build_facts(
    *,
    payloads: dict[str, Any],
    sources: dict[str, Source],
    source_root: Path,
    construction_unit: str,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """组装最终工程事实 JSON 的一级结构。

    分块构建 unit/process/scheme/equipment 等，再按 TOP_LEVEL_KEYS 清洗，
    只保留契约允许的顶层字段。
    """
    plant_info = (payloads.get("plant_info") or {}).get("plant_info") or {}
    plant_level = payloads.get("plant_level") or {}
    topology = payloads.get("retrofit_topology") or {}
    source_refs = _source_refs(sources)
    component_names = _component_name_map(plant_level.get("components") or [])

    unit = _build_unit(plant_info, source_refs)
    process = _build_process(plant_level, topology, component_names, source_refs)
    if "scheme" in payloads:
        scheme_analysis, adopted_scheme = _build_scheme_sections(
            payloads.get("scheme") or {}, source_refs, diagnostics
        )
    else:
        scheme_analysis, adopted_scheme = {}, {}
    equipment = _build_equipment(payloads, source_refs)
    _warn_name_conflicts(equipment, process, diagnostics)

    facts = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "sources": [sources[key].as_fact_source(source_root) for key in sorted(sources)],
        "basic_info": {"construction_unit": construction_unit},
        "unit": unit,
        "component_catalog": _clean(copy.deepcopy(plant_level.get("components") or [])),
        "process": process,
        "diagnosis": _build_diagnosis(payloads, source_refs),
        "scheme_analysis": scheme_analysis,
        "adopted_scheme": adopted_scheme,
        "equipment": equipment,
        "derived_facts": _build_derived_facts(unit, process, equipment, facts_hint=payloads),
    }
    return _clean(facts, keep_top=True)


def _source_refs(sources: dict[str, Source]) -> dict[str, Source]:
    """来源引用表；这里直接复用来源字典，便于下游按角色取引用。"""
    return sources


def _ref(
    sources: dict[str, Source],
    source_type: str,
    path: str | None = None,
) -> list[dict[str, str]]:
    """返回指定来源的溯源引用列表；来源缺失时为空列表。"""
    source = sources.get(source_type)
    return [source.ref(path)] if source else []


def _build_unit(plant_info: dict[str, Any], sources: dict[str, Source]) -> dict[str, Any]:
    """从 plant_info 构建 unit 段：装置名称/类型/年运行时长及设计/改造目标。"""
    return _clean(
        {
            "name": plant_info.get("plant_name"),
            "type": plant_info.get("plant_type"),
            "annual_operating_hours": plant_info.get("annual_operating_hours"),
            "unit": plant_info.get("unit"),
            "design_case": _compact_plant_info_case(
                plant_info.get("design_case") or {}, "design", sources
            ),
            "retrofit_case": _compact_plant_info_case(
                plant_info.get("retrofit_case") or {}, "retrofit", sources
            ),
            "stream_mapping_notes": plant_info.get("stream_mapping_notes"),
            "source_refs": _ref(sources, "plant_info", "plant_info"),
        }
    )


def _compact_plant_info_case(
    case: dict[str, Any],
    condition: str,
    sources: dict[str, Source],
) -> dict[str, Any]:
    """归集单个工况（设计/改造）的进料、产物与目标对齐信息。"""
    return _clean(
        {
            "case_name": case.get("case_name"),
            "capacity_ratio": case.get("capacity_ratio"),
            "feeds": [
                _compact_material(
                    item, f"{condition}_case.feeds[{index}]", sources
                )
                for index, item in enumerate(case.get("feeds") or [])
            ],
            "products": [
                _compact_material(
                    item, f"{condition}_case.products[{index}]", sources
                )
                for index, item in enumerate(case.get("products") or [])
            ],
            "target_alignment": case.get("target_alignment"),
            "source_refs": _ref(sources, "plant_info", f"plant_info.{condition}_case"),
        }
    )


def _compact_material(
    item: dict[str, Any],
    source_path: str,
    sources: dict[str, Source],
) -> dict[str, Any]:
    """归集单个物料（进料/产物）的名称、流股号、产能与流量。"""
    return _clean(
        {
            "name": item.get("name"),
            "stream_id": item.get("stream_id"),
            "annual_capacity": _clean(copy.deepcopy(item.get("annual_capacity"))),
            "hourly_rate": _clean(copy.deepcopy(item.get("hourly_rate"))),
            "flow_rate": _clean(copy.deepcopy(item.get("flow_rate"))),
            "is_main_feed": item.get("is_main_feed"),
            "is_main_product": item.get("is_main_product"),
            "source_refs": _ref(sources, "plant_info", f"plant_info.{source_path}"),
        }
    )


def _component_name_map(components: list[dict[str, Any]]) -> dict[str, str]:
    """建立组分子式到名称的映射，供流股组分相充名。"""
    return {
        str(item.get("formula")): item.get("name")
        for item in components
        if isinstance(item, dict) and item.get("formula") and item.get("name")
    }


def _build_process(
    plant_level: dict[str, Any],
    topology: dict[str, Any],
    component_names: dict[str, str],
    sources: dict[str, Source],
) -> dict[str, Any]:
    """构建 process 段：分别聚合设计工况与改造工况的流程/流股/设备配置。"""
    return {
        "design": _build_process_case(
            plant_level.get("design_case") or {},
            "design",
            topology.get("edges") or [],
            component_names,
            sources,
            use_external_topology=False,
        ),
        "retrofit": _build_process_case(
            plant_level.get("retrofit_case") or {},
            "retrofit",
            topology.get("edges") or [],
            component_names,
            sources,
            use_external_topology=True,
            topology=topology,
        ),
    }


def _build_process_case(
    case: dict[str, Any],
    condition: str,
    topology_edges: list[dict[str, Any]],
    component_names: dict[str, str],
    sources: dict[str, Source],
    *,
    use_external_topology: bool,
    topology: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """聚合单个工况（设计/改造）的流程事实。

    设计工况沿用 plant_level 自带拓扑；改造工况采用 retrofit_topology 提供的
    外部拓扑与方案变更，并将拓扑边语义注入流股名称。
    """
    # 由拓扑边建立流股 id -> 语义名称，用于给流股补充可读名称
    semantic_names = {
        str(edge.get("id")): edge.get("name")
        for edge in topology_edges
        if isinstance(edge, dict)
    }
    streams = [
        _stream(item, condition, semantic_names, component_names, sources)
        for item in case.get("streams") or []
        if isinstance(item, dict)
    ]
    # 无 source 的边视为外部进料（装置边界入口）
    external_ids = {
        str(edge.get("id"))
        for edge in topology_edges
        if isinstance(edge, dict) and not edge.get("source")
    }
    if use_external_topology:
        case_topology = {
            "nodes": (topology or {}).get("nodes") or [],
            "edges": topology_edges,
        }
    else:
        case_topology = case.get("topology")
    out = {
        "streams": streams,
        "external_feeds": [
            item
            for item in streams
            if not item.get("source_equipment_id") or str(item.get("stream_id")) in external_ids
        ],
        "product_streams": [item for item in streams if item.get("target_product_stream")],
        "separators": _clean(copy.deepcopy(case.get("separator_config") or [])),
        "reactors": _clean(copy.deepcopy(case.get("reactor_config") or [])),
        "topology": _clean(copy.deepcopy(case_topology or {})),
        "source_refs": _ref(sources, "plant_level", f"{condition}_case"),
    }
    if use_external_topology:
        out["scheme_changes"] = _clean(copy.deepcopy((topology or {}).get("schemeInfo") or []))
        out["topology_source_refs"] = _ref(sources, "retrofit_topology")
    return _clean(out)


def _stream(
    item: dict[str, Any],
    condition: str,
    semantic_names: dict[str, str],
    component_names: dict[str, str],
    sources: dict[str, Source],
) -> dict[str, Any]:
    """将单条流股映射为工程事实结构，含流量/温度/压力/组分及溯源。"""
    stream_id = item.get("id")
    flow = item.get("flow_rate") or {}
    temperature = item.get("temperature") or {}
    pressure = item.get("pressure") or {}
    return _clean(
        {
            "stream_id": stream_id,
            "name": semantic_names.get(str(stream_id)) or item.get("name"),
            "raw_name": item.get("name"),
            "source_equipment_id": item.get("source"),
            "target_equipment_id": item.get("target"),
            "flow": {"value": flow.get("value"), "unit": flow.get("unit")},
            "temperature": {
                "value": temperature.get("value"),
                "unit": temperature.get("unit") or "C",
            },
            "pressure": {"value": pressure.get("value"), "unit": pressure.get("unit") or "MPa"},
            "phase": item.get("phase"),
            "target_product_stream": bool(item.get("target_product_stream")),
            "composition": [
                _clean(
                    {
                        "component_code": comp.get("component"),
                        "component_name": component_names.get(
                            str(comp.get("component")), comp.get("component")
                        ),
                        "mass_fraction": comp.get("mass_fraction"),
                        "mole_fraction": comp.get("mole_fraction"),
                    }
                )
                for comp in item.get("composition") or []
                if isinstance(comp, dict)
            ],
            "source_refs": _ref(
                sources,
                "plant_level",
                f"{condition}_case.streams[id={stream_id}]",
            ),
        }
    )


def _build_diagnosis(payloads: dict[str, Any], sources: dict[str, Source]) -> dict[str, Any]:
    """从诊断 Markdown 解析收率、反应器、瓶颈三类结构化事实。"""
    text = payloads.get("plant_diagnosis") or ""
    return _clean(
        {
            "yield_summary": _parse_yield_summary(text),
            "reactor_table": _parse_reactor_table(text),
            "bottleneck_table": _parse_bottleneck_table(text),
            "source_refs": _ref(sources, "plant_diagnosis"),
        }
    )


def _parse_yield_summary(text: str) -> dict[str, Any]:
    """从收率汇总表正则提取设计与运行总收率百分比。"""
    result: dict[str, Any] = {}
    design = re.search(r"\|\s*设计总收率\s*\|\s*([0-9.]+)%", text)
    operating = re.search(r"\|\s*运行总收率\s*\|\s*([0-9.]+)%", text)
    if design:
        result["design_total_yield_pct"] = float(design.group(1))
    if operating:
        result["operating_total_yield_pct"] = float(operating.group(1))
    return result


def _parse_reactor_table(text: str) -> list[dict[str, Any]]:
    """从反应器工况表正则解析每台反应器的设计/运行转化率与状态。"""
    rows = []
    pattern = r"\|\s*([^|\n]+?)\s*\|\s*([0-9.]+)%\s*\|\s*([0-9.]+)%\s*\|\s*([^|\n]+?)\s*\|"
    for match in re.finditer(pattern, text):
        name = match.group(1).strip()
        if name == "设备名称":
            continue
        rows.append(
            {
                "name": name,
                "design_conversion_pct": float(match.group(2)),
                "operating_conversion_pct": float(match.group(3)),
                "status": match.group(4).strip(),
            }
        )
    return rows


def _parse_bottleneck_table(text: str) -> list[dict[str, Any]]:
    """从瓶颈分析表正则解析设备编号、描述与建议。"""
    rows = []
    for match in re.finditer(r"\|\s*([0-9]+)\s*\|\s*([^|\n]+?)\s*\|\s*([^|\n]+?)\s*\|", text):
        if match.group(1) == "设备编号":
            continue
        rows.append(
            {
                "equipment_id": match.group(1),
                "description": match.group(2).strip(),
                "recommendation": match.group(3).strip(),
            }
        )
    return rows


def _build_scheme_sections(
    scheme: dict[str, Any],
    sources: dict[str, Source],
    diagnostics: list[Diagnostic],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构建 scheme_analysis 与 adopted_scheme 两段。

    adopted_scheme 仅由 scheme.json.user_selected 决定，且必须在 candidates 与
    user_candidates 中精确唯一匹配。缺少选择时保留方案分析并给出 warning；
    已有选择但未匹配或重名歧义仍是致命错误。
    """
    candidates = [
        _scheme_item(item, "candidates", index, sources, diagnostics)
        for index, item in enumerate(scheme.get("candidates") or [])
        if isinstance(item, dict)
    ]
    user_candidates = [
        _scheme_item(item, "user_candidates", index, sources, diagnostics)
        for index, item in enumerate(scheme.get("user_candidates") or [])
        if isinstance(item, dict)
    ]
    recommendation = scheme.get("recommendation") or {}
    scheme_analysis = _clean(
        {
            "diagnosis": [
                _scheme_diagnosis(item, index, sources)
                for index, item in enumerate(scheme.get("issues") or [])
                if isinstance(item, dict)
            ],
            "candidates": candidates,
            "user_candidates": user_candidates,
            "recommendation": {
                "combination": _clean(copy.deepcopy(recommendation.get("combination") or [])),
                "reason": recommendation.get("reason"),
                "source_refs": _ref(sources, "scheme", "recommendation"),
            },
            "source_refs": _ref(sources, "scheme"),
        }
    )
    selected_names = _selected_names(scheme.get("user_selected"))
    if not selected_names:
        diagnostics.append(
            Diagnostic(
                "warning",
                "USER_SELECTED_MISSING",
                "scheme.json has no user_selected; adopted_scheme was left empty.",
            )
        )
        return scheme_analysis, {}

    selected_schemes = []
    raw_pools = [
        ("candidates", index, item)
        for index, item in enumerate(scheme.get("candidates") or [])
        if isinstance(item, dict)
    ] + [
        ("user_candidates", index, item)
        for index, item in enumerate(scheme.get("user_candidates") or [])
        if isinstance(item, dict)
    ]
    for selected_name in selected_names:
        matches = [
            (pool, index, item)
            for pool, index, item in raw_pools
            if _scheme_matches(item, selected_name)
        ]
        if not matches:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "USER_SELECTED_NOT_FOUND",
                    f"user_selected not found: {selected_name}",
                )
            )
        elif len(matches) > 1:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "USER_SELECTED_AMBIGUOUS",
                    f"user_selected matched multiple schemes: {selected_name}",
                )
            )
        else:
            pool, index, item = matches[0]
            selected_schemes.append(_scheme_item(item, pool, index, sources, diagnostics))

    adopted_refs = _ref(sources, "scheme", "user_selected")
    adopted_refs.extend(_ref(sources, "scheme", "optimizated"))
    adopted_scheme = _clean(
        {
            "selected_names": selected_names,
            "optimized": bool(scheme.get("optimizated")),
            "schemes": selected_schemes,
            "source_refs": adopted_refs,
        }
    )
    return scheme_analysis, adopted_scheme


def _selected_names(value: Any) -> list[str]:
    """把 user_selected 归一化为名称列表，兼容列表/字符串/字典三种形态。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, dict):
        name = (
            value.get("name")
            or value.get("scheme_name")
            or value.get("schemeId")
            or value.get("scheme_id")
        )
        return [str(name).strip()] if name else []
    return []


def _scheme_matches(item: dict[str, Any], selected_name: str) -> bool:
    """判断候选方案是否与所选名称匹配（按 name/schemeId/scheme_id/id）。"""
    identifiers = {item.get("name"), item.get("schemeId"), item.get("scheme_id"), item.get("id")}
    return selected_name in {str(value) for value in identifiers if value not in (None, "")}


def _scheme_item(
    item: dict[str, Any],
    pool: str,
    index: int,
    sources: dict[str, Source],
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """把单个候选方案映射为工程事实结构，去掉评分，保留描述与设备/流股变更。"""
    return _clean(
        {
            "scheme_id": item.get("schemeId") or item.get("scheme_id") or item.get("id"),
            "name": item.get("name"),
            "brief": item.get("brief"),
            "source_pool": pool,
            "retrofit_type": item.get("retrofitType"),
            "retrofit_type_label": item.get("retrofitTypeLabel"),
            "description": item.get("description"),
            "new_equipment": _clean(copy.deepcopy(item.get("newEquipment_list") or [])),
            "removed_equipment": _clean(copy.deepcopy(item.get("removedEquipment_list") or [])),
            "new_streams": _clean(copy.deepcopy(item.get("newStream_list") or [])),
            "modified_streams": _clean(copy.deepcopy(item.get("modifiedStream_list") or [])),
            "removed_streams": _clean(copy.deepcopy(item.get("removedStream_list") or [])),
            "outlet_disposition": _clean(copy.deepcopy(item.get("outletDisposition") or [])),
            "resulting_topology_pattern": item.get("resultingTopologyPattern"),
            "resulting_topology_pattern_label": item.get("resultingTopologyPatternLabel"),
            "expected_effect": item.get("expectedEffect"),
            "scheme_details": item.get("schemeDetails"),
            "evaluation": _scheme_evaluation(item.get("evaluation") or {}, diagnostics),
            "source_refs": _ref(sources, "scheme", f"{pool}[{index}]"),
        }
    )


def _scheme_evaluation(
    evaluation: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """动态保留方案评价维度，归一化键名并在冲突时返回 fatal。"""
    out = {}
    if not isinstance(evaluation, dict):
        return out
    for raw_key, raw_item in evaluation.items():
        key = _evaluation_key(raw_key, diagnostics)
        if key is None:
            continue
        if key in out:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SCHEME_EVALUATION_KEY_COLLISION",
                    f"scheme evaluation key collision after normalization: {raw_key}",
                )
            )
            continue
        out[key] = _scheme_evaluation_fields(
            raw_item if isinstance(raw_item, dict) else {"value": raw_item},
            key,
            diagnostics,
        )
    return _clean(out)


def _evaluation_key(raw_key: Any, diagnostics: list[Diagnostic]) -> str | None:
    key_text = str(raw_key or "").strip()
    if not key_text:
        diagnostics.append(
            Diagnostic(
                "fatal",
                "SCHEME_EVALUATION_KEY_COLLISION",
                f"scheme evaluation key is empty or symbol-only: {raw_key}",
            )
        )
        return None
    if any("\u4e00" <= char <= "\u9fff" for char in key_text):
        return key_text
    if re.fullmatch(r"[\W_]+", key_text, flags=re.ASCII):
        diagnostics.append(
            Diagnostic(
                "fatal",
                "SCHEME_EVALUATION_KEY_COLLISION",
                f"scheme evaluation key is empty or symbol-only: {raw_key}",
            )
        )
        return None
    return _snake(key_text)


def _scheme_evaluation_fields(
    item: dict[str, Any],
    dimension_key: str,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    known_labels = {
        "technical_feasibility": "技术可行性",
        "implementation_complexity": "实施复杂度",
        "operational_risk": "运行风险",
    }
    field_aliases = {
        "displayName": "display_name",
        "keyBasis": "key_basis",
        "keyMetrics": "key_metrics",
        "pendingItems": "pending_items",
    }
    out: dict[str, Any] = {}
    explicit_name_fields = {"label", "display_name", "name"}
    for raw_key, value in item.items():
        raw_key_text = str(raw_key or "").strip()
        if raw_key_text in FORBIDDEN_KEYS or "llm" in raw_key_text.lower():
            continue
        key = field_aliases.get(raw_key_text, _snake(raw_key_text))
        if not key or not any(char.isalnum() for char in key):
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SCHEME_EVALUATION_KEY_COLLISION",
                    f"scheme evaluation field key is empty or symbol-only: "
                    f"{dimension_key}.{raw_key}",
                )
            )
            continue
        if key in out and out[key] != value:
            diagnostics.append(
                Diagnostic(
                    "fatal",
                    "SCHEME_EVALUATION_KEY_COLLISION",
                    f"scheme evaluation field collision after normalization: {dimension_key}.{raw_key}",
                )
            )
            continue
        out[key] = value
    if dimension_key in known_labels and not explicit_name_fields.intersection(out):
        out["label"] = known_labels[dimension_key]
    return _clean(out)


def _scheme_diagnosis(
    item: dict[str, Any],
    index: int,
    sources: dict[str, Source],
) -> dict[str, Any]:
    """把 scheme.issues 中的单条诊断映射为工程事实结构。"""
    diagnosis = item.get("diagnosis") or {}
    return _clean(
        {
            "issue_id": item.get("issueId"),
            "constraint_component": diagnosis.get("constraintComponent"),
            "associated_component": diagnosis.get("associatedComponent"),
            "boiling_point_difference": diagnosis.get("boilingPointDiff"),
            "bottleneck_equipment": diagnosis.get("bottleneckEquipment"),
            "current_topology_pattern": diagnosis.get("currentTopologyPattern"),
            "current_topology_pattern_label": diagnosis.get("currentTopologyPatternLabel"),
            "bottleneck_type": diagnosis.get("bottleneckType"),
            "bottleneck_type_label": diagnosis.get("bottleneckTypeLabel"),
            "recommended_retrofit_pattern": diagnosis.get("recommendedRetrofitPattern"),
            "recommended_retrofit_pattern_label": diagnosis.get("recommendedRetrofitPatternLabel"),
            "basis": diagnosis.get("basis"),
            "assumptions": diagnosis.get("assumptions"),
            "risks": diagnosis.get("risks"),
            "source_refs": _ref(sources, "scheme", f"issues[{index}].diagnosis"),
        }
    )


def _build_equipment(payloads: dict[str, Any], sources: dict[str, Source]) -> dict[str, Any]:
    """构建 equipment 段：设备目录、反应器/塔器专业评估及新增设备参数。"""
    reactor_result = _to_snake(payloads.get("reactor_result") or {})
    tower_result = _to_snake(payloads.get("tower_result") or {})
    return _clean(
        {
            "object_catalog": _clean(
                _to_snake(
                    (payloads.get("retrofit_equipment") or {}).get("equipment")
                    or []
                )
            ),
            "reactor": {
                "evaluations": reactor_result.get("reactors"),
                "retrofit_required": reactor_result.get("retrofit_required"),
                "conclusion": reactor_result.get("conclusion"),
                "selected_scheme": reactor_result.get("selected_combined_scheme"),
                "combined_schemes": reactor_result.get("combined_schemes"),
                "source_refs": _ref(sources, "reactor_result"),
            },
            "tower": {
                "evaluations": tower_result.get("separator"),
                "retrofit_required": tower_result.get("retrofit_required"),
                "conclusion": tower_result.get("conclusion"),
                "combined_schemes": tower_result.get("combined_schemes"),
                "source_refs": _ref(sources, "tower_result"),
            },
            "new_device_parameters": _clean(_to_snake(payloads.get("new_device_params") or {})),
            "source_refs": _ref(sources, "retrofit_equipment"),
        }
    )


def _build_derived_facts(
    unit: dict[str, Any],
    process: dict[str, Any],
    equipment: dict[str, Any],
    *,
    facts_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """计算可复算的派生事实：年化流股量与原辅料年消耗。

    只针对单位为 kg/h 的流股，用「流量 × 年运行时长 / 1000」折算为 t/a，
    并保留输入值、单位、运行时与公式，不冒充上游名义产能。
    """
    hours = unit.get("annual_operating_hours")
    stream_quantities = []
    material_consumption = []
    for condition in ("design", "retrofit"):
        case = process.get(condition) or {}
        for stream in case.get("streams") or []:
            flow = stream.get("flow") or {}
            if flow.get("unit") != "kg/h":
                continue
            annual = _annualize(flow.get("value"), hours)
            if annual is None:
                continue
            roles = []
            if stream in (case.get("external_feeds") or []):
                roles.append("external_feed")
            if stream in (case.get("product_streams") or []):
                roles.append("product_stream")
            source_refs = list(stream.get("source_refs") or [])
            source_refs.append(
                {
                    "source_id": "plant_info",
                    "path": "plant_info.annual_operating_hours",
                }
            )
            stream_quantities.append(
                _clean(
                    {
                        "condition": condition,
                        "stream_id": stream.get("stream_id"),
                        "name": stream.get("name"),
                        "roles": roles,
                        "input": {
                            "flow_value": (stream.get("flow") or {}).get("value"),
                            "flow_unit": (stream.get("flow") or {}).get("unit"),
                            "annual_operating_hours": hours,
                        },
                        "annual_quantity": {"value": annual, "unit": "t/a"},
                        "formula": "flow_value(kg/h) * annual_operating_hours / 1000",
                        "source_refs": source_refs,
                    }
                )
            )
        for stream in case.get("external_feeds") or []:
            material_consumption.extend(
                _material_consumption_rows(condition, stream, hours)
            )
    utility_summary = _build_utility_consumption_summary(equipment, hours)
    return _clean(
        {
            "annualized_stream_quantities": stream_quantities,
            "annualized_material_consumption": material_consumption,
            "utility_consumption_summary": utility_summary,
            "energy_conversion": _build_energy_conversion(
                utility_summary,
                _select_energy_conversion_type(facts_hint or {}),
            ),
        }
    )


def _material_consumption_rows(
    condition: str,
    stream: dict[str, Any],
    hours: Any,
) -> list[dict[str, Any]]:
    """为单个外部进料流股生成原辅料年消耗行。"""
    flow = stream.get("flow") or {}
    if flow.get("unit") != "kg/h":
        return []
    annual = _annualize(flow.get("value"), hours)
    if annual is None:
        return []
    source_refs = list(stream.get("source_refs") or [])
    source_refs.append(
        {"source_id": "plant_info", "path": "plant_info.annual_operating_hours"}
    )
    return [
        _clean(
            {
                "condition": condition,
                "stream_id": stream.get("stream_id"),
                "stream_name": stream.get("name"),
                "input": {
                    "flow_value": flow.get("value"),
                    "flow_unit": flow.get("unit"),
                    "annual_operating_hours": hours,
                },
                "annual_consumption": {"value": annual, "unit": "t/a"},
                "formula": (
                    "flow_value(kg/h) * annual_operating_hours(h/a) / 1000"
                ),
                "source_refs": source_refs,
            }
        )
    ]


def _annualize(flow_value: Any, hours: Any) -> float | None:
    """把 kg/h 流量折算为 t/a 年化量；输入非法时返回 None。"""
    try:
        return round(float(flow_value) * float(hours) / 1000.0, 6)
    except (TypeError, ValueError):
        return None


def _build_utility_consumption_summary(
    equipment: dict[str, Any],
    hours: Any,
) -> dict[str, Any]:
    """从设备专业事实中识别公用工程，按工况与对象生成可复算年量。"""
    items = []
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in _extract_equipment_utilities(equipment):
        # 识别出的原始公用工程先归一化，无法归一化的丢弃
        normalized = _normalize_utility_item(item, hours)
        if not normalized:
            continue
        items.append(normalized)
    # 去重后再汇总，避免同一设备/介质重复计量
    items = _dedupe_utility_items(items)
    for normalized in items:
        # 汇总键：工况 + 介质编码 + 年量单位，同键归并到同一分项
        key = (
            normalized.get("condition", ""),
            normalized.get("medium_code", ""),
            normalized.get("annual_quantity", {}).get("unit", ""),
        )
        # 缺年量、缺介质或单位无法聚合的条目不参与 totals
        if not normalized.get("annual_quantity") or not key[1] or not key[2]:
            continue
        bucket = grouped.setdefault(
            key,
            {
                "condition": normalized.get("condition"),
                "medium_code": normalized.get("medium_code"),
                "medium_name": normalized.get("medium_name"),
                "annual_quantity": {"value": 0.0, "unit": key[2]},
                "item_count": 0,
            },
        )
        bucket["annual_quantity"]["value"] = round(
            bucket["annual_quantity"]["value"]
            + float(normalized["annual_quantity"]["value"]),
            6,
        )
        bucket["item_count"] += 1
    return _clean(
        {
            "status": "identified" if items else "not_identified",
            "annual_operating_hours": hours,
            "items": items,
            "totals": list(grouped.values()),
        }
    )


def _extract_equipment_utilities(equipment: dict[str, Any]) -> list[dict[str, Any]]:
    """只在 equipment 根内扫描设备专业公用工程字段，避免误读工艺流股 medium。"""
    rows: list[dict[str, Any]] = []
    # 已选反应器方案单独抽取，与通用递归扫描互为补充
    rows.extend(_selected_reactor_utility_rows(equipment.get("reactor") or {}))

    def visit(value: Any, path: str, context: dict[str, Any]) -> None:
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]", context)
            return
        if not isinstance(value, dict):
            return

        # 设备标识沿路径向下继承，优先用更具体的当前节点覆盖
        current = dict(context)
        for key in ("equipment_id", "id", "tag", "name", "equipment_name"):
            if value.get(key) not in (None, ""):
                current.setdefault(key, value.get(key))
        if path.startswith("reactor."):
            current.setdefault("discipline", "reactor")
        elif path.startswith("tower."):
            current.setdefault("discipline", "tower")
        # 从路径判断工况，显式命中则覆盖，否则默认改造工况
        explicit_condition = _explicit_condition_from_path(path)
        if explicit_condition:
            current["condition"] = explicit_condition
        else:
            current.setdefault("condition", "retrofit")

        utility = value.get("utility")
        if isinstance(utility, dict):
            # 显式 utility 子对象直接作为一条公用工程记录
            row = dict(current)
            row.update(utility)
            row["source_path"] = f"{path}.utility"
            rows.append(row)

        utility_medium = value.get("utility_medium") or value.get("heat_medium")
        if utility_medium not in (None, ""):
            # 有介质字段时，把介质与质量流量/功率/压力/温度等拼成一条记录
            row = dict(current)
            row["medium"] = utility_medium
            for key in (
                "utility_mass_flow_kg_h",
                "power_kw",
                "power_k_w",
                "electric_power_kw",
                "pressure_mpa",
                "pressure_mpag",
                "inlet_temperature_c",
                "outlet_temperature_c",
            ):
                if value.get(key) not in (None, ""):
                    row[key] = value.get(key)
            row["source_path"] = path
            rows.append(row)

        duty = _first_value(
            value,
            ("duty", "heat_duty", "heat_duty_kw", "load", "qb_kw"),
        )
        if duty is not None and utility is None and utility_medium in (None, ""):
            # 只有热负荷而没有介质时，仅作为无法折算的线索保留
            rows.append(
                {
                    **current,
                    "medium": "热负荷线索",
                    "duty": duty,
                    "duty_unit": "kW",
                    "source_path": path,
                }
            )

        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            # source_refs 与 utility 不再下钻，避免误读溯源、重复采集
            if key in {"source_refs", "utility"}:
                continue
            # 跳过未选反应器候选方案，防止把不采用方案的消耗也统计进来
            if _is_unselected_reactor_candidate_path(child_path):
                continue
            visit(child, child_path, current)

    visit(equipment, "", {})
    return rows


def _selected_reactor_utility_rows(reactor: dict[str, Any]) -> list[dict[str, Any]]:
    """仅把 selected_combined_scheme 指向的反应器方案纳入公用工程计算。"""
    selected = reactor.get("selected_scheme")
    if not isinstance(selected, dict) or not selected:
        return []
    evaluations = [
        item
        for item in reactor.get("evaluations") or []
        if isinstance(item, dict)
    ]
    # 建立设备 id -> 评估对象索引，便于按选定动作定位来源
    by_id = {str(item.get("id")): item for item in evaluations if item.get("id") is not None}
    rows = []
    for action in _selected_reactor_actions(selected):
        equipment_id = str(action.get("id"))
        source = by_id.get(equipment_id)
        if not source:
            continue
        # 找到该设备上被选中的改造方案，再取运行工况下的公用工程
        chosen = _matching_reactor_scheme(source, action.get("scheme_type"))
        utility = ((chosen.get("operating_conditions") or {}).get("utility") or {})
        if not isinstance(utility, dict) or not utility:
            continue
        row = {
            "discipline": "reactor",
            "condition": "retrofit",
            "equipment_id": equipment_id,
            "equipment_name": source.get("name"),
            "source_path": (
                f"reactor.evaluations[id={equipment_id}].retrofit.schemes"
                f"[scheme_type={action.get('scheme_type')}] + selected_scheme"
            ),
        }
        row.update(utility)
        rows.append(row)
    return rows


def _selected_reactor_actions(selected: dict[str, Any]) -> list[dict[str, Any]]:
    """把选定方案中各分组的动作拍平成统一列表，并标注所属分组。"""
    actions = []
    for group in ("reuse", "addition", "parallel", "heat_transfer", "no_retrofit"):
        for item in selected.get(group) or []:
            if isinstance(item, dict) and item.get("id") is not None:
                actions.append({"group": group, **item})
    return actions


def _matching_reactor_scheme(reactor: dict[str, Any], scheme_type: Any) -> dict[str, Any]:
    """在反应器改造方案列表中按 scheme_type 匹配被选中的方案。"""
    schemes = ((reactor.get("retrofit") or {}).get("schemes") or [])
    for scheme in schemes:
        if isinstance(scheme, dict) and scheme.get("scheme_type") == scheme_type:
            return scheme
    return {}


def _is_unselected_reactor_candidate_path(path: str) -> bool:
    """判断路径是否属于未被选中的反应器候选方案，这些在通用扫描时需跳过。"""
    return (
        path.startswith("reactor.combined_schemes")
        or ".retrofit.schemes" in path
        or path.startswith("reactor.selected_scheme")
    )


def _normalize_utility_item(item: dict[str, Any], hours: Any) -> dict[str, Any] | None:
    # 介质取值优先级：medium > utility_medium > name
    medium_raw = item.get("medium") or item.get("utility_medium") or item.get("name")
    medium_code, medium_name = _normalize_utility_medium(medium_raw)
    duty = _first_value(item, ("duty", "heat_duty", "heat_duty_kw", "load", "qb_kw"))
    quantity_value, quantity_unit, formula = _utility_annual_quantity(item, medium_code, hours)
    annual_quantity = (
        {"value": quantity_value, "unit": quantity_unit}
        if quantity_value is not None and quantity_unit
        else None
    )
    # 有可复算年量才算 annualized，否则作为待补充线索保留
    status = "annualized" if annual_quantity else "clue_only"
    reason = None
    if not annual_quantity:
        reason = "physical_quantity_missing" if duty is not None else "quantity_or_hours_missing"
    source_path = str(item.get("source_path") or "")
    source_id = _source_id_from_equipment_path(source_path)
    return _clean(
        {
            "status": status,
            "reason": reason,
            "condition": item.get("condition") or "retrofit",
            "discipline": item.get("discipline"),
            "equipment_id": item.get("equipment_id") or item.get("id") or item.get("tag"),
            "equipment_name": item.get("equipment_name") or item.get("name"),
            "medium": medium_raw,
            "medium_code": medium_code,
            "medium_name": medium_name,
            "input": _clean(
                {
                    "mass_flow_kg_h": _first_value(
                        item,
                        ("mass_flow_kg_h", "utility_mass_flow_kg_h"),
                    ),
                    "power_kw": _first_value(
                        item,
                        ("power_kw", "power_k_w", "electric_power_kw"),
                    ),
                    "annual_operating_hours": hours,
                    "duty": duty,
                    "duty_unit": item.get("duty_unit") or ("kW" if duty is not None else None),
                    "inlet_temperature_c": item.get("inlet_temperature_c"),
                    "outlet_temperature_c": item.get("outlet_temperature_c"),
                    "pressure_mpa": _first_value(
                        item,
                        ("pressure_mpa", "pressure", "pressure_mpag", "gauge_pressure"),
                    ),
                    "pressure_grade": item.get("pressure_grade"),
                    "steam_grade": item.get("steam_grade"),
                }
            ),
            "annual_quantity": annual_quantity,
            "formula": formula,
            "source_refs": [{"source_id": source_id, "path": source_path}] if source_id else [],
            "utility_identity": _utility_identity(source_path),
        }
    )


def _dedupe_utility_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一设备/工况/介质/确定性物理量重复出现时合并来源，不重复计量。"""
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        annual = item.get("annual_quantity") or {}
        input_data = item.get("input") or {}
        key = (
            item.get("equipment_id"),
            item.get("condition"),
            item.get("medium_code"),
            item.get("utility_identity"),
            annual.get("value"),
            annual.get("unit"),
            input_data.get("mass_flow_kg_h"),
            input_data.get("power_kw"),
            input_data.get("duty"),
        )
        if item.get("equipment_id") in (None, ""):
            key = (*key, item.get("equipment_name"), tuple(_source_paths(item)))
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = copy.deepcopy(item)
            continue
        refs = {
            (ref.get("source_id"), ref.get("path")): ref
            for ref in existing.get("source_refs") or []
            if isinstance(ref, dict)
        }
        for ref in item.get("source_refs") or []:
            if isinstance(ref, dict):
                refs[(ref.get("source_id"), ref.get("path"))] = ref
        existing["source_refs"] = list(refs.values())
    return list(deduped.values())


def _source_paths(item: dict[str, Any]) -> list[str]:
    """提取公用工程条目的全部溯源路径，供去重兜底使用。"""
    return [
        str(ref.get("path"))
        for ref in item.get("source_refs") or []
        if isinstance(ref, dict) and ref.get("path")
    ]


def _utility_identity(source_path: str) -> str:
    """生成稳定负荷点 identity；不同子系统路径不互相合并。"""
    value = str(source_path or "").strip().lstrip(".")
    if not value:
        return ""
    # 把下标统一成 []，忽略元素顺序带来的路径差异
    value = re.sub(r"\[\d+\]", "[]", value)
    # 去掉末尾的 selected_scheme 标记，避免同一负荷点因标记不同而分裂
    value = re.sub(r"\s*\+\s*selected_scheme\s*$", "", value)
    # 统一等价路径写法，保证同一 utility 位置只产生一个 identity
    value = value.replace(
        ".selected_parameters.operating_conditions.utility",
        ".utility",
    )
    return value


def _utility_annual_quantity(
    item: dict[str, Any],
    medium_code: str,
    hours: Any,
) -> tuple[float | None, str | None, str | None]:
    # 优先级 1：来源已给出显式年量，直接采用
    annual = _first_value(item, ("annual_quantity", "annual_consumption", "quantity"))
    unit = _first_value(item, ("annual_unit", "unit", "units"))
    if annual is not None and unit not in (None, ""):
        return _to_float(annual), _canonical_unit(str(unit)), "annual_quantity from equipment data"
    # 优先级 2：有质量流量，按运行时折算为 t/a
    mass_flow = _first_value(item, ("mass_flow_kg_h", "utility_mass_flow_kg_h"))
    value = _annualize(mass_flow, hours)
    if value is not None:
        return value, "t/a", "mass_flow_kg_h * annual_operating_hours / 1000"
    # 优先级 3：有功率，按运行时折算为 kWh/a
    power = _first_value(item, ("power_kw", "power_k_w", "electric_power_kw"))
    if power is not None and hours not in (None, ""):
        try:
            return (
                round(float(power) * float(hours), 6),
                "kWh/a",
                "power_kw * annual_operating_hours",
            )
        except (TypeError, ValueError):
            return None, None, None
    # 优先级 4：电力介质还有显式年用电量兜底
    if medium_code == "electricity":
        electricity = _first_value(item, ("electricity_kwh_a", "annual_power_kwh"))
        if electricity is not None:
            return _to_float(electricity), "kWh/a", "annual electricity from equipment data"
    return None, None, None


def _build_energy_conversion(
    utility_summary: dict[str, Any],
    conversion_type: str,
) -> dict[str, Any]:
    rules = _load_energy_rules()
    # 取当前折算类型对应的规则块，缺失时回退到标准煤
    block = rules.get(conversion_type) or rules.get("standard_coal") or {}
    rule_version = (rules.get("metadata") or {}).get("version")
    # 标准油用 toe，标准煤用 tce，字段名与系数键随之切换
    per_key = "toe_per_input_unit" if conversion_type == "standard_oil" else "tce_per_input_unit"
    result_key = "standard_oil_toe" if conversion_type == "standard_oil" else "standard_coal_tce"
    total_key = (
        "total_standard_oil_toe"
        if conversion_type == "standard_oil"
        else "total_standard_coal_tce"
    )
    items = []
    totals_by_condition: dict[str, dict[str, Any]] = {}
    for utility in utility_summary.get("items") or []:
        converted = _convert_utility_item(
            utility,
            rules,
            block,
            conversion_type,
            per_key,
            result_key,
            rule_version,
        )
        if converted.get("status") == "calculated":
            # 只把折算成功的项计入分工况汇总
            condition = str(converted.get("condition") or "unspecified")
            bucket = totals_by_condition.setdefault(
                condition,
                {
                    "condition": condition,
                    "item_count": 0,
                    total_key: 0.0,
                },
            )
            bucket["item_count"] += 1
            bucket[total_key] = round(
                float(bucket[total_key]) + float(converted.get(result_key) or 0),
                6,
            )
        items.append(converted)
    result = {
        "status": _conversion_status(items),
        "conversion_type": conversion_type,
        "standard": block.get("standard"),
        "total_unit": block.get("total_unit"),
        "items": items,
    }
    if totals_by_condition:
        result["totals_by_condition"] = list(totals_by_condition.values())
    # 只有一个工况时，把合计值提升到顶层便于直接取用
    if len(totals_by_condition) == 1:
        result[total_key] = next(iter(totals_by_condition.values()))[total_key]
    return _clean(result)


def _convert_utility_item(
    utility: dict[str, Any],
    rules: dict[str, Any],
    block: dict[str, Any],
    conversion_type: str,
    per_key: str,
    result_key: str,
    rule_version: Any,
) -> dict[str, Any]:
    annual = utility.get("annual_quantity") or {}
    if not annual:
        return _conversion_blocked(utility, "physical_quantity_missing")

    steam_block = (rules.get(conversion_type) or {}).get("steam")
    rule = None
    steam_grade = None
    if utility.get("medium_code") == "steam" and steam_block and conversion_type == "standard_oil":
        # 标准油下的蒸汽需按压力/等级匹配专用系数
        rule = _match_steam_rule(steam_block, utility)
        if rule is None:
            return _conversion_blocked(utility, "steam_pressure_or_grade_missing")
        steam_grade = rule.get("pressure_grade") or rule.get("label")
    else:
        # 普通介质先按介质编码查规则，失败再按介质名兜底
        rule = _energy_rule_index(rules, conversion_type).get(
            _normalize_rule_key(utility.get("medium_code"))
        )
        if rule is None:
            rule = _energy_rule_index(rules, conversion_type).get(
                _normalize_rule_key(utility.get("medium_name"))
            )
    if rule is None:
        return _conversion_blocked(utility, "conversion_factor_missing")

    factor = rule.get(per_key)
    rule_unit = rule.get("input_unit") or (steam_block or {}).get("input_unit")
    value = annual.get("value")
    unit = annual.get("unit")
    if value is None or factor is None:
        return _conversion_blocked(utility, "quantity_or_factor_missing", rule)
    # 把年量单位对齐到规则要求的输入单位，得到缩放系数
    scale, canonical_unit = _harmonize_annual_unit(unit, rule_unit)
    if scale is None:
        blocked = _conversion_blocked(utility, "unit_mismatch", rule)
        blocked["input_unit"] = unit
        blocked["expected_unit"] = rule_unit
        return blocked
    converted_value = round(float(value) * scale * float(factor), 6)
    return _clean(
        {
            "status": "calculated",
            "medium": utility.get("medium"),
            "medium_code": utility.get("medium_code"),
            "medium_name": rule.get("energy_name") or utility.get("medium_name"),
            "condition": utility.get("condition"),
            "equipment_id": utility.get("equipment_id"),
            "equipment_name": utility.get("equipment_name"),
            "quantity": value,
            "unit": canonical_unit,
            "rule_id": rule.get("rule_id"),
            "coefficient": rule.get("coefficient"),
            "coefficient_unit": rule.get("coefficient_unit"),
            "steam_grade": steam_grade,
            result_key: converted_value,
            "source": _rule_source(rule, block, steam_block, rule_version),
            "source_refs": utility.get("source_refs"),
        }
    )


def _conversion_blocked(
    utility: dict[str, Any],
    reason: str,
    rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _clean(
        {
            "status": "blocked",
            "reason": reason,
            "medium": utility.get("medium"),
            "medium_code": utility.get("medium_code"),
            "medium_name": utility.get("medium_name"),
            "condition": utility.get("condition"),
            "equipment_id": utility.get("equipment_id"),
            "equipment_name": utility.get("equipment_name"),
            "quantity": (utility.get("annual_quantity") or {}).get("value"),
            "unit": (utility.get("annual_quantity") or {}).get("unit"),
            "rule_id": (rule or {}).get("rule_id"),
            "source_refs": utility.get("source_refs"),
        }
    )


def _conversion_status(items: list[dict[str, Any]]) -> str:
    if not items:
        return "blocked"
    if all(item.get("status") == "calculated" for item in items):
        return "calculated"
    if any(item.get("status") == "calculated" for item in items):
        return "partial"
    return "blocked"


def _load_energy_rules(path: Path | None = None) -> dict[str, Any]:
    rules_path = path or ENERGY_RULES_PATH
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    if not isinstance(rules, dict):
        raise ValueError("energy conversion rules must be a JSON object")
    return rules


def _select_energy_conversion_type(facts_hint: dict[str, Any]) -> str:
    """从上游 hints 中探测折算类型，命中标准油相关关键字则用标准油，否则默认标准煤。"""
    for value in _explicit_energy_standard_values(facts_hint):
        normalized = _normalize_rule_key(value)
        if normalized in {"standard_oil", "标准油", "折标准油", "油标", "toe"}:
            return "standard_oil"
    return "standard_coal"


def _explicit_energy_standard_values(value: Any) -> list[Any]:
    """递归收集上游中显式标注能源核算标准的字段值。"""
    field_names = {
        "energy_accounting_standard",
        "accounting_standard",
        "conversion_type",
        "energy_conversion_type",
        "standard",
    }
    values = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = _snake(str(key))
            # 命中核算标准字段时取值，否则继续向下递归
            if key_text in field_names:
                values.append(child)
            elif isinstance(child, (dict, list)):
                values.extend(_explicit_energy_standard_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_explicit_energy_standard_values(child))
    return values


def _energy_rule_index(rules: dict[str, Any], conversion_type: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rule in ((rules.get(conversion_type) or {}).get("exact") or []):
        for name in [rule.get("energy_code"), rule.get("energy_name"), *(rule.get("aliases") or [])]:
            if name:
                out[_normalize_rule_key(name)] = rule
    return out


def _rule_source(
    rule: dict[str, Any],
    block: dict[str, Any],
    steam_block: dict[str, Any] | None = None,
    rule_version: Any = None,
) -> dict[str, Any]:
    source = rule.get("source") or {}
    fallback = (steam_block or {}).get("source") or {}
    return _clean(
        {
            "standard": source.get("standard") or fallback.get("standard") or block.get("standard"),
            "clause": source.get("clause") or fallback.get("clause"),
            "note": source.get("note") or fallback.get("note"),
            "rule_version": rule_version,
        }
    )


def _match_steam_rule(steam_block: dict[str, Any], utility: dict[str, Any]) -> dict[str, Any] | None:
    input_data = utility.get("input") or {}
    name = (
        input_data.get("pressure_grade")
        or input_data.get("steam_grade")
        or utility.get("medium")
        or utility.get("medium_name")
    )
    norm_name = _normalize_rule_key(name)
    # 先按压力等级/蒸汽等级/介质名精确匹配
    for rule in steam_block.get("rules") or []:
        labels = [rule.get("label"), rule.get("pressure_grade")]
        if norm_name in {_normalize_rule_key(label) for label in labels if label}:
            return rule
    # 等级名匹配不到时，退回压力数值落在哪个区间
    pressure = input_data.get("pressure_mpa")
    if pressure in (None, ""):
        return None
    try:
        pressure_value = float(re.search(r"\d+(?:\.\d+)?", str(pressure)).group(0))
    except (AttributeError, ValueError):
        return None
    for rule in steam_block.get("rules") or []:
        low = rule.get("min")
        high = rule.get("max")
        low_value = None if low in (None, "") else float(low)
        high_value = None if high in (None, "", "+inf", "inf") else float(high)
        if low_value is not None:
            if pressure_value < low_value if rule.get("min_inclusive", True) else pressure_value <= low_value:
                continue
        if high_value is not None:
            if pressure_value > high_value if rule.get("max_inclusive", True) else pressure_value >= high_value:
                continue
        return rule
    return None


def _harmonize_annual_unit(input_unit: Any, rule_unit: Any) -> tuple[float | None, str | None]:
    unit = _canonical_unit(str(input_unit or ""))
    expected = _canonical_unit(str(rule_unit or ""))
    # 折算系数按“每单位物量”定义，年量单位可先去掉 /a 后缀再比较
    if unit.endswith("/a"):
        unit = unit[:-2]
    if expected.endswith("/a"):
        expected = expected[:-2]
    if unit == expected:
        return 1.0, expected
    # 质量单位 kg/t 之间的换算
    if unit == "kg" and expected == "t":
        return 0.001, expected
    if unit == "t" and expected == "kg":
        return 1000.0, expected
    return None, expected or unit


def _canonical_unit(unit: str) -> str:
    key = _normalize_rule_key(unit)
    # 常见单位别名字典，统一到规范写法
    aliases = {
        "kwh": "kWh",
        "kwha": "kWh/a",
        "kwh/a": "kWh/a",
        "千瓦时": "kWh",
        "度": "kWh",
        "t": "t",
        "ta": "t/a",
        "t/a": "t/a",
        "吨": "t",
        "吨年": "t/a",
        "吨/年": "t/a",
        "kg": "kg",
        "kga": "kg/a",
        "kg/a": "kg/a",
        "公斤": "kg",
    }
    return aliases.get(key, unit)


def _normalize_utility_medium(value: Any) -> tuple[str, str]:
    raw = str(value or "").strip()
    key = _normalize_rule_key(raw)
    # 介质别名映射到规范编码与中文名
    aliases = {
        "ho": ("heat_oil", "导热油"),
        "热油": ("heat_oil", "导热油"),
        "导热油": ("heat_oil", "导热油"),
        "cw": ("cooling_water", "循环冷却水"),
        "循环水": ("cooling_water", "循环冷却水"),
        "冷却水": ("cooling_water", "循环冷却水"),
        "循环冷却水": ("cooling_water", "循环冷却水"),
        "electricity": ("electricity", "电力"),
        "power": ("electricity", "电力"),
        "电": ("electricity", "电力"),
        "电力": ("electricity", "电力"),
        "steam": ("steam", "蒸汽"),
        "蒸汽": ("steam", "蒸汽"),
        "热负荷线索": ("duty", "热负荷线索"),
    }
    return aliases.get(key, (key or raw, raw or "待补充"))


def _normalize_rule_key(value: Any) -> str:
    """把名称/单位等归一化为小写去空格的匹配键。"""
    return str(value or "").strip().lower().replace(" ", "")


def _condition_from_path(path: str) -> str:
    """从来源路径判断工况，识别不出时默认改造工况。"""
    return _explicit_condition_from_path(path) or "retrofit"


def _explicit_condition_from_path(path: str) -> str | None:
    """从来源路径中显式识别 design/retrofit 工况，识别不出返回 None。"""
    segments = re.split(r"[.\[\]]+", str(path or ""))
    if "design" in segments or any(segment.startswith("design_") for segment in segments):
        return "design"
    if "retrofit" in segments or any(segment.startswith("retrofit_") for segment in segments):
        return "retrofit"
    return None


def _source_id_from_equipment_path(path: str) -> str | None:
    """根据 equipment 内路径前缀反推该公用工程所属的来源角色。"""
    normalized = path.lstrip(".")
    if normalized.startswith("reactor."):
        return "reactor_result"
    if normalized.startswith("tower."):
        return "tower_result"
    if normalized.startswith("new_device_parameters."):
        return "new_device_params"
    if normalized.startswith("object_catalog"):
        return "retrofit_equipment"
    return None


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """按给定键顺序返回第一个非空值，都没有则返回 None。"""
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
    return None


def _to_float(value: Any) -> float | None:
    """把值安全转为保留 6 位小数的浮点数，失败返回 None。"""
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _warn_name_conflicts(
    equipment: dict[str, Any],
    process: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> None:
    """检查同一设备 id 在不同来源是否名称不一致，冲突仅作 warning 诊断。"""
    names_by_id: dict[str, set[str]] = {}

    def add(item: dict[str, Any]) -> None:
        equipment_id = item.get("id") or item.get("equipment_id")
        name = item.get("name") or item.get("tag")
        if equipment_id not in (None, "") and name not in (None, ""):
            names_by_id.setdefault(str(equipment_id), set()).add(str(name))

    for item in equipment.get("object_catalog") or []:
        if isinstance(item, dict):
            add(item)
    for condition in ("design", "retrofit"):
        case = process.get(condition) or {}
        for bucket in ("separators", "reactors"):
            for item in case.get(bucket) or []:
                if isinstance(item, dict):
                    add(item)
    for equipment_id, names in sorted(names_by_id.items()):
        if len(names) > 1:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "EQUIPMENT_NAME_CONFLICT",
                    f"equipment id {equipment_id} has inconsistent names: {sorted(names)}",
                )
            )


def _to_snake(value: Any) -> Any:
    """递归把键转为 snake_case，并剔除禁止键与 LLM 相关字段。"""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key) in FORBIDDEN_KEYS:
                continue
            if "llm" in str(key).lower():
                continue
            out[_snake(str(key))] = _to_snake(item)
        return out
    if isinstance(value, list):
        return [_to_snake(item) for item in value]
    return value


def _snake(key: str) -> str:
    """把单个 camelCase 或 kebab-case 键转为 snake_case。"""
    key = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", key)
    key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return key.replace("-", "_").lower()


def _clean(value: Any, *, keep_top: bool = False) -> Any:
    """递归清洗：剔除禁止键与 LLM 字段，并清空 None/空值。

    keep_top=True 时只保留 TOP_LEVEL_KEYS 的顶层字段，用于最终产物定型。
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key) in FORBIDDEN_KEYS or "llm" in str(key).lower():
                continue
            cleaned = _clean(item)
            if keep_top and key in TOP_LEVEL_KEYS:
                out[key] = cleaned if cleaned is not None else ({} if key != "sources" else [])
            elif cleaned not in (None, {}, []):
                out[key] = cleaned
        return out
    if isinstance(value, list):
        return [item for item in (_clean(item) for item in value) if item not in (None, {}, [])]
    return value


def _write_artifact(facts: dict[str, Any], host_client: HostClient) -> dict[str, str]:
    """把工程事实 JSON 保存到 HostClient，返回 path/schema/媒体类型。"""
    path = f"{new_logical_prefix('engineering_facts')}/engineering_facts.json"
    try:
        host_client.save_file(path, facts)
    except Exception as exc:  # noqa: BLE001 - storage adapter boundary
        raise HostStorageError(
            f"HostClient failed to save engineering facts artifact: {path}. Cause: {exc}",
            code="ENGINEERING_FACTS_ARTIFACT_SAVE_FAILED",
        ) from exc
    return {
        "path": path,
        "schema_version": SCHEMA_VERSION,
        "media_type": MEDIA_TYPE,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """原子写 JSON，避免长任务中断留下半截产物。"""
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _relative_location(path: Path, source_root: Path) -> str:
    try:
        return path.resolve().relative_to(source_root).as_posix()
    except ValueError:
        return str(path)


def _summary(facts: dict[str, Any]) -> dict[str, Any]:
    """从产物中汇总少量计数，作为 Tool 返回的 summary 小对象。"""
    scheme = facts.get("scheme_analysis") or {}
    adopted = facts.get("adopted_scheme") or {}
    derived = facts.get("derived_facts") or {}
    equipment = facts.get("equipment") or {}
    equipment_count = len(equipment.get("object_catalog") or [])
    derived_count = sum(
        len(value) for value in derived.values() if isinstance(value, list)
    )
    return {
        "source_count": len(facts.get("sources") or []),
        "equipment_count": equipment_count,
        "candidate_count": len(scheme.get("candidates") or []),
        "user_candidate_count": len(scheme.get("user_candidates") or []),
        "adopted_scheme_count": len(adopted.get("schemes") or []),
        "selected_names": adopted.get("selected_names") or [],
        "optimized": adopted.get("optimized"),
        "derived_fact_count": derived_count,
        "annualized_stream_quantity_count": len(derived.get("annualized_stream_quantities") or []),
        "annualized_material_consumption_count": len(
            derived.get("annualized_material_consumption") or []
        ),
    }
