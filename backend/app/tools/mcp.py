"""
Fast MCP implementation for defining agent tools.
"""
import inspect
import functools
from typing import Any, Callable, Dict, Optional, Type, get_type_hints, Union
from pydantic import BaseModel, create_model

class McpTool:
    """
    Wrapper for a function to make it look like a BaseTool.
    This allows us to transition to decorators while maintaining compatibility.
    """
    
    def __init__(
        self,
        func: Callable,
        args_schema: Type[BaseModel],
        name: Optional[str] = None,
        description: Optional[str] = None,
        required_role: Optional[str] = None,
        feature_flag: Optional[str] = None,
        friendly_label: Optional[str] = None,
        friendly_completion_label: Optional[str] = None,
    ):
        self._func = func
        self._args_schema = args_schema
        self._name = name or func.__name__
        self._description = description or func.__doc__ or ""
        self._required_role = required_role
        self._feature_flag = feature_flag
        self._friendly_label = friendly_label
        self._friendly_completion_label = friendly_completion_label
        
    @property
    def name(self) -> str:
        return self._name
        
    @property
    def description(self) -> str:
        return self._description.strip()

    @property
    def required_role(self) -> Optional[str]:
        return self._required_role

    @property
    def feature_flag(self) -> Optional[str]:
        return self._feature_flag

    @property
    def friendly_label(self) -> str:
        """User-facing copy shown while the tool is running.

        Falls back to a humanized version of the snake_case tool name
        (e.g. ``search_user_entitlements`` -> ``Searching user entitlements...``)
        when no explicit label was provided at registration time.
        """
        if self._friendly_label:
            return self._friendly_label
        humanized = self._name.replace("_", " ").strip().capitalize()
        return f"Running {humanized}..."

    @property
    def friendly_completion_label(self) -> Optional[str]:
        """Optional copy shown after the tool succeeds.

        ``None`` => the UI swaps the running pill to a generic done state
        without changing the label.
        """
        return self._friendly_completion_label
        
    @property
    def input_schema(self) -> Dict[str, Any]:
        """Returns the JSON schema for the input parameters."""
        schema = self._args_schema.model_json_schema()
        
        # Patch schema for strict validation by some LLMs (e.g. Databricks FM APIs)
        # Ensure array types have an 'items' definition.
        if "properties" in schema:
            for prop_name, prop_def in schema["properties"].items():
                if prop_def.get("type") == "array" and "items" not in prop_def:
                    prop_def["items"] = {"type": "string"}
                    
        return schema
        
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Executes the wrapped function with the provided arguments."""
        # Validate arguments against function signature to prevent type errors
        # when extra context (like _user_email) is injected by the runner.
        sig = inspect.signature(self._func)
        bound_args = {}
        
        for k, v in kwargs.items():
            if k in sig.parameters:
                bound_args[k] = v
            elif any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                # Function accepts **kwargs, pass everything
                bound_args[k] = v
                
        # Check if the function is a coroutine
        if inspect.iscoroutinefunction(self._func):
            return await self._func(**bound_args)
        else:
            return self._func(**bound_args)

def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    args_schema: Optional[Type[BaseModel]] = None,
    required_role: Optional[str] = None,
    feature_flag: Optional[str] = None,
    friendly_label: Optional[str] = None,
    friendly_completion_label: Optional[str] = None,
):
    """
    Decorator to register a function as a tool.
    
    Usage:
        class MyArgs(BaseModel):
            arg1: str
            
        @tool(args_schema=MyArgs, feature_flag="core")
        def my_tool(arg1: str):
            ...
    
    Or automatic schema inference (subset of features):
        @tool()
        def my_tool(arg1: str, arg2: int = 5):
            ...
    """
    def decorator(func: Callable) -> McpTool:
        # Determine schema
        actual_schema = args_schema
        
        if actual_schema is None:
            # Simple inference from type hints
            # This is a basic implementation. For complex types, passing explicit args_schema is better.
            type_hints = get_type_hints(func)
            fields = {}
            for param_name, param in inspect.signature(func).parameters.items():
                if param_name == 'return':
                    continue
                
                annotation = type_hints.get(param_name, Any)
                default = param.default
                
                if default == inspect.Parameter.empty:
                    fields[param_name] = (annotation, ...)
                else:
                    fields[param_name] = (annotation, default)
            
            tool_name = name or func.__name__
            actual_schema = create_model(f"{tool_name}Input", **fields)
            
        return McpTool(
            func,
            actual_schema,
            name,
            description,
            required_role,
            feature_flag,
            friendly_label,
            friendly_completion_label,
        )
        
    return decorator
