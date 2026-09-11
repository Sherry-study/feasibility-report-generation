"""MCPHostClient：``duck.host_client.HostClient`` 协议的 HTTP 实现。

单 URL 设计：所有请求统一打到 ``base_url`` 的普通文件服务接口
``{base_url}/files/{path}``（对齐装置级 server 已验证的保存方式）：

- ``save_file``:   POST   ``{base_url}/files/{path}``
- ``get_file``:    GET    ``{base_url}/files/{path}``

文件服务（如 ``mock_file_server``）支持 POST 创建文件、GET 读取文件。
不依赖平台工作区接口（``/internal/platform/workspace/files/`` 只读、不支持
创建新文件，实测 POST 返回 404）。``ctx`` 参数仅为兼容保留，不提取平台凭证。
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Union
from urllib.parse import quote

import httpx
from fastmcp import Context

logger = logging.getLogger(__name__)


class MCPHostError(RuntimeError):
    """MCPHostClient 请求失败的统一异常。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        path: Optional[str] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.code = code


class MCPHostClient:
    """``duck.host_client.HostClient`` 协议的 HTTP 实现。"""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8200",
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
        ctx: Optional[Context] = None,  # noqa: ARG002 - 兼容保留，不提取平台凭证
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MCPHostClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return f"{self._base_url}/files/{quote(path.lstrip('/'), safe='/')}"

    def _raise_for_status(self, resp: httpx.Response, path: Optional[str], op: str) -> None:
        if resp.status_code < 400:
            return
        code: Optional[str] = None
        message = resp.text
        try:
            data = resp.json()
            if isinstance(data, dict):
                code = data.get("code")
                message = data.get("error", message)
        except (ValueError, httpx.DecodingError):
            pass
        if resp.status_code == 404:
            detail = ""
            try:
                data = resp.json()
                if isinstance(data, dict):
                    code = data.get("code")
                    message = data.get("error") or data.get("message")
                    if code or message:
                        detail = f" (platform code={code}, message={message})"
            except (ValueError, httpx.DecodingError):
                pass
            logger.error(
                "MCPHostClient 404: op=%s path=%s url=%s status=%d body=%s",
                op,
                path,
                resp.url,
                resp.status_code,
                resp.text[:500] if resp.text else "",
            )
            raise FileNotFoundError(f"MCPHostClient.{op}: {path or resp.url} 不存在{detail}")
        raise MCPHostError(
            f"MCPHostClient.{op} failed: {resp.status_code} {message}",
            status_code=resp.status_code,
            path=path,
            code=code,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[dict] = None,
        json: Optional[dict] = None,
        content: Optional[bytes] = None,
        url: Optional[str] = None,
    ) -> httpx.Response:
        """Retry transient transport failures for idempotent workspace I/O."""
        if url is None:
            url = self._url(path)
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    content=content,
                )
            except (httpx.RequestError, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt >= 2:
                    break
                time.sleep(0.25 * (2**attempt))
                logger.warning(
                    "MCPHostClient.%s 连接异常 (第 %d 次重试) path=%s err=%r",
                    method,
                    attempt + 1,
                    path,
                    exc,
                )
        if last_exc is None:  # pragma: no cover - defensive guard
            raise RuntimeError("MCPHostClient request failed without an exception")
        raise last_exc

    def save_file(
        self,
        path: str,
        data: Union[dict, str, bytes],
        *,
        kind: str = "auto",
    ) -> None:
        method = "POST"
        write_url = self._url(path)

        if kind == "auto":
            if isinstance(data, (bytes, bytearray)):
                kind = "bytes"
            elif isinstance(data, dict):
                kind = "json"
            elif isinstance(data, str):
                kind = "text"
            else:
                raise TypeError(
                    f"save_file 不支持的数据类型: {type(data).__name__}，可选 dict / str / bytes"
                )

        if kind == "json":
            if not isinstance(data, dict):
                raise TypeError(f"kind='json' 需要 dict，得到 {type(data).__name__}")
            resp = self._request(
                method,
                path,
                json=data,
                headers={"Content-Type": "application/json"},
                url=write_url,
            )
        elif kind == "text":
            if isinstance(data, str):
                payload = data.encode("utf-8")
            elif isinstance(data, (bytes, bytearray)):
                payload = bytes(data)
            else:
                raise TypeError(f"kind='text' 需要 str 或 bytes，得到 {type(data).__name__}")
            resp = self._request(
                method,
                path,
                content=payload,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                url=write_url,
            )
        elif kind == "bytes":
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError(f"kind='bytes' 需要 bytes，得到 {type(data).__name__}")
            resp = self._request(method, path, content=bytes(data), url=write_url)
        else:
            raise ValueError(f"不支持的 kind: {kind}，可选值: json / text / bytes / auto")

        self._raise_for_status(resp, path, "save_file")
        logger.info("MCPHostClient 保存文件: %s (kind=%s)", write_url, kind)

    def get_file(
        self,
        path: str,
        *,
        kind: str = "auto",
    ) -> Union[dict, str, bytes]:
        url = self._url(path)
        resp = self._request("GET", path)
        if resp.status_code == 404:
            raise FileNotFoundError(f"MCPHostClient.get_file: {url} 不存在")
        self._raise_for_status(resp, path, "get_file")

        if kind == "auto":
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                kind = "json"
            elif "text/" in ctype:
                kind = "text"
            else:
                kind = "bytes"

        if kind == "json":
            try:
                data = resp.json()
            except (ValueError, httpx.DecodingError) as exc:
                raise MCPHostError(
                    f"get_file 响应不是有效的 JSON: {exc}",
                    status_code=resp.status_code,
                    path=path,
                ) from exc
            if not isinstance(data, dict):
                raise MCPHostError(
                    f"get_file 期望 JSON dict，实际得到 {type(data).__name__}",
                    status_code=resp.status_code,
                    path=path,
                )
            return data
        elif kind == "text":
            return resp.content.decode("utf-8")
        elif kind == "bytes":
            return resp.content
        else:
            raise ValueError(f"不支持的 kind: {kind}，可选值: json / text / bytes / auto")


_shared_http_client = httpx.Client(timeout=30.0)


def make_host_client(ctx: Context, base_url: str = "http://localhost:8200") -> MCPHostClient:
    """为每次 tool 调用创建 HostClient。

    对齐装置级本地模式：读写统一走 ``{base_url}/files/{path}``，不依赖平台
    工作区接口。``ctx`` 仅保留签名兼容，不提取平台凭证。``base_url`` 应指向
    支持 POST 创建文件的文件服务（如 ``mock_file_server``）。
    """
    return MCPHostClient(base_url=base_url, client=_shared_http_client, ctx=ctx)
