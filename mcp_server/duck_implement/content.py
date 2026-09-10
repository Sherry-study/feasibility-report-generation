"""MCPContent：``duck.content.Content`` 协议的 MCP 实现侧桥接。

- :class:`MCPContent`：实现 ``duck.content.Content`` 协议，通过
  :func:`asyncio.run_coroutine_threadsafe` 把同步调用桥接到主事件循环上
  的 fastmcp ``Context``。
- :func:`_send_progress_with_data`：构造携带 ``uiEvent`` 字段的
  ``ProgressNotificationParams``；host 未设置 progressToken 时回退到
  标准 ``ctx.report_progress``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastmcp import Context
from mcp.types import ProgressNotification, ProgressNotificationParams
from pydantic import ConfigDict

logger = logging.getLogger(__name__)


class _UiEventProgressParams(ProgressNotificationParams):
    """进度参数：mcp 2.x 的 params 模型默认丢弃多余字段，这里放行 ``uiEvent`` 扩展字段。"""

    model_config = ConfigDict(extra="allow")


class _UiEventProgressNotification(ProgressNotification):
    """进度通知：把 params 类型收窄为 ``_UiEventProgressParams``，保证序列化时保留 uiEvent。"""

    params: _UiEventProgressParams


async def _send_progress_with_data(
    ctx: Context,
    progress: float,
    total: Optional[float] = None,
    message: Optional[str] = None,
    ui_event: Optional[dict[str, Any]] = None,
) -> None:
    """发送携带 ``uiEvent`` 扩展字段的 progress 通知。

    标准 ``ctx.report_progress`` 只支持 progress/total/message，本函数通过
    直接构造携带 ``uiEvent`` 额外字段的 ``ProgressNotificationParams`` 发送
    一个结构化 ``uiEvent``，``ui_event`` dict 的内容会原样作为 ``uiEvent`` 的值
    传给前端，前端直接从 ``progress.uiEvent.<key>`` 读取逐步渲染所需数据。

    mcp 2.x 中 ``ServerNotification`` 是通知的联合类型别名（不可实例化），
    发送时直接传入具体的 ``ProgressNotification`` 实例，并用
    ``related_request_id`` 关联当前请求；``uiEvent`` 需借助放行额外字段的
    params 子类才能在序列化时保留。

    通过 fastmcp Context 的公开 API（``ctx.session`` property 与
    ``ctx.request_context`` 元数据）获取 session 与 progress token；
    若 host 未设置 progressToken 或发送失败，回退到标准 report_progress。
    """
    try:
        try:
            session = ctx.session
        except RuntimeError:
            session = None
        token = _extract_progress_token(ctx)
        if session is not None and token is not None:
            params: dict[str, Any] = {
                # 必须用 alias progressToken：ProgressNotificationParams 未开启
                # populate_by_name，传字段名 progress_token 会校验失败并回退，
                # 导致 uiEvent 被丢弃。
                "progressToken": token,
                "progress": progress,
            }
            if total is not None:
                params["total"] = total
            if message is not None:
                params["message"] = message
            if ui_event is not None:
                params["uiEvent"] = ui_event
            await session.send_notification(
                _UiEventProgressNotification(
                    params=_UiEventProgressParams(**params),
                ),
                related_request_id=ctx.request_id,
            )
            return
    except Exception as e:
        logger.debug("send_progress_with_data failed, fallback to standard: %s", e)
    await ctx.report_progress(progress, total, message)


def _extract_progress_token(ctx: Context) -> Optional[Any]:
    """从 fastmcp Context 元数据中提取 progress token。

    兼容两种形状：``ctx.request_context.meta`` 为对象（属性 ``progressToken`` /
    ``progress_token``）或为 dict（键 ``progress_token`` / ``progressToken``）。
    """
    request_ctx = getattr(ctx, "request_context", None)
    if request_ctx is None:
        return None
    meta = getattr(request_ctx, "meta", None)
    if meta is None:
        # fastmcp 4 的真实 Context 把 meta 挂在内部 ServerRequestContext 上
        srctx = getattr(request_ctx, "_srctx", None)
        meta = getattr(srctx, "meta", None) if srctx is not None else None
    if isinstance(meta, dict):
        return meta.get("progress_token") or meta.get("progressToken")
    if meta is not None:
        return getattr(meta, "progress_token", None) or getattr(meta, "progressToken", None)
    return None


class MCPContent:
    """工作流 ``content`` 参数的同步适配器。

    工作流在线程池中同步调用 ``report_progress``；本类通过
    :func:`asyncio.run_coroutine_threadsafe` 把调用桥接到主事件循环上，
    使进度真正推送给 MCP 客户端。进度推送是 best-effort，异常仅记日志。
    """

    def __init__(self, ctx: Context, loop: asyncio.AbstractEventLoop) -> None:
        self._ctx = ctx
        self._loop = loop

    def report_progress(
        self,
        progress: float,
        total: Optional[float] = None,
        message: Optional[str] = None,
    ) -> None:
        if not self._loop.is_running():
            logger.debug("report_progress skipped (loop not running): %s", message)
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._ctx.report_progress(progress, total, message),
            self._loop,
        )
        fut.add_done_callback(self._log_future_error)

    def send_progress_with_data(
        self,
        progress: float,
        total: Optional[float] = None,
        message: Optional[str] = None,
        ui_event: Optional[dict[str, Any]] = None,
    ) -> None:
        """发送携带 ``uiEvent`` 扩展字段的 progress 通知。

        若 host 未设置 progressToken，回退到标准 report_progress（ui_event 丢弃）。
        """
        if not self._loop.is_running():
            logger.debug("send_progress_with_data skipped (loop not running): %s", message)
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._send_progress_with_data_async(progress, total, message, ui_event),
            self._loop,
        )
        fut.add_done_callback(self._log_future_error)

    async def _send_progress_with_data_async(
        self,
        progress: float,
        total: Optional[float] = None,
        message: Optional[str] = None,
        ui_event: Optional[dict[str, Any]] = None,
    ) -> None:
        await _send_progress_with_data(self._ctx, progress, total, message, ui_event)

    @staticmethod
    def _log_future_error(fut: "asyncio.Future[Any]") -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            logger.warning("report_progress failed: %r", exc)
