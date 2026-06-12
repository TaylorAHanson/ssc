"""
Database models for the dynamic Tool Registry.

The registry is the data-driven replacement for the hardcoded agent tool gating
(the ``required_role`` filter + the ``_AUTHORING_TOOL_NAMES`` whitelist that used
to live in ``app/api/v1/agent.py``). Every tool the agent can use — whether a
locally-defined ``McpTool`` or one discovered from a Databricks MCP server — has a
row here that an admin controls:

- which usage context may use it (``enabled_for_main_agent`` = the unified
  self-service chat; ``enabled_for_workflow_agent`` = the workflow-authoring chat;
  ``enabled_for_workflow_execution`` = usable as a workflow graph building block),
- which internal roles may use it (``allowed_roles``; empty = all roles),
- whether it runs as the Service Principal or On-Behalf-Of the user
  (``identity_mode``).

``McpSourceModel`` is an admin-registered MCP server endpoint (e.g. a managed
Databricks ``/api/2.0/mcp/functions/{catalog}/{schema}`` server, a Genie space, an
AI Search index, or an external/custom server). Tools discovered from a source are
rows in ``ToolRegistryModel`` with ``origin='mcp'`` and a ``source_id`` FK.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped

from app.db.base import Base

# Tool origin: where a tool is defined / how it is invoked.
TOOL_ORIGIN_LOCAL = "local"        # app.tools.** chat-agent McpTool
TOOL_ORIGIN_WORKFLOW = "workflow"  # app.workflows.tools provider/graph McpTool
TOOL_ORIGIN_MCP = "mcp"            # discovered from a remote MCP server

# Identity the tool runs as when invoked.
IDENTITY_SP = "sp"      # the app Service Principal
IDENTITY_OBO = "obo"    # On-Behalf-Of the calling user

# Usage contexts the per-tool toggles map to (the three columns).
SURFACE_MAIN = "main_agent"                 # unified self-service chat ("main agent")
SURFACE_WORKFLOW_AGENT = "workflow_agent"   # workflow-authoring chat
SURFACE_WORKFLOW_EXECUTION = "workflow_execution"  # usable as a workflow graph building block

# MCP source kinds (informational; drive UI grouping + the discovery URL shape).
MCP_SOURCE_KINDS = (
    "managed_functions",  # /api/2.0/mcp/functions/{catalog}/{schema}
    "sql",                # /api/2.0/mcp/sql
    "genie",              # /api/2.0/mcp/genie[/{space_id}]
    "ai_search",          # /api/2.0/mcp/ai-search/{catalog}/{schema}[/{index}]
    "external",           # /api/2.0/mcp/external/{connection}
    "custom_app",         # https://<app-url>/mcp
)


class McpSourceModel(Base):
    """An admin-registered MCP server endpoint discovered with the Service Principal."""

    __tablename__ = "mcp_sources"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the source")
    name: Mapped[str] = Column(
        String, nullable=False, unique=True, index=True,
        comment="Human-readable label for the MCP server (e.g. 'UC system.ai functions')",
    )
    server_url: Mapped[str] = Column(
        String, nullable=False,
        comment="Full MCP server URL the SP lists tools from (e.g. https://host/api/2.0/mcp/functions/system/ai)",
    )
    kind: Mapped[str] = Column(
        String, nullable=False, default="managed_functions",
        comment="One of MCP_SOURCE_KINDS — drives UI grouping",
    )
    enabled: Mapped[bool] = Column(
        Boolean, nullable=False, default=True,
        comment="When false, the source is not synced and its tools are excluded",
    )
    default_identity_mode: Mapped[str] = Column(
        String, nullable=False, default=IDENTITY_OBO,
        comment="Identity newly-discovered tools from this source default to (sp | obo)",
    )
    created_by: Mapped[Optional[str]] = Column(String, nullable=True, comment="Email of the admin who added it")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True, comment="When tools were last discovered")
    last_sync_status: Mapped[Optional[str]] = Column(
        String, nullable=True, comment="ok | error — outcome of the last discovery run",
    )
    last_sync_error: Mapped[Optional[str]] = Column(Text, nullable=True, comment="Error detail from the last failed sync")
    last_tool_count: Mapped[Optional[int]] = Column(Integer, nullable=True, comment="Tools found on the last successful sync")


class ToolRegistryModel(Base):
    """A single agent-usable tool (local or MCP-discovered) with admin-managed gating."""

    __tablename__ = "tool_registry"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the registry row")
    tool_name: Mapped[str] = Column(
        String, nullable=False, index=True,
        comment="The tool's invocation name (what the LLM calls / what ToolExecutor runs)",
    )
    origin: Mapped[str] = Column(
        String, nullable=False, default=TOOL_ORIGIN_LOCAL, index=True,
        comment="local (app.tools chat) | workflow (app.workflows.tools provider) | mcp (remote MCP server)",
    )
    source_id: Mapped[Optional[str]] = Column(
        String, ForeignKey("mcp_sources.id", ondelete="CASCADE"), nullable=True, index=True,
        comment="FK to mcp_sources for origin='mcp'; NULL for local tools",
    )
    description: Mapped[Optional[str]] = Column(Text, nullable=True, comment="LLM-facing tool description")
    input_schema: Mapped[Optional[dict]] = Column(
        JSON, nullable=True, comment="JSON schema for the tool's parameters (for the LLM + UI)",
    )
    is_mutating: Mapped[bool] = Column(
        Boolean, nullable=False, default=False,
        comment="Whether the tool mutates state — drives OPA pre-flight + idempotency",
    )
    side_effect_class: Mapped[str] = Column(
        String, nullable=False, default="read",
        comment="One of SIDE_EFFECT_CLASSES — OPA bounding hint",
    )
    enabled: Mapped[bool] = Column(
        Boolean, nullable=False, default=True,
        comment="Master switch; when false the tool is never offered to any surface",
    )
    enabled_for_main_agent: Mapped[bool] = Column(
        Boolean, nullable=False, default=False,
        comment="Offer this tool to the unified self-service main chat agent",
    )
    enabled_for_workflow_agent: Mapped[bool] = Column(
        Boolean, nullable=False, default=False,
        comment="Offer this tool to the workflow-authoring chat agent",
    )
    enabled_for_workflow_execution: Mapped[bool] = Column(
        Boolean, nullable=False, default=False,
        comment="Allow this tool to be used as a workflow graph building block (step tool)",
    )
    exposed_via_mcp: Mapped[bool] = Column(
        Boolean, nullable=False, default=False,
        comment="Publish this tool over the in-app MCP server (/mcp) for external agents/apps",
    )
    allowed_roles: Mapped[Optional[list]] = Column(
        JSON, nullable=True,
        comment="Internal roles permitted to use the tool ([] / null = all roles)",
    )
    identity_mode: Mapped[str] = Column(
        String, nullable=False, default=IDENTITY_OBO,
        comment="Run the tool as the SP or OBO the user (sp | obo)",
    )
    discovered_at: Mapped[Optional[datetime]] = Column(
        DateTime, nullable=True, comment="When an MCP tool was last seen during discovery",
    )
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
