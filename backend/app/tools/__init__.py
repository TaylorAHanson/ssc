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
