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
# or persisted in audit facts (they carry tokens / PII / non-model context).
_INJECTED_KEYS = ("_obo_token", "_user_email", "_user_roles", "_user_entitlements", "_request_id")

_DEFAULT_ALLOW_DECISION = {
    "allow": True,
    "requires_approval": False,
    "approval_type": "",
    "reason": "No policy decision (default allow).",
}

# Status strings that, when a tool returns an envelope dict, indicate failure.
_FAILURE_STATUS = {"error", "failed", "failure"}


def is_tool_failure(result: Any, predicate: Any = None) -> Optional[str]:
    """Return a human-readable reason if ``result`` is a *false success*, else ``None``.

    A tool can return HTTP 200 / a dict that semantically means failure — the
    classic case is a ServiceNow (or any external) MCP call that "succeeds" at the
    transport level but whose body reports the operation didn't happen. Without
    this check a workflow would write its ``success_fact`` and advance anyway.

    Detection, in priority order:
      1. An author-declared ``success_predicate`` (a ``$``-expression evaluated
         against ``{"result": <output>}`` via app/workflows/expr.py). When present
         it is authoritative: falsy => failure, truthy => success.
      2. Result-envelope conventions on a dict result: ``ok``/``success`` is
         ``False``, a non-empty ``error``/``errors``, or ``status`` in
         {error, failed, failure}.

    A broken/unevaluable predicate never blocks (logged + treated as success) so a
    misconfigured predicate can't wedge every call.
    """
    # 1. Author-declared predicate is authoritative when present.
    if predicate is not None:
        try:
            from app.workflows.expr import evaluate
            ok = bool(evaluate(predicate, {"ctx": {"result": result}}))
        except Exception as e:  # noqa: BLE001 - never let a bad predicate wedge calls
            logger.warning(
                "is_tool_failure: success_predicate eval failed (treating as success): %s", e
            )
            return None
        return None if ok else "tool result did not satisfy its success_predicate"

    # 2. Envelope conventions (only meaningful for dict results).
    if not isinstance(result, dict):
        return None
    if result.get("ok") is False or result.get("success") is False:
        return str(result.get("error") or result.get("message") or "tool reported ok=false")
    err = result.get("error") or result.get("errors")
    if err:
        return str(err)
    status = result.get("status")
    if isinstance(status, str) and status.strip().lower() in _FAILURE_STATUS:
        return f"tool reported status='{status}'"
    return None


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
    # Capability scope: when set (not None), a *mutating* tool whose name is not
    # in this list is structurally refused before any policy/execution — the
    # active workflow's ``allowed_tools`` bound. ``None`` means "unscoped" (the
    # legacy/global chat agent), an empty list means "no mutating tools allowed".
    allowed_tools: Optional[List[str]] = None
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
        # Distinguish a real policy outcome from an *infrastructure* failure:
        # OPA being unreachable, a transport error, or the async client being
        # misused across event loops is NOT the same as a deliberate "deny".
        # Conflating them means a transient OPA blip permanently hard-fails a
        # workflow step (mislabeled as a policy denial). We flag infra failures
        # so the executor can surface them as RETRYABLE instead (see ``run``).
        from app.core.exceptions import RetryableError

        infra_error: Optional[Exception] = None
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
        except RetryableError as e:
            # OPA client already classified this as transport/transient.
            infra_error = e
            logger.warning("ToolExecutor: OPA unavailable for '%s': %s", tool.name, e)
        except Exception as e:
            if _is_opa_infra_error(e):
                infra_error = e
            logger.warning("ToolExecutor: OPA evaluation failed for '%s': %s", tool.name, e)

        if settings.AGENT_TOOL_OPA_ENFORCE:
            if infra_error is not None:
                # Transient: not a denial. ``run`` turns this into a RetryableError.
                return {
                    "allow": False,
                    "requires_approval": False,
                    "approval_type": "",
                    "opa_unavailable": True,
                    "retryable": True,
                    "reason": (
                        "OPA policy service unavailable (infra/transport error), "
                        f"not a policy denial: {infra_error}"
                    ),
                }
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
        #
        #    ``args`` is untrusted: it is the model's parsed tool-call arguments
        #    (runner) or an external MCP client's payload. Injected identity keys
        #    are dropped from it unconditionally rather than merely overwritten,
        #    because the writes below are conditional — ``ctx.user_identity`` is
        #    empty whenever the MCP request carries no forwarded-email header. In
        #    that case a caller-supplied ``_user_email`` would otherwise survive
        #    into the call and impersonate another user to every tool that scopes
        #    its reads by it.
        call_args = {k: v for k, v in args.items() if k not in _INJECTED_KEYS}
        call_args.update(ctx.injected_args)
        if ctx.obo_token:
            call_args["_obo_token"] = ctx.obo_token
        if ctx.user_identity:
            call_args["_user_email"] = ctx.user_identity.get("email")
            call_args["_user_roles"] = ctx.user_identity.get("roles")
            call_args["_user_entitlements"] = ctx.user_identity.get("entitlements")
        # Workflow steps carry the request id as the executor scope. Inject it so
        # tools that need to persist to / read from the originating request (e.g.
        # the enforcement sentinel writing violations back to state_context) can
        # find it without every graph spec having to thread it through args.
        if ctx.scope_id:
            call_args["_request_id"] = ctx.scope_id

        # Args the model actually supplied (for validation + policy + audit),
        # excluding injected identity/context keys.
        model_args = {k: v for k, v in call_args.items() if k not in _INJECTED_KEYS}

        # 2. Validate model-supplied args against the tool's schema (non-fatal in
        #    shadow mode: log and continue so we don't regress current behavior).
        self._validate_args(tool, model_args)

        # 2b. Capability scope (structural bound, runs before policy). A workflow
        #     declares which tools it may use; a mutating tool outside that set
        #     is refused regardless of OPA — bounding blast radius first.
        if tool.is_mutating and ctx.allowed_tools is not None and tool.name not in ctx.allowed_tools:
            refusal = {
                "error": (
                    f"Tool '{tool.name}' is not in the active workflow's capability scope "
                    f"(allowed_tools). Refusing out-of-scope mutating call."
                ),
                "out_of_scope": True,
            }
            logger.warning(
                "[capability-scope] refused out-of-scope mutating tool '%s' (allowed=%s)",
                tool.name, ctx.allowed_tools,
            )
            self._audit(tool, ctx, ok=False, decision=None, error=refusal["error"])
            return refusal

        # 3 + 4. Policy pre-flight for mutating tools.
        decision: Optional[Dict[str, Any]] = None
        if tool.is_mutating:
            decision = await self._evaluate_policy(tool, model_args, ctx)
            # Infra/transport OPA failure (not a real deny): surface as a
            # *retryable* error so a transient OPA blip / restart is retried by
            # the poller instead of permanently failing the step, and the chat
            # path shows a transient error rather than a bogus "policy denied".
            if decision.get("opa_unavailable"):
                from app.core.exceptions import RetryableError
                self._audit(tool, ctx, ok=False, decision=decision,
                            error="OPA policy service unavailable (infra); retryable")
                raise RetryableError(
                    decision.get("reason") or "OPA policy service unavailable"
                )
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

        # 6b. False-success detection. A 200/dict result can still mean the
        #     operation failed (e.g. an external MCP call). Surface it as an
        #     error-shaped result: the agent loop adapts as it does for any failed
        #     tool, and the workflow step node (which re-checks) halts instead of
        #     writing a success_fact and advancing.
        failure_reason = is_tool_failure(result, getattr(tool, "success_predicate", None))
        if failure_reason:
            self._audit(tool, ctx, ok=False, decision=decision, error=failure_reason)
            logger.warning(
                "ToolExecutor: tool '%s' returned a false-success result: %s",
                tool.name, failure_reason,
            )
            return {
                "error": f"Tool '{tool.name}' did not succeed: {failure_reason}",
                "tool_failed": True,
                "result": _audit_safe(result),
            }

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


def _is_opa_infra_error(exc: Exception) -> bool:
    """True if ``exc`` is an OPA *infrastructure* failure rather than a real deny.

    Transport problems (connection refused/reset, timeouts) and async client
    misuse across event loops (``...is bound to a different event loop``) are
    operational, not governance decisions. Treating them distinctly lets the
    executor retry them rather than permanently failing a step as if denied.
    """
    try:
        import httpx
        if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
            return True
    except Exception:  # noqa: BLE001 - httpx always present; never let this wedge
        pass
    msg = str(exc).lower()
    return (
        "different event loop" in msg
        or "event loop is closed" in msg
        or "connection" in msg
        or "timed out" in msg
        or "timeout" in msg
    )


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
