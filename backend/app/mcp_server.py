"""
MCP Server Adapter.
Exposes internal tools via the standard Model Context Protocol.
"""
import logging
from mcp.server.fastmcp import FastMCP
from app.core.config import settings
from app.tools import AVAILABLE_TOOLS

logger = logging.getLogger(__name__)

# Initialize FastMCP Server
mcp = FastMCP(settings.PROJECT_NAME)

# Register tools
for tool in AVAILABLE_TOOLS:
    # We register the underlying function directly.
    # FastMCP handles schema generation from type hints/Pydantic models on the function.
    if hasattr(tool, "_func"):
        try:
            # Check if name is already registered to avoid duplicates if list has issues
            # FastMCP doesn't expose a check easily, but we trust AVAILABLE_TOOLS is unique.
            mcp.tool(name=tool.name, description=tool.description)(tool._func)
            logger.info(f"Registered MCP tool: {tool.name}")
        except Exception as e:
            logger.warning(f"Failed to register tool {tool.name} with MCP: {e}")
    else:
        logger.warning(f"Skipping tool {tool.name}: No _func attribute found")

