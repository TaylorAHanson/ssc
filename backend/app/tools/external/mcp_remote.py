"""``RemoteMcpTool`` — an ``McpTool``-shaped adapter over a remote MCP tool.

Built from a ``ToolRegistryModel`` row (``origin='mcp'``) plus its source's
``server_url``. It exposes exactly the attributes the agent runner and the shared
``ToolExecutor`` read (``name``, ``description``, ``input_schema``, ``is_mutating``,
``side_effect_class``, ``friendly_label``, ``execute``), so a remote tool flows
through the same governance pipeline (OPA pre-flight for mutating calls, audit,
idempotency) as any local tool — no special-casing in the runner.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.tools.external import mcp_client


class RemoteMcpTool:
    """Adapter that makes a remote MCP tool callable like a local ``McpTool``."""

    def __init__(
        self,
        *,
        name: str,
        server_url: str,
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
        is_mutating: bool = False,
        side_effect_class: str = "read",
        identity_mode: str = "obo",
        policy_ref: Optional[str] = None,
    ):
        self._name = name
        self._server_url = server_url
        self._description = description or f"Remote MCP tool '{name}'."
        self._input_schema = input_schema or {"type": "object", "properties": {}}
        self._is_mutating = bool(is_mutating)
        self._side_effect_class = side_effect_class
        self._identity_mode = identity_mode
        self._policy_ref = policy_ref
        # No pydantic schema to validate against; ToolExecutor skips validation
        # when this is None (the remote server enforces its own arg contract).
        self._args_schema = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description.strip()

    @property
    def input_schema(self) -> Dict[str, Any]:
        return self._input_schema

    @property
    def is_mutating(self) -> bool:
        return self._is_mutating

    @property
    def side_effect_class(self) -> str:
        return self._side_effect_class

    @property
    def policy_ref(self) -> Optional[str]:
        return self._policy_ref

    @property
    def required_role(self) -> Optional[str]:
        # Role gating is enforced by the registry before the tool is offered, so
        # the adapter itself declares no extra requirement.
        return None

    @property
    def feature_flag(self) -> Optional[str]:
        return None

    @property
    def external(self) -> bool:
        return False

    @property
    def friendly_label(self) -> str:
        humanized = self._name.replace("_", " ").strip().capitalize()
        return f"Running {humanized}..."

    @property
    def friendly_completion_label(self) -> Optional[str]:
        return None

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Invoke the remote tool, honoring the configured identity mode.

        Strips executor-injected context keys (``_obo_token`` / ``_user_*``) from
        the arguments forwarded to the MCP server; only model-supplied args go over
        the wire.
        """
        obo_token = kwargs.get("_obo_token")
        arguments = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        return mcp_client.call_tool(
            self._server_url,
            self._name,
            arguments,
            identity_mode=self._identity_mode,
            obo_token=obo_token,
        )
