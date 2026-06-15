"""
Fast MCP implementation for defining agent tools.
"""
import asyncio
import inspect
import functools
from typing import Any, Callable, Dict, Optional, Type, get_type_hints, Union
from pydantic import BaseModel, create_model

# Canonical side-effect classes used by the V2 ToolExecutor + the
# `data.agent.tools` OPA package to decide bounding (approval gates, idempotency,
# audit). `read` is the safe default; everything else mutates some system.
#   read       - queries/lists/searches; no external mutation
#   app_write  - writes app-internal state only (e.g. feedback rows)
#   data_grant - grants/revokes Unity Catalog access
#   infra      - Terraform apply, workspace/volume/SP/repo creation, workflow kickoff
#   membership - identity-group membership changes (e.g. LMWS / Entra / Okta)
#   notify     - email/slack/teams notifications
#   destructive- enforcement kill / delete / uncertify (irreversible-ish)
SIDE_EFFECT_CLASSES = frozenset(
    {"read", "app_write", "data_grant", "infra", "membership", "notify", "destructive"}
)
MUTATING_SIDE_EFFECT_CLASSES = SIDE_EFFECT_CLASSES - {"read"}


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
        external: bool = False,
        side_effect_class: str = "read",
        is_mutating: Optional[bool] = None,
        policy_ref: Optional[str] = None,
        success_predicate: Optional[Any] = None,
    ):
        self._func = func
        self._args_schema = args_schema
        self._name = name or func.__name__
        self._description = description or func.__doc__ or ""
        self._required_role = required_role
        self._feature_flag = feature_flag
        self._friendly_label = friendly_label
        self._friendly_completion_label = friendly_completion_label
        self._external = external
        if side_effect_class not in SIDE_EFFECT_CLASSES:
            raise ValueError(
                f"Tool '{self._name}': invalid side_effect_class "
                f"'{side_effect_class}'. Must be one of {sorted(SIDE_EFFECT_CLASSES)}."
            )
        self._side_effect_class = side_effect_class
        # `is_mutating` defaults to "anything not a plain read". An author may
        # override (e.g. a read-classified tool that still has a benign write).
        self._is_mutating = (
            is_mutating
            if is_mutating is not None
            else side_effect_class in MUTATING_SIDE_EFFECT_CLASSES
        )
        self._policy_ref = policy_ref
        self._success_predicate = success_predicate
        
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
    def external(self) -> bool:
        """Whether this tool may be exposed outside the app.

        When ``True`` the tool is published over the in-app MCP server
        (``mcp_server.py``, mounted at ``/mcp``), which can be registered as a
        custom MCP provider in Databricks AI Gateway so other agents/apps can
        reuse it.         Defaults to ``False`` so tools stay app-internal unless an
        author explicitly opts in.
        """
        return self._external

    @property
    def side_effect_class(self) -> str:
        """One of :data:`SIDE_EFFECT_CLASSES`. Drives ToolExecutor + OPA bounding."""
        return self._side_effect_class

    @property
    def is_mutating(self) -> bool:
        """Whether this tool mutates external/app state (vs. a pure read)."""
        return self._is_mutating

    @property
    def policy_ref(self) -> Optional[str]:
        """Optional OPA rule/identifier this tool maps to inside ``data.agent.tools``.

        ``None`` => the policy keys off ``side_effect_class`` alone.
        """
        return self._policy_ref

    @property
    def success_predicate(self) -> Optional[Any]:
        """Optional ``$``-expression deciding tool success (see app/workflows/expr.py).

        Evaluated against ``{"result": <tool output>}``. When set and it evaluates
        falsy, the call is treated as a failure even on an HTTP-200/dict result.
        ``None`` => fall back to the default envelope heuristics.
        """
        return self._success_predicate

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
    def accepted_args(self) -> Dict[str, Any]:
        """Introspect the wrapped function's *named* parameters for author-time
        arg linting.

        Returns ``{"named": set, "required": set, "accepts_var_kw": bool}`` where
        ``named`` is the explicit (non ``**kwargs``) parameters an author may set,
        ``required`` is those without a default, and ``accepts_var_kw`` is True if
        the function has a ``**kwargs`` catch-all. Parameters beginning with ``_``
        (executor-injected context like ``_user_email``) are excluded. When the
        tool only takes ``**kwargs`` (no named params) the contract is open and
        ``named`` is empty, so callers should skip the unknown-arg check.
        """
        import inspect as _inspect

        named: set = set()
        required: set = set()
        accepts_var_kw = False
        try:
            sig = _inspect.signature(self._func)
        except (TypeError, ValueError):
            return {"named": named, "required": required, "accepts_var_kw": True}
        for pname, p in sig.parameters.items():
            if p.kind == _inspect.Parameter.VAR_KEYWORD:
                accepts_var_kw = True
                continue
            if p.kind == _inspect.Parameter.VAR_POSITIONAL:
                continue
            if pname.startswith("_") or pname == "kwargs":
                continue
            named.add(pname)
            if p.default is _inspect.Parameter.empty:
                required.add(pname)
        return {"named": named, "required": required, "accepts_var_kw": accepts_var_kw}

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
        """Executes the wrapped function with the provided arguments.

        LLM-supplied arguments are validated/coerced against ``args_schema``
        before the call so that schema constraints (``Literal`` choices, numeric
        bounds, ``min_length``, types) are actually *enforced* — not merely
        advertised. This is a security boundary: several tools interpolate args
        into SQL, so an unconstrained ``Literal`` field would otherwise be an
        injection vector. Executor-injected context (underscore-prefixed keys
        like ``_obo_token`` / ``_user_email``) bypasses validation and is passed
        through untouched.
        """
        from pydantic import ValidationError

        # Split LLM args (declared in the schema) from injected context.
        schema_fields = set(getattr(self._args_schema, "model_fields", {}).keys())
        provided = {
            k: v
            for k, v in kwargs.items()
            if k in schema_fields and not k.startswith("_") and k != "kwargs"
        }
        context = {k: v for k, v in kwargs.items() if k not in provided}

        try:
            # exclude_unset keeps the function's own defaults authoritative for
            # args the caller didn't pass, while still validating/coercing the
            # ones it did.
            validated = self._args_schema(**provided).model_dump(exclude_unset=True)
        except ValidationError as e:
            return {
                "error": (
                    f"Invalid arguments for tool '{self._name}': {e.errors(include_url=False)}"
                )
            }

        merged = {**validated, **context}

        # Validate arguments against function signature to prevent type errors
        # when extra context (like _user_email) is injected by the runner.
        sig = inspect.signature(self._func)
        bound_args = {}

        for k, v in merged.items():
            if k in sig.parameters:
                bound_args[k] = v
            elif any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                # Function accepts **kwargs, pass everything
                bound_args[k] = v
                
        # Check if the function is a coroutine
        if inspect.iscoroutinefunction(self._func):
            return await self._func(**bound_args)
        # Synchronous tool bodies almost always make blocking calls (Databricks
        # SDK, requests, sync SQLAlchemy). Running them inline would block the
        # asyncio event loop and stall every other in-flight request, so we hand
        # them to the default thread pool. Async tools that themselves wrap
        # blocking SDK calls offload those calls internally via asyncio.to_thread.
        return await asyncio.to_thread(lambda: self._func(**bound_args))

def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    args_schema: Optional[Type[BaseModel]] = None,
    required_role: Optional[str] = None,
    feature_flag: Optional[str] = None,
    friendly_label: Optional[str] = None,
    friendly_completion_label: Optional[str] = None,
    external: bool = False,
    side_effect_class: str = "read",
    is_mutating: Optional[bool] = None,
    policy_ref: Optional[str] = None,
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

    Set ``external=True`` to publish the tool over the in-app MCP server so it
    can be registered as a custom MCP provider in AI Gateway and reused by other
    agents/apps. Tools are app-internal (``external=False``) by default.

    ``side_effect_class`` (default ``"read"``) tags the tool's blast radius for
    the V2 ToolExecutor + ``data.agent.tools`` OPA policy. Anything other than
    ``"read"`` is treated as mutating (auto-sets ``is_mutating``) and is subject
    to OPA pre-flight / approval gates. ``policy_ref`` optionally pins the tool
    to a specific OPA rule.
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
                # Skip the ``**kwargs`` / ``*args`` catch-alls and executor-injected
                # context (``_obo_token`` etc.) — they are NOT LLM-facing args, and
                # turning them into required schema fields would both pollute the
                # advertised JSON schema and (now that execute() validates) reject
                # every call to a ``def tool(**kwargs)`` body.
                if param.kind in (
                    inspect.Parameter.VAR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                ):
                    continue
                if param_name.startswith('_'):
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
            external,
            side_effect_class,
            is_mutating,
            policy_ref,
        )
        
    return decorator
