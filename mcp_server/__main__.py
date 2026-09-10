"""``python -m mcp_server`` 入口。"""

from __future__ import annotations

from mcp_server.server import main, setup_logging


if __name__ == "__main__":
    setup_logging()
    main()
