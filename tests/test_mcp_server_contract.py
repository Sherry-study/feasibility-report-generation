"""MCP server public contract tests."""

from __future__ import annotations

import asyncio
import unittest

from mcp_server.server import mcp


def _run(awaitable):
    return asyncio.run(awaitable)


class MCPServerContractTests(unittest.TestCase):
    def test_public_tools_are_limited_to_two_tool_contract(self) -> None:
        tools = _run(mcp.list_tools())
        self.assertEqual(
            [tool.name for tool in tools],
            ["engineering_facts", "report_generation"],
        )

    def test_tools_are_bound_to_independent_ui_resources(self) -> None:
        expected = {
            "engineering_facts": "ui://mcp-app-ui/engineering_facts/index.html",
            "report_generation": "ui://mcp-app-ui/report_generation/index.html",
        }

        for name, resource_uri in expected.items():
            tool = _run(mcp.get_tool(name))
            self.assertIsNotNone(tool)
            self.assertEqual(tool.meta["ui"]["resourceUri"], resource_uri)

    def test_ui_resources_are_registered(self) -> None:
        resources = _run(mcp.list_resources())
        self.assertEqual(
            [str(resource.uri) for resource in resources],
            [
                "ui://mcp-app-ui/engineering_facts/index.html",
                "ui://mcp-app-ui/report_generation/index.html",
            ],
        )


if __name__ == "__main__":
    unittest.main()
