"""阶段②人工确认：核心逻辑（同步，依赖 ``duck.content.Content`` 推送进度）。

FA 年化计算并 merge 回写 facts -> 生成关键事实确认单（JSON + Markdown）->
人工确认门槛 -> 应用确认结果。等价于 ``tools/engineering_confirmation/run.py``
的进程内编排。
"""

from __future__ import annotations

from argparse import Namespace
from typing import Any, Optional

from duck.content import Content
from internal.confirmation import run_confirmation_stage


class EngineeringConfirmationWorkflow:
    """阶段②工作流：生成关键事实确认单并应用确认结果（同步实现）。"""

    def __init__(self, content: Content) -> None:
        self._content = content

    def run(
        self,
        *,
        facts: str,
        output_dir: str = "report_output_dir",
        confirm_as_is: bool = False,
        confirmation_response: Optional[str] = None,
    ) -> dict[str, Any]:
        """运行阶段②。

        Args:
            facts: 必填，阶段①产出的 ``project_facts.json`` 路径（会被年化 merge 回写）。
            output_dir: 可选，默认 ``report_output_dir``。
            confirm_as_is: 显式确认 report_confirmation.json 当前内容，不做修改。
            confirmation_response: 用户确认/修正后的确认响应 JSON/YAML。

        Returns:
            含 ``exit_code`` 与阶段摘要字段的字典（0=confirmed，12=needs_confirmation）。
        """
        self._content.report_progress(progress=1, total=3, message="年化计算并 merge 回写")
        args = Namespace(
            facts=facts,
            confirmation_response=confirmation_response,
            confirm_as_is=confirm_as_is,
        )
        code, summary = run_confirmation_stage(args, output_dir)
        self._content.report_progress(progress=3, total=3, message="确认阶段完成")
        return {"exit_code": code, **summary}
