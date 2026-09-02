"""阶段④报告生成：核心逻辑（同步，依赖 ``duck.content.Content`` / ``duck.host_client.HostClient``）。

工艺拓扑分析 -> 报告表格 -> 报告模型 -> 一致性门槛 -> DOCX/Markdown 导出 ->
报告溯源。等价于 ``tools/report_generation/run.py`` 的进程内编排；生成成功
后可选把最终报告推送到宿主存储。
"""

from __future__ import annotations

import logging
from argparse import Namespace
from pathlib import Path
from typing import Any, Optional

from duck.content import Content
from duck.host_client import HostClient
from internal.report.stage import run_report_generation_stage

logger = logging.getLogger(__name__)


class ReportGenerationWorkflow:
    """阶段④工作流：生成 DOCX/Markdown 报告（同步实现）。"""

    def __init__(self, content: Content, host_client: HostClient) -> None:
        self._content = content
        self._host = host_client

    def run(
        self,
        *,
        facts: str,
        profile: str,
        chapter_plan: str,
        output_dir: str = "report_output_dir",
        section_drafts: Optional[str] = None,
        strict_consistency: bool = False,
    ) -> dict[str, Any]:
        """运行阶段④。

        Args:
            facts: 必填，``confirmed_project_facts.json`` 路径。
            profile / chapter_plan: 必填。
            output_dir: 可选，默认 ``report_output_dir``。
            section_drafts: 可选，阶段③产物章节草稿。
            strict_consistency: 将上游高严重性事实冲突升级为阻断。

        Returns:
            含 ``exit_code`` 与阶段摘要字段的字典（0=generated，3=consistency_blocked）。
        """
        self._content.report_progress(progress=1, total=4, message="工艺拓扑分析与报告表格")
        args = Namespace(
            facts=facts,
            profile=profile,
            chapter_plan=chapter_plan,
            section_drafts=section_drafts,
            strict_consistency=strict_consistency,
        )
        code, summary = run_report_generation_stage(args, output_dir)
        if code == 0:
            self._push_report_to_host(summary)
            self._attach_markdown_content(summary)
        self._content.report_progress(progress=4, total=4, message="报告生成完成")
        return {"exit_code": code, **summary}

    def _attach_markdown_content(self, summary: dict[str, Any]) -> None:
        """把 Markdown 正文内联进结果，前端无需再按路径读文件即可回显报告。"""
        local = summary.get("markdown")
        if not local:
            return
        try:
            path = Path(local)
            if path.is_file():
                summary["markdown_content"] = path.read_text(encoding="utf-8")
                logger.info("已内联报告 Markdown 正文: %d 字符", len(summary["markdown_content"]))
        except Exception as exc:  # 读取失败不阻断报告生成
            logger.warning("读取报告 Markdown 正文失败: %r", exc)

    def _push_report_to_host(self, summary: dict[str, Any]) -> None:
        """把最终报告推送到宿主存储（best-effort，失败不阻塞报告生成）。"""
        for key, name in (("markdown", "可行性研究报告_初稿.md"), ("docx", "可行性研究报告_初稿.docx")):
            local = summary.get(key)
            if not local:
                continue
            try:
                path = Path(local)
                if not path.is_file():
                    continue
                if path.suffix.lower() == ".docx":
                    self._host.save_file(f"{name}", path.read_bytes())
                else:
                    self._host.save_file(f"{name}", path.read_text(encoding="utf-8"))
            except Exception as exc:  # 宿主存储不可达时仅记录，不阻断
                logger.warning("推送报告 %s 到宿主存储失败: %r", name, exc)
