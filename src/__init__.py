"""可研交付业务核心逻辑（同步实现，不依赖 MCP / asyncio）。

4 个阶段的核心逻辑对应 ``tools/`` 下 4 个 Tool 的进程内编排，重写为同步
工作流类，通过 ``duck.content.Content`` / ``duck.host_client.HostClient``
鸭子类型与宿主环境解耦，传入具体实现即可工作。
"""
