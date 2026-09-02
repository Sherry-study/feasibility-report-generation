"""Content 鸭子类型协议。

为业务核心逻辑提供进度推送能力的抽象接口，使工作流与宿主环境
（MCP Server / CLI entry）解耦。

- :class:`Content`：工作流 ``content`` 参数的鸭子类型，描述标准 progress
  通知与携带 ``uiEvent`` 扩展字段的 progress 通知两类方法。

工作流通过 ``content`` 参数推送进度与中间结果；宿主侧负责将调用桥接到
实际的通信通道（MCP session / 日志等）。

实现示例：
- ``mcp_server.duck_implement.MCPContent``：MCP 服务实现，通过
  :func:`asyncio.run_coroutine_threadsafe` 把同步调用桥接到主事件循环上
  的 fastmcp ``Context``。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class Content(Protocol):
    """工作流 ``content`` 参数的鸭子类型。

    方法约定：
        - ``report_progress``: 发送标准 progress 通知（progress/total/message）。
        - ``send_progress_with_data``: 发送携带 ``uiEvent`` 扩展字段的 progress
          通知，``ui_event`` dict 的内容原样作为 ``uiEvent`` 的值传给前端。

    推送是 best-effort：异常不应阻塞工作流，实现内部应捕获并记录日志。
    """

    def report_progress(
        self,
        progress: float,
        total: Optional[float] = None,
        message: Optional[str] = None,
    ) -> None:
        """发送标准 progress 通知。"""
        ...

    def send_progress_with_data(
        self,
        progress: float,
        total: Optional[float] = None,
        message: Optional[str] = None,
        ui_event: Optional[dict[str, Any]] = None,
    ) -> None:
        """发送携带 ``uiEvent`` 扩展字段的 progress 通知。"""
        ...
