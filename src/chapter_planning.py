"""阶段③章节级研究路由：核心逻辑（同步，依赖 ``duck.content.Content`` 推送进度）。

基于确认后事实与 Profile 生成章节计划 -> 缺口分析 -> 编制信息一次性提问 ->
生成外部研究任务 -> 宿主代理路由。等价于 ``tools/chapter_planning/run.py``
的进程内编排。
"""

from __future__ import annotations

import os
from argparse import Namespace
from typing import Any, Optional

from duck.content import Content
from internal.planning.stage import run_chapter_planning_stage


class ChapterPlanningWorkflow:
    """阶段③工作流：章节规划与研究/LLM 路由（同步实现）。"""

    def __init__(self, content: Content) -> None:
        self._content = content

    def run(
        self,
        *,
        facts: str,
        profile: str,
        output_dir: str = "report_output_dir",
        research_evidence: Optional[str] = None,
        section_drafts: Optional[str] = None,
        ai_mode: str = "host_agent",
        run_mode: str = "production",
        skip_user_inputs: bool = False,
    ) -> dict[str, Any]:
        """运行阶段③。

        Args:
            facts: 必填，``confirmed_project_facts.json`` 路径。
            profile: 必填，项目画像文件路径。
            output_dir: 可选，默认 ``report_output_dir``。
            research_evidence / section_drafts: 宿主提供的外部证据与章节草稿。
            ai_mode: ``host_agent``（默认）/ ``disabled``。
            run_mode: ``production``（默认）/ ``test``（要求环境变量
                ``FEASIBILITY_SKILL_DEV_TEST=1``）。
            skip_user_inputs: 跳过编制信息一次性提问（deferred_questions），保留缺口继续。

        Returns:
            含 ``exit_code`` 与阶段摘要字段的字典
            （0=planning_ready，9=needs_user_input，10=needs_research，11=needs_llm）。
        """
        if run_mode == "test" and os.environ.get("FEASIBILITY_SKILL_DEV_TEST") != "1":
            raise ValueError("test mode requires FEASIBILITY_SKILL_DEV_TEST=1")

        self._content.report_progress(progress=1, total=4, message="生成章节计划与缺口分析")
        args = Namespace(
            facts=facts,
            profile=profile,
            research_evidence=research_evidence,
            section_drafts=section_drafts,
            ai_mode=ai_mode,
            run_mode=run_mode,
            skip_user_inputs=skip_user_inputs,
        )
        code, summary = run_chapter_planning_stage(args, output_dir)
        self._content.report_progress(progress=4, total=4, message="章节规划完成")
        return {"exit_code": code, **summary}
