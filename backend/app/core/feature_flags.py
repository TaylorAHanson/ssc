import os

# Single source of truth: the live process-wide config built from the in-code
# defaults (default_config.py) in config.py. Importing the same object here
# means settings-store overrides applied at runtime are reflected immediately.
from app.core.config import _yaml_config

def is_feature_enabled(feature_flag: str) -> bool:
    """
    Check if a feature flag is enabled.
    """
    if not feature_flag:
        return True
        
    features = _yaml_config.get("features")
    if features is not None and feature_flag in features:
        return bool(features[feature_flag])
    
    # Fallback to env var
    env_var_name = f"FEATURE_{feature_flag.upper()}"
    val = os.getenv(env_var_name, "true").lower()
    return val == "true"

def is_tool_enabled(tool_name: str) -> bool:
    """
    Check if a specific tool is enabled.

    A tool entry in ``configuration.yaml`` may be either a bare bool
    (``run_sql: true``) or a nested config dict (``ask_your_data: {enabled: true,
    ...}``). For dict entries we honor an explicit ``enabled:`` key and otherwise
    treat the presence of config as "enabled" — using bare ``bool(dict)`` would
    silently ignore ``enabled: false`` (a non-empty dict is always truthy) and
    disable a tool whose config happens to be an empty dict.
    """
    tools = _yaml_config.get("tools")
    if tools is not None:
        if tool_name not in tools:
            return False
        val = tools[tool_name]
        if isinstance(val, dict):
            return bool(val.get("enabled", True))
        return bool(val)

    # Fallback to env var
    enabled_tools = os.getenv("ENABLED_TOOLS")
    if enabled_tools is None:
        return True # Default to True for backward compatibility if the env var isn't set
        
    enabled_list = [t.strip() for t in enabled_tools.split(",") if t.strip()]
    return tool_name in enabled_list
