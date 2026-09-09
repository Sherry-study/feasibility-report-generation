"""本地手动运行工程事实整理算法。

只需要修改上游算法输出目录，然后执行：

    python -B -X utf8 scripts/run_engineering_facts.py
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


def main() -> int:
    """执行算法并打印 Tool 返回结果。"""
    result = execute(
        {
            "source_location": {
                "provider": "local_directory",
                "location": SOURCE_LOCATION,
            },
            "construction_unit": CONSTRUCTION_UNIT,
        },
        content=ConsoleContent(),
        host_client=LocalHostClient(LOCAL_HOST_ROOT),
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
