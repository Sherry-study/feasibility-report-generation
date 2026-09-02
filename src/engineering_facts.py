"""阶段①工程事实：核心逻辑（同步，依赖 ``duck.content.Content`` 推送进度）。

来源登记（Source Registry）-> 项目 Profile 解析 -> 已解析输入清单 -> 用户
输入解析 -> 事实构建。等价于 ``tools/engineering_facts/run.py`` 的进程内
编排；进度经 ``content`` 桥接。
"""

from __future__ import annotations

from argparse import Namespace
from typing import Any, Optional

from duck.content import Content
from internal.facts.stage import run_engineering_facts_stage


class EngineeringFactsWorkflow:
    """阶段①工作流：整理并构建工程事实（同步实现）。"""

    def __init__(self, content: Content) -> None:
        self._content = content

    def run(
        self,
        *,
        output_dir: str = "report_output_dir",
        workspace: Optional[str] = None,
        source_manifest: Optional[str] = None,
        profile: Optional[str] = None,
        project_name: Optional[str] = None,
        project_id: Optional[str] = None,
        project_level: str = "unit",
        project_type: str = "mixed",
        user_inputs: Optional[str] = None,
        construction_unit: Optional[str] = None,
        annual_operating_hours: Optional[float] = None,
        project_location: Optional[str] = None,
        implementation_schedule: Optional[str] = None,
    ) -> dict[str, Any]:
        """运行阶段①。

        Args:
            output_dir: 可选，默认 ``report_output_dir``，产物写入该目录。
            workspace: 算法输出工作区目录；与 source_manifest 二选一。
            source_manifest: 来源清单文件；与 workspace 二选一。
            profile: 可选显式覆盖的项目画像文件。
            project_name / project_id / project_level / project_type: 来源登记元信息。
            user_inputs / construction_unit / annual_operating_hours /
                project_location: 用户输入与 CLI 覆盖。
            implementation_schedule: 可选，宿主提供的实施进度估算 JSON 文件。

        Returns:
            含 ``exit_code`` 与阶段摘要字段的字典（0=facts_ready，2=needs_resolution）。
        """
        if not workspace and not source_manifest:
            raise ValueError("至少提供 workspace 或 source_manifest")

        self._content.report_progress(progress=1, total=4, message="来源登记中")
        args = Namespace(
            workspace=workspace,
            source_manifest=source_manifest,
            profile=profile,
            project_name=project_name,
            project_id=project_id,
            project_level=project_level,
            project_type=project_type,
            user_inputs=user_inputs,
            construction_unit=construction_unit,
            annual_operating_hours=annual_operating_hours,
            project_location=project_location,
            implementation_schedule=implementation_schedule,
        )
        self._content.report_progress(progress=2, total=4, message="解析项目 Profile 与用户输入")
        code, summary = run_engineering_facts_stage(args, output_dir)
        self._content.report_progress(progress=4, total=4, message="工程事实构建完成")
        return {"exit_code": code, **summary}
