"""本地手动运行报告准备 + 定稿算法。

只需要修改工程事实文件路径，然后执行：

    python -B -X utf8 scripts/run_report_generation.py

默认先 prepare 生成 work_package.json，再不携带 Agent 工作结果直接 finalize，
导出一份以模板 fallback 兜底的不完整初稿 Markdown 与 DOCX，用于本地验证完整链路。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from scripts.local_adapters import ConsoleContent, LocalHostClient  # noqa: E402
from src.report_finalize import execute as execute_finalize  # noqa: E402
from src.report_prepare import execute as execute_prepare  # noqa: E402

# 1. 工程事实文件路径：通常由工程事实整理 Tool 生成的 engineering_facts.json。
ENGINEERING_FACTS_LOCATION = str(TOOL_ROOT / "report_output_dir" / "engineering_facts.json")

# 2. 可选报告项目名称：为空时按“建设单位 + 装置名称 + 技术改造项目”推导。
PROJECT_NAME = ""
LOCAL_HOST_ROOT = TOOL_ROOT / "report_output_dir" / "local_host"


def main() -> int:
    """执行 prepare + finalize，并打印 Tool 返回结果。"""
    facts_path = Path(ENGINEERING_FACTS_LOCATION).resolve()
    host_client = LocalHostClient(LOCAL_HOST_ROOT)
    content = ConsoleContent()
    engineering_facts_path = "manual/engineering_facts.json"
    host_client.save_file(
        engineering_facts_path,
        json.loads(facts_path.read_text(encoding="utf-8")),
    )

    prepare_result = execute_prepare(
        {
            "engineering_facts_path": engineering_facts_path,
            "report_context": {"project_name": PROJECT_NAME} if PROJECT_NAME.strip() else {},
        },
        content=content,
        host_client=host_client,
    )
    print(json.dumps(prepare_result, ensure_ascii=False, indent=2))

    if prepare_result.get("status") != "prepared":
        return 1

    # 手动运行默认不接 Agent 结果：不传 work_results_path，
    # finalize 会使用空工作结果继续，对需 Agent 撰写的章节使用模板 fallback
    # 兜底，并返回 WORK_RESULTS_NOT_PROVIDED warning，从而验证导出链路。
    work_package_path = prepare_result["artifact"]["path"]

    finalize_result = execute_finalize(
        {
            "work_package_path": work_package_path,
        },
        content=content,
        host_client=host_client,
    )
    print()
    print(json.dumps(finalize_result, ensure_ascii=False, indent=2))

    if finalize_result.get("status") != "completed":
        return 1

    print()
    print(f"manifest_path: {finalize_result.get('manifest_path')}")
    for label, value in finalize_result.get("artifacts", {}).items():
        print(f"{label}: {value}")
    print(f"本地 Host 根目录：{LOCAL_HOST_ROOT}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
