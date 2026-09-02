"""HostClient 鸭子类型协议。

为业务核心逻辑提供与宿主环境（MCP Server / FastAPI / CLI entry 等）交互的
抽象接口，使工作流与宿主存储/通信层解耦。

- :class:`HostClient`：文件读写，工作流通过它读取输入文件、保存最终结果。
"""

from __future__ import annotations

from typing import Union, Protocol, runtime_checkable


@runtime_checkable
class HostClient(Protocol):
    """宿主客户端鸭子类型。

    提供文件读写能力，使工作流能与宿主存储层解耦。
    """

    def save_file(self, path: str, data: Union[dict, bytes, str]) -> None:
        """将数据保存到宿主存储。

        Args:
            path: 宿主存储中的文件路径。
            data: dict 序列化为 JSON；str 写入文本；bytes 直接写入。
        """
        ...

    def get_file(self, path: str) -> Union[dict, bytes, str]:
        """从宿主存储读取文件。

        Returns:
            dict（JSON 文件）、str（文本）或 bytes（二进制文件）。
        """
        ...
