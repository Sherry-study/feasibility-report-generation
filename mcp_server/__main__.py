"""``python -m mcp_server`` 入口。"""

from __future__ import annotations

import logging

from mcp_server.server import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
