"""本地手动运行工程事实整理算法。

只需要修改上游算法输出目录，然后执行：

    python -B -X utf8 skills/feasibility-report-generation/scripts/run_engineering_facts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[1]
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from scripts.local_adapters import ConsoleContent, LocalHostClient
from src.engineering_facts import execute


# 1. 上游算法输出目录：目录内应包含 plant_info.json、plant_level.json、
#    scheme.json、retrofit_topology.json 等同一轮算法产物。
SOURCE_LOCATION = r"D:\0公司相关\Redesign\可研报告编写\算法输出-测试数据\装置级测试数据"
# SOURCE_LOCATION = r"D:\0公司相关\Redesign\可研报告编写\算法输出-测试数据\设备级测试数据"

# 2. 建设单位：可为空。
CONSTRUCTION_UNIT = ""
LOCAL_HOST_ROOT = TOOL_ROOT / "report_output_dir" / "local_host"

# 本地来源目录会在宿主逻辑路径 sources/ 下平铺；不使用标准文件名的
# 来源文件需在此登记（相对 root 的宿主逻辑路径）。
# 下方为"算法输出-测试数据/装置级测试数据"目录的默认布局。
FILE_OVERRIDES: dict[str, str] = {
    "reactor_result_path": "plant_reactor_result(2).json",
    "tower_result_path": "example_result_all_v3(1).json",
    "plant_result_path": "plant_level_result_v3.json",
    "retrofit_topology_path": "retrofit_topology(1).json",
}


def main() -> int:
    """执行算法并打印 Tool 返回结果。"""
    # 把本地来源目录上传到模拟宿主文件服务的 sources/ 前缀下
    host_client = LocalHostClient(LOCAL_HOST_ROOT)
    source_files = {
        f"sources/{path.name}": path.read_bytes()
        for path in Path(SOURCE_LOCATION).rglob("*")
        if path.is_file()
    }
    for logical_path, data in source_files.items():
        host_client.save_file(logical_path, data)

    result = execute(
        {
            "root": "sources",
            "file_overrides": FILE_OVERRIDES or None,
            "construction_unit": CONSTRUCTION_UNIT,
        },
        content=ConsoleContent(),
        host_client=host_client,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    artifact = result.get("artifact")
    if artifact:
        print()
        print(f"工程事实逻辑路径：{artifact['path']}")
        print(f"本地 Host 根目录：{LOCAL_HOST_ROOT}")

    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
