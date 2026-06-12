import os

def load_config_yaml():
    import yaml
    paths = ["configuration.yaml", "../configuration.yaml"]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, 'r') as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
    return {}

_yaml_config = load_config_yaml()

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
    """
    tools = _yaml_config.get("tools")
    if tools is not None:
        return bool(tools.get(tool_name, False))
        
    # Fallback to env var
    enabled_tools = os.getenv("ENABLED_TOOLS")
    if enabled_tools is None:
        return True # Default to True for backward compatibility if the env var isn't set
        
    enabled_list = [t.strip() for t in enabled_tools.split(",") if t.strip()]
    return tool_name in enabled_list
