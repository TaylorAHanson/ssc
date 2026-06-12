import pkgutil
import importlib
import inspect
from pathlib import Path
from app.tools.mcp import McpTool
from app.core.feature_flags import is_feature_enabled, is_tool_enabled
import logging

logger = logging.getLogger(__name__)

AVAILABLE_TOOLS = []

def load_tools():
    """Dynamically loads tools from the app.tools directory."""
    package_dir = Path(__file__).resolve().parent
    
    # First, import the base tool definitions to avoid circular dependency issues
    try:
        importlib.import_module("app.tools.mcp")
    except Exception as e:
        logger.warning(f"Failed to import mcp module: {e}")
    
    # Walk through all modules in the tools directory
    for _, module_name, is_pkg in pkgutil.walk_packages([str(package_dir)], prefix="app.tools."):
        if module_name in ("app.tools.mcp", "app.tools"):
            continue
            
        try:
            module = importlib.import_module(module_name)
            
            # Find all McpTool instances in the module
            for name, obj in inspect.getmembers(module):
                if isinstance(obj, McpTool):
                    # Check individual tool override
                    if not is_tool_enabled(obj.name):
                        continue
                        
                    # Check feature flag
                    if is_feature_enabled(obj.feature_flag):
                        if obj not in AVAILABLE_TOOLS:
                            AVAILABLE_TOOLS.append(obj)
        except ImportError as e:
            logger.debug(f"Skipping module {module_name} due to import error: {e}")
        except Exception as e:
            logger.warning(f"Failed to load tools from module {module_name}: {e}")

# Call load_tools on import
load_tools()

def get_read_only_tools():
    """Returns tools that don't perform destructive actions or workflow executions."""
    # Exclude execute_workflow
    return [t for t in AVAILABLE_TOOLS if t.name != "execute_workflow"]

def _attribute_external_tools():
    """Code-declared external tools (``external=True``); the seed + safe fallback."""
    return [t for t in AVAILABLE_TOOLS if getattr(t, "external", False)]


def get_external_tools():
    """Tools published over the in-app MCP server (``app.mcp_server``).

    Data-driven: an admin opts a tool in via the Tool Registry's ``exposed_via_mcp``
    flag (seeded from the code-declared ``external=True`` attribute). These are the
    only tools exposed over ``/mcp`` (registerable as a custom MCP provider in
    Databricks AI Gateway); all others stay app-internal. Falls back to the
    attribute-based set if the registry can't be consulted.
    """
    try:
        from app.db.session import get_session_local
        from app.db.tool_registry import ToolRegistryModel
        from app.tools import catalog

        db = get_session_local()()
        try:
            names = {
                r[0]
                for r in db.query(ToolRegistryModel.tool_name)
                .filter(
                    ToolRegistryModel.enabled.is_(True),
                    ToolRegistryModel.exposed_via_mcp.is_(True),
                )
                .all()
            }
        finally:
            db.close()
        resolved = [t for n in names if (t := catalog.get_by_name(n)) is not None]
        # If the registry hasn't been seeded yet, fall back to the code defaults.
        if not resolved and names == set():
            return _attribute_external_tools()
        return resolved
    except Exception as e:  # noqa: BLE001 - never break MCP startup on a registry hiccup
        logger.warning(f"get_external_tools: registry unavailable, using code defaults: {e}")
        return _attribute_external_tools()
