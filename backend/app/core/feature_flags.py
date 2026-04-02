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

def is_workflow_enabled(workflow_type: str) -> bool:
    """
    Check if a specific workflow is enabled.
    workflow_type is expected to be a string like 'RequestType.ASSET_DEDUPLICATION' or 'asset_deduplication'
    """
    workflows = _yaml_config.get("workflows")
    if workflows is not None:
        # Check both the raw string value and the RequestType.XXX format
        if workflow_type in workflows:
            return bool(workflows[workflow_type])
        req_type_format = f"RequestType.{workflow_type.upper()}"
        if req_type_format in workflows:
            return bool(workflows[req_type_format])
        return False
        
    # Fallback to env var
    enabled_workflows = os.getenv("ENABLED_WORKFLOWS")
    if enabled_workflows is None:
        return True # Default to True for backward compatibility if the env var isn't set
        
    enabled_list = [w.strip() for w in enabled_workflows.split(",") if w.strip()]
    
    # Check both the raw string value and the RequestType.XXX format
    return workflow_type in enabled_list or f"RequestType.{workflow_type.upper()}" in enabled_list
