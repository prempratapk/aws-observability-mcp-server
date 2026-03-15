"""AWS Observability MCP Server - entrypoint with tool registration."""

from __future__ import annotations

import logging
import sys
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)

from aws_observability_mcp.tools.cloudwatch_logs import (
    TOOL_DEFINITIONS as CW_LOGS_TOOLS,
    handle_tool_call as cw_logs_handle,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---- Server instantiation ----
app = Server("aws-observability-mcp")

# Registry: tool name -> handler
_TOOL_REGISTRY: dict[str, Any] = {}
for _tool in CW_LOGS_TOOLS:
    _TOOL_REGISTRY[_tool.name] = cw_logs_handle


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Return all registered MCP tools."""
    return CW_LOGS_TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Dispatch incoming tool calls to the appropriate handler."""
    handler = _TOOL_REGISTRY.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        return await handler(name, arguments)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool '%s' raised an error", name)
        return [TextContent(type="text", text=f"Error executing tool '{name}': {exc}")]


def main() -> None:
    """Run the MCP server over stdio transport."""
    logger.info("Starting AWS Observability MCP Server")
    import asyncio

    async def _run() -> None:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="aws-observability-mcp",
                    server_version="0.1.0",
                    capabilities=app.get_capabilities(
                        notification_options=None,
                        experimental_capabilities={},
                    ),
                ),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
