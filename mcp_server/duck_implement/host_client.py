"""MCPHostClient：``duck.host_client.HostClient`` 协议的 HTTP 实现。

单 URL 设计：所有请求统一打到 ``base_url``。
- 构造时从 ``ctx``（fastmcp ``Context``）提取平台工作区凭证（capability）。
  有平台凭证时走平台工作区接口
  ``{base_url}/internal/platform/workspace/files/{base64_path}``，带 Bearer 认证；
- 无平台上下文时走原文件服务接口 ``{base_url}/files/{path}``。

每次 tool 调用创建独立实例，构造时即从 ``ctx`` 捕获平台参数、不持有 ``ctx``，
因此调用者在线程池线程内也能安全使用本客户端。
"""

from __future__ import annotations

import base64
import logging
import time
import unicodedata
from typing import Optional, Union
from urllib.parse import quote

import httpx
from fastmcp import Context

logger = logging.getLogger(__name__)

PLATFORM_META_KEY = "io.industrial.platform"
WORKSPACE_CONTENT_TYPE = "application/vnd.industrial.platform-workspace-file"


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
        base_url: str = "http://10.30.70.120:8200",
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
        ctx: Optional[Context] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._platform_headers = self._extract_platform_headers(ctx)

    @staticmethod
    def _extract_platform_headers(ctx: Optional[Context]) -> Optional[dict[str, str]]:
        if ctx is None:
            return None
        request_context = getattr(ctx, "request_context", None)
        if request_context is None:
            return None
        meta = getattr(request_context, "meta", None)
        if meta is None:
            return None
        extras = getattr(meta, "model_extra", None)
        platform_meta = (extras or {}).get(PLATFORM_META_KEY)
        if not isinstance(platform_meta, dict):
            return None
        capability = platform_meta.get("capability")
        if not isinstance(capability, str) or not capability:
            return None
        return {"Authorization": f"Bearer {capability}"}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MCPHostClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _url(self, path: str) -> str:
        if self._platform_headers is not None:
            logical_path = unicodedata.normalize("NFC", path.replace("\\", "/"))
            encoded_path = base64.urlsafe_b64encode(logical_path.encode("utf-8"))
            encoded_path = encoded_path.rstrip(b"=").decode("ascii")
            return (
                f"{self._base_url}/internal/platform/workspace/files/"
                f"{encoded_path}"
            )
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
            raise FileNotFoundError(f"MCPHostClient.{op}: {path or resp.url} 不存在")
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
    ) -> httpx.Response:
        """Retry transient transport failures for idempotent workspace I/O."""
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
        platform = self._platform_headers is not None
        method = "PUT" if platform else "POST"
        headers = None
        if platform:
            headers = dict(self._platform_headers)
            headers["Content-Type"] = WORKSPACE_CONTENT_TYPE

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
                headers=headers or {"Content-Type": "application/json"},
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
                headers=headers or {"Content-Type": "text/plain; charset=utf-8"},
            )
        elif kind == "bytes":
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError(f"kind='bytes' 需要 bytes，得到 {type(data).__name__}")
            resp = self._request(method, path, content=bytes(data), headers=headers)
        else:
            raise ValueError(f"不支持的 kind: {kind}，可选值: json / text / bytes / auto")

        self._raise_for_status(resp, path, "save_file")
        logger.info("MCPHostClient 保存文件: %s (kind=%s)", self._url(path), kind)

    def get_file(
        self,
        path: str,
        *,
        kind: str = "auto",
    ) -> Union[dict, str, bytes]:
        url = self._url(path)
        resp = self._request("GET", path, headers=self._platform_headers)
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


def make_host_client(ctx: Context, base_url: str = "http://localhost:9000") -> MCPHostClient:
    """为每次 tool 调用创建 HostClient，构造时从 ctx 捕获平台凭证。"""
    return MCPHostClient(base_url=base_url, client=_shared_http_client, ctx=ctx)
