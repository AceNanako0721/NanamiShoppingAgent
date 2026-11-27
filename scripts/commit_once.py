"""Synthetic crash-test worker. Uses MCP, never issues approval."""

import asyncio
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def main():
    async with Client(
        StdioServerParameters(
            command=sys.executable, args=["-m", "nanami_shopping.server", "--config", sys.argv[1]]
        )
    ) as client:
        await client.call_tool(
            "shopping_commit_purchase", dict(task_id=sys.argv[2], quote_id=sys.argv[3])
        )


if __name__ == "__main__":
    asyncio.run(main())
