"""Unit tests for the V2 ToolExecutor — the single governed choke point that
both the agent runner and the MCP server route every tool call through.

Verifies: identity injection, read vs. mutating classification, shadow-mode
(non-blocking) policy posture, and that a tool's result is returned verbatim.
"""
import pytest

from app.tools.tool_executor import ToolContext, ToolExecutor


class _FakeTool:
    def __init__(self, name, *, is_mutating, result, side_effect_class="read"):
        self.name = name
        self.is_mutating = is_mutating
        self.side_effect_class = side_effect_class
        self.policy_ref = None
        self._result = result
        self._args_schema = None
        self.received_kwargs = None

    async def execute(self, **kwargs):
        self.received_kwargs = kwargs
        return self._result


@pytest.mark.asyncio
async def test_read_tool_executes_and_returns_result():
    tool = _FakeTool("lookup", is_mutating=False, result={"ok": True})
    ctx = ToolContext(tool_call_id="tc-1", user_identity={"email": "u@corp.com"})
    out = await ToolExecutor().run(tool, ctx, q="x")
    assert out == {"ok": True}
    # The model-supplied arg reaches the tool...
    assert tool.received_kwargs["q"] == "x"
    # ...and identity context is injected for OBO-aware tools.
    assert tool.received_kwargs["_user_email"] == "u@corp.com"


@pytest.mark.asyncio
async def test_mutating_tool_runs_in_shadow_mode(monkeypatch):
    """Default posture is shadow: policy is evaluated/logged but never blocks."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AGENT_TOOL_OPA_ENFORCE", False, raising=False)

    tool = _FakeTool("grant", is_mutating=True, result={"granted": True},
                     side_effect_class="mutate")
    executor = ToolExecutor()
    # Avoid reaching for a live OPA server in the unit test; shadow mode should
    # tolerate a missing decision and still run.
    async def _fake_policy(t, args, c):
        return {"allow": True, "requires_approval": False, "approval_type": "", "reason": "test"}
    monkeypatch.setattr(executor, "_evaluate_policy", _fake_policy)

    ctx = ToolContext(tool_call_id="tc-2", user_identity={"email": "u@corp.com"})
    out = await executor.run(tool, ctx, target="grp")
    assert out == {"granted": True}


@pytest.mark.asyncio
async def test_enforce_mode_blocks_unapproved_mutation(monkeypatch):
    """In enforce mode a requires-approval decision halts the call with a refusal."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AGENT_TOOL_OPA_ENFORCE", True, raising=False)

    tool = _FakeTool("grant", is_mutating=True, result={"granted": True},
                     side_effect_class="mutate")
    executor = ToolExecutor()

    async def _needs_approval(t, args, c):
        return {"allow": True, "requires_approval": True,
                "approval_type": "manager", "reason": "needs manager"}
    monkeypatch.setattr(executor, "_evaluate_policy", _needs_approval)

    ctx = ToolContext(tool_call_id="tc-3", user_identity={"email": "u@corp.com"})
    out = await executor.run(tool, ctx, target="grp")
    # Refusal dict (not the tool result) and the tool never executed.
    assert out.get("requires_approval") is True
    assert "approval" in out.get("error", "").lower()
    assert tool.received_kwargs is None

    # Supplying the satisfied approval lets it through.
    ctx_ok = ToolContext(tool_call_id="tc-3", user_identity={"email": "u@corp.com"},
                         approvals=["manager"])
    out_ok = await executor.run(tool, ctx_ok, target="grp")
    assert out_ok == {"granted": True}


@pytest.mark.asyncio
async def test_capability_scope_refuses_out_of_scope_mutating_tool(monkeypatch):
    """A mutating tool outside the active workflow's allowed_tools is refused
    structurally, before policy/execution. Reads and in-scope tools pass."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AGENT_TOOL_OPA_ENFORCE", False, raising=False)

    executor = ToolExecutor()

    async def _allow(t, args, c):
        return {"allow": True, "requires_approval": False, "approval_type": "", "reason": "ok"}
    monkeypatch.setattr(executor, "_evaluate_policy", _allow)

    grant = _FakeTool("grant", is_mutating=True, result={"granted": True}, side_effect_class="data_grant")

    # Out of scope -> refused, tool never runs.
    ctx_oos = ToolContext(tool_call_id="t1", user_identity={"email": "u@corp.com"},
                          allowed_tools=["send_notification"])
    out = await executor.run(grant, ctx_oos, target="grp")
    assert out.get("out_of_scope") is True
    assert grant.received_kwargs is None

    # In scope -> executes.
    ctx_ok = ToolContext(tool_call_id="t2", user_identity={"email": "u@corp.com"},
                         allowed_tools=["grant", "send_notification"])
    assert await executor.run(grant, ctx_ok, target="grp") == {"granted": True}

    # Unscoped (None) -> no capability restriction (legacy global agent).
    ctx_unscoped = ToolContext(tool_call_id="t3", user_identity={"email": "u@corp.com"})
    assert await executor.run(grant, ctx_unscoped, target="grp") == {"granted": True}

    # Reads are never capability-blocked (info gathering stays broad).
    read = _FakeTool("lookup", is_mutating=False, result={"ok": True})
    ctx_read = ToolContext(tool_call_id="t4", user_identity={"email": "u@corp.com"},
                           allowed_tools=["grant"])
    assert await executor.run(read, ctx_read, q="x") == {"ok": True}


# --- Dry run (workflow test sandbox) ---------------------------------------
#
# A workflow test case starts the REAL agent, so the only thing standing between
# "verify this workflow behaves" and a real grant/provision is this flag. It is
# enforced at the executor rather than per tool precisely so no individual tool
# can forget about it.


@pytest.mark.asyncio
async def test_dry_run_simulates_mutating_tools_without_executing_them(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "AGENT_TOOL_OPA_ENFORCE", False, raising=False)

    tool = _FakeTool("grant_access", is_mutating=True, result={"granted": True},
                     side_effect_class="data_grant")
    executor = ToolExecutor()

    async def _allow(t, args, c):
        return {"allow": True, "requires_approval": False, "approval_type": "", "reason": "ok"}
    monkeypatch.setattr(executor, "_evaluate_policy", _allow)

    ctx = ToolContext(tool_call_id="dr-1", user_identity={"email": "u@corp.com"},
                      dry_run=True)
    out = await executor.run(tool, ctx, target="grp")

    # Nothing ran, and the agent still gets a plausible success so the
    # conversation continues far enough to be judged.
    assert tool.received_kwargs is None
    assert out.get("ok") is True
    assert out.get("dry_run") is True
    assert out.get("status") == "simulated"
    # The arguments it *would* have used are kept so the judge can check them.
    assert out.get("args", {}).get("target") == "grp"


@pytest.mark.asyncio
async def test_dry_run_still_executes_read_tools():
    """Reads have to work or a test transcript is meaningless — the agent could
    never look anything up to answer with."""
    tool = _FakeTool("lookup", is_mutating=False, result={"rows": [1, 2]})
    ctx = ToolContext(tool_call_id="dr-2", user_identity={"email": "u@corp.com"},
                      dry_run=True)
    out = await ToolExecutor().run(tool, ctx, q="x")
    assert out == {"rows": [1, 2]}
    assert tool.received_kwargs["q"] == "x"


def test_dry_run_does_not_write_audit_facts(monkeypatch):
    """A sandboxed run must not leave governance history behind, or the audit
    trail fills with things that never happened."""
    written: list = []
    monkeypatch.setattr(
        "app.state_machines.facts.add_fact",
        lambda *a, **k: written.append(a),
    )

    tool = _FakeTool("grant_access", is_mutating=True, result={"granted": True},
                     side_effect_class="data_grant")
    executor = ToolExecutor()
    # A db + scope is what makes _audit actually persist, so the flag is the only
    # thing suppressing the write here.
    ctx = ToolContext(tool_call_id="dr-3", user_identity={"email": "u@corp.com"},
                      db=object(), scope_id="req-1", dry_run=True)
    executor._audit(tool, ctx, ok=True, decision=None, result={"granted": True})
    assert written == []

    live = ToolContext(tool_call_id="dr-4", user_identity={"email": "u@corp.com"},
                       db=object(), scope_id="req-1")
    executor._audit(tool, live, ok=True, decision=None, result={"granted": True})
    assert len(written) == 1, "a non-dry-run call should still be audited"


# --- Identity spoofing -----------------------------------------------------
#
# Many tools scope their reads by the injected ``_user_email`` (the user-context
# profile, approvals, entitlements). ``args`` is untrusted — it is the model's
# parsed tool-call arguments, or an external MCP client's payload — so it must
# never be able to supply those keys itself.


@pytest.mark.asyncio
async def test_caller_cannot_override_the_injected_identity():
    tool = _FakeTool("lookup", is_mutating=False, result={"ok": True})
    ctx = ToolContext(tool_call_id="tc-1", user_identity={"email": "real@corp.com"})

    await ToolExecutor().run(tool, ctx, q="x", _user_email="victim@corp.com")

    assert tool.received_kwargs["_user_email"] == "real@corp.com"


@pytest.mark.asyncio
async def test_caller_supplied_identity_is_dropped_when_the_context_has_none():
    """The dangerous case: nothing overwrites the spoofed value.

    ``ToolContext.user_identity`` is empty whenever an MCP request arrives with no
    forwarded-email header, so the executor's identity writes are skipped. A
    spoofed ``_user_email`` must be stripped rather than left to pass through and
    impersonate somebody — the receiving tool has no way to tell it apart from a
    real injection.
    """
    tool = _FakeTool("lookup", is_mutating=False, result={"ok": True})
    ctx = ToolContext(tool_call_id="tc-1", user_identity={})

    await ToolExecutor().run(
        tool,
        ctx,
        q="x",
        _user_email="victim@corp.com",
        _user_roles="Platform Admin",
        _user_entitlements="admins",
        _obo_token="stolen",
    )

    for key in ("_user_email", "_user_roles", "_user_entitlements", "_obo_token"):
        assert key not in tool.received_kwargs, f"{key} was not stripped"
    assert tool.received_kwargs["q"] == "x"
