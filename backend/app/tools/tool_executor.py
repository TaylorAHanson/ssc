"""
V2 ToolExecutor — the single choke point for every agent tool call.

Both the in-process agent runner (``app.agents.runner``) and the external MCP
server (``app.mcp_server``) route tool calls through :meth:`ToolExecutor.run`,
so governance is applied uniformly instead of being scattered across call sites
or bypassed entirely (the ``/mcp`` path historically called the raw function).

Pipeline (per call):
  1. inject identity context (``_obo_token`` / ``_user_*``) from the context
  2. validate args against the tool's Pydantic schema
  3. classify (read ``tool.side_effect_class`` / ``tool.is_mutating``)
  4. OPA pre-flight for mutating tools -> ``{allow, requires_approval, ...}``
       - SHADOW mode (default): evaluate + log the decision, never block
       - ENFORCE mode (``AGENT_TOOL_OPA_ENFORCE``): deny / unmet-approval halts
  5. idempotency: if a prior success fact for this scope+key exists, return the
     cached result instead of re-executing (best-effort; needs ``db`` + ``scope_id``)
  6. execute the tool
  7. append an audit fact (best-effort; needs ``db`` + ``scope_id``)

The executor returns the tool's raw result on success. Policy refusals and
unmet-approval gates are returned as ``{"error": ..., "policy_decision": {...}}``
dicts so the agent loop surfaces them as a failed tool result and the model can
adapt (rather than raising and killing the stream).
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Keys the runner/executor inject into tool kwargs; never forwarded to OPA input
# or persisted in audit facts (they carry tokens / PII).
_INJECTED_KEYS = ("_obo_token", "_user_email", "_user_roles", "_user_entitlements")

_DEFAULT_ALLOW_DECISION = {
    "allow": True,
    "requires_approval": False,
    "approval_type": "",
    "reason": "No policy decision (default allow).",
}


@dataclass
class ToolContext:
    """Per-call context carried through the executor.

    ``db`` + ``scope_id`` are optional: when present the executor persists audit
    facts and enforces idempotency; when absent (e.g. the lightweight chat agent
    path today) it degrades to log-only. ``approvals`` holds approval facts the
    caller has already gathered (e.g. ``["manager"]``) so satisfied gates pass.
    """
    tool_call_id: str = ""
    obo_token: Optional[str] = None
    user_identity: Dict[str, Any] = field(default_factory=dict)
    db: Optional[Any] = None
    scope_id: Optional[str] = None
    approvals: List[str] = field(default_factory=list)
    # Extra kwargs the caller wants injected into the tool call (e.g.
    # execute_workflow's conversation_history) that aren't model-supplied args.
    injected_args: Dict[str, Any] = field(default_factory=dict)


class ToolExecutor:
    """Shared, governed executor for agent tools (singleton via module ``executor``)."""

    def __init__(self):
        self._opa = None  # lazy: avoid OPA setup cost until a mutating tool runs

    # -- OPA ---------------------------------------------------------------
    def _get_opa(self):
        if self._opa is None:
            from app.providers.opa.client import OpaProvider
            self._opa = OpaProvider(settings.opa_provider_config())
        return self._opa

    async def _evaluate_policy(self, tool, sanitized_args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
        """Evaluate ``data.agent.tools.decision`` for a mutating tool.

        Failures degrade to the configured posture: SHADOW => default allow;
        ENFORCE => fail-closed deny (a mutating tool must not run if we can't
        get a policy decision).
        """
        opa_input = {
            "tool": tool.name,
            "side_effect_class": tool.side_effect_class,
            "is_mutating": tool.is_mutating,
            "policy_ref": tool.policy_ref,
            "args": sanitized_args,
            "user": {
                "email": ctx.user_identity.get("email"),
                "roles": ctx.user_identity.get("roles") or [],
                "entitlements": ctx.user_identity.get("entitlements") or [],
            },
            "approvals": ctx.approvals,
        }
        try:
            opa = self._get_opa()
            result = await opa.evaluate(
                policy_path="agent_tools.rego",
                query="data.agent.tools.decision",
                input_data=opa_input,
            )
            if isinstance(result, dict) and "allow" in result:
                return result
            logger.warning(
                "ToolExecutor: empty/invalid OPA decision for '%s' (%s)",
                tool.name, tool.side_effect_class,
            )
        except Exception as e:
            logger.warning("ToolExecutor: OPA evaluation failed for '%s': %s", tool.name, e)

        if settings.AGENT_TOOL_OPA_ENFORCE:
            return {
                "allow": False,
                "requires_approval": True,
                "approval_type": "admin",
                "reason": "Policy decision unavailable; failing closed (enforce mode).",
            }
        return dict(_DEFAULT_ALLOW_DECISION)

    # -- idempotency / audit (best-effort; require db + scope_id) ----------
    def _idempotency_key(self, tool, ctx: ToolContext) -> str:
        return f"{ctx.scope_id}:{ctx.tool_call_id or tool.name}"

    def _cached_result(self, tool, ctx: ToolContext) -> Optional[Any]:
        if not (ctx.db and ctx.scope_id):
            return None
        try:
            from app.state_machines.facts import get_latest_fact
            fact = get_latest_fact(
                ctx.db, ctx.scope_id, "agent_tool_succeeded",
                idempotency_key=self._idempotency_key(tool, ctx),
            )
            if fact and isinstance(fact.event_data, dict):
                return fact.event_data.get("result")
        except Exception as e:
            logger.debug("ToolExecutor: idempotency lookup skipped: %s", e)
        return None

    def _audit(self, tool, ctx: ToolContext, *, ok: bool, decision: Optional[Dict[str, Any]],
               result: Any = None, error: Optional[str] = None) -> None:
        fact_type = "agent_tool_succeeded" if ok else "agent_tool_failed"
        if not (ctx.db and ctx.scope_id):
            logger.info(
                "[tool-audit] %s tool=%s class=%s ok=%s decision=%s%s",
                ctx.scope_id or "(no-scope)", tool.name, tool.side_effect_class, ok,
                decision, f" error={error}" if error else "",
            )
            return
        try:
            from app.state_machines.facts import add_fact
            data: Dict[str, Any] = {
                "tool": tool.name,
                "side_effect_class": tool.side_effect_class,
                "idempotency_key": self._idempotency_key(tool, ctx),
                "policy_decision": decision,
            }
            if ok:
                data["result"] = result
            else:
                data["error"] = error
            add_fact(
                ctx.db, ctx.scope_id, fact_type, data,
                actor=ctx.user_identity.get("email") or "agent",
            )
        except Exception as e:
            logger.debug("ToolExecutor: audit fact skipped: %s", e)

    # -- main entrypoint ---------------------------------------------------
    async def run(self, tool, ctx: ToolContext, **args) -> Any:
        """Execute ``tool`` with ``args`` under the governance pipeline."""
        # 1. Inject identity context + caller-supplied extra args. The tool's
        #    own execute() filters by signature, so unused keys are harmless.
        call_args = dict(args)
        call_args.update(ctx.injected_args)
        if ctx.obo_token:
            call_args["_obo_token"] = ctx.obo_token
        if ctx.user_identity:
            call_args["_user_email"] = ctx.user_identity.get("email")
            call_args["_user_roles"] = ctx.user_identity.get("roles")
            call_args["_user_entitlements"] = ctx.user_identity.get("entitlements")

        # Args the model actually supplied (for validation + policy + audit),
        # excluding injected identity/context keys.
        model_args = {k: v for k, v in call_args.items() if k not in _INJECTED_KEYS}

        # 2. Validate model-supplied args against the tool's schema (non-fatal in
        #    shadow mode: log and continue so we don't regress current behavior).
        self._validate_args(tool, model_args)

        # 3 + 4. Policy pre-flight for mutating tools.
        decision: Optional[Dict[str, Any]] = None
        if tool.is_mutating:
            decision = await self._evaluate_policy(tool, model_args, ctx)
            blocked = self._enforce(tool, decision, ctx)
            if blocked is not None:
                self._audit(tool, ctx, ok=False, decision=decision,
                            error=blocked.get("error"))
                return blocked

        # 5. Idempotency (mutating tools only; needs db + scope).
        if tool.is_mutating:
            cached = self._cached_result(tool, ctx)
            if cached is not None:
                logger.info("ToolExecutor: idempotent replay for '%s'", tool.name)
                return cached

        # 6. Execute.
        try:
            result = await tool.execute(**call_args)
        except Exception as e:
            self._audit(tool, ctx, ok=False, decision=decision, error=str(e))
            raise

        # 7. Audit success.
        self._audit(tool, ctx, ok=True, decision=decision, result=_audit_safe(result))
        return result

    # -- helpers -----------------------------------------------------------
    def _validate_args(self, tool, model_args: Dict[str, Any]) -> None:
        schema = getattr(tool, "_args_schema", None)
        if schema is None:
            return
        try:
            schema(**model_args)
        except Exception as e:
            # The tool's execute() also filters kwargs; we log rather than hard
            # fail so we don't change behavior for tools whose callers already
            # rely on lenient coercion. Surfaced for policy tuning.
            logger.info("ToolExecutor: arg validation note for '%s': %s", tool.name, e)

    def _enforce(self, tool, decision: Dict[str, Any], ctx: ToolContext) -> Optional[Dict[str, Any]]:
        """Return a refusal dict if the call must be blocked, else ``None``.

        In shadow mode we always return ``None`` (log only); the decision was
        already logged by the caller via audit.
        """
        if not settings.AGENT_TOOL_OPA_ENFORCE:
            logger.info(
                "[opa-shadow] tool=%s class=%s decision=%s",
                tool.name, tool.side_effect_class, decision,
            )
            return None
        if not decision.get("allow", True):
            return {
                "error": f"Policy denied tool '{tool.name}': {decision.get('reason', 'not allowed')}",
                "policy_decision": decision,
            }
        if decision.get("requires_approval") and not self._approval_satisfied(decision, ctx):
            return {
                "error": (
                    f"Tool '{tool.name}' requires '{decision.get('approval_type')}' approval "
                    f"before it can run: {decision.get('reason', '')}"
                ),
                "requires_approval": True,
                "policy_decision": decision,
            }
        return None

    def _approval_satisfied(self, decision: Dict[str, Any], ctx: ToolContext) -> bool:
        atype = decision.get("approval_type")
        return bool(atype) and atype in (ctx.approvals or [])


def _audit_safe(result: Any) -> Any:
    """Keep audit facts small: store a compact preview of large/string results."""
    try:
        if isinstance(result, str) and len(result) > 2000:
            return result[:2000] + "...[truncated]"
        return result
    except Exception:
        return None


# Module-level singleton used by both the runner and the MCP server.
executor = ToolExecutor()
