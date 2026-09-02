"""``duck`` 协议的 MCP 实现侧：``Content`` 与 ``HostClient``。

实现来自 ``.trae/skills/build_mcp_server/business-mcp-demo/mcp_server/duck_implement``。
"""

from .content import MCPContent, _send_progress_with_data
from .host_client import MCPHostClient, make_host_client

__all__ = ["MCPContent", "MCPHostClient", "make_host_client", "_send_progress_with_data"]
