"""
Run MCP Server via Stdio.
For local usage with Claude Desktop or other stdio clients.
"""
import asyncio
from app.mcp_server import mcp

if __name__ == "__main__":
    # We need to run inside an event loop
    asyncio.run(mcp.run_stdio_async())
