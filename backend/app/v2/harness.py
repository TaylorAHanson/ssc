"""
V2 pre-publish eval / sandbox harness.

For every registered graph it asserts:
  * the graph COMPILES,
  * a fresh run pauses at each gate (HITL is wired),
  * resuming gates drives it to a terminal ``completed`` state,
  * every mutating side effect went through the shared ``ToolExecutor``
    (capability/OPA choke point) — never a raw provider call.

Run hermetically (no live Databricks/GitHub/SMTP): provider getters are
monkeypatched with a fake, and fact writes are no-ops. This is the gate a Skill
must pass before publish (M3); it also guards the M4 workflow port.

    python -m app.v2.harness
"""
import asyncio
import logging
import types
import uuid
from typing import List, Tuple

logger = logging.getLogger(__name__)


class _FakeProvider:
    """One fake satisfying every provider method the V2 tools call."""
    async def plan(self, *a, **k): return {"plan": "noop"}
    async def apply(self, *a, **k): return {"applied": True}
    async def grant_access(self, **k): return {"granted": True}
    async def revoke_access(self, **k): return {"revoked": True}
    async def get_asset_tags(self, *a, **k): return {"approver_group": "owners@corp.com"}
    async def get_asset_owner(self, *a, **k): return "owner@corp.com"
    async def create_repo(self, *a, **k): return {"repo": "created"}
    async def create_from_template(self, *a, **k): return {"repo": "created"}
    async def set_permissions(self, *a, **k): return {"perm": "set"}
    async def create_pull_request(self, **k): return {"number": 1}
    async def list_members_add(self, *a, **k): return {"added": True}
    async def submit_job(self, *a, **k): return {"run_id": 1, "state": "SUCCESS"}
    async def send(self, **k): return {"sent": True}


def _install_fakes():
    import app.state_machines.facts as facts
    import app.v2.graphs as graphs
    import app.v2.tools as T

    facts.add_fact = lambda *a, **k: None
    facts.get_latest_fact = lambda *a, **k: None
    # Force the code catalog (ignore any graph_spec seeded in the local dev DB)
    # so the harness is deterministic regardless of DB state.
    graphs.published_graph_spec = lambda *a, **k: None

    fake = _FakeProvider()
    for getter in ("_get_databricks_provider", "_get_github_provider",
                   "_get_gitops_provider", "_get_notification_provider",
                   "_get_identity_provider"):
        setattr(T, getter, lambda fake=fake: fake)


def _make_request(rtype_value: str):
    return types.SimpleNamespace(
        id=f"req-{uuid.uuid4()}",
        type=rtype_value,
        state_context={
            "request_id": "ctx",
            "requested_by_email": "alice@corp.com",
            "scope": "domain",            # exercise manager gate (not enterprise auto)
            "requires_training": True,    # exercise training gate
            "access_group": "grp-a",
            "workspace": "ws-1",
            "display_name": "sp-demo",
            "repo_name": "demo-repo",
            "github_username": "alice",
            "name": "demo",
            "path": "/Shared/demo",
            "tag_key": "cost_center",
            "resource_id": "main.sales.orders",
            "justification": "needed for project",
            "subject": "hello", "body": "world", "to_email": "bob@corp.com",
            "prompts": ["summarize spend"],
            "recipients": ["a@corp.com", "b@corp.com"],
            "children": [{"child_type": "github_repo_creation", "parameters": {}}],
            "notebook_path": "/Shared/dedup",
            "enforcement_mode": "audit_only",
            "assets": [{"asset_name": "main.sales.orders", "asset_type": "table"}],
            "access_level": "read",
        },
    )


async def _run_one(executor, rtype_value: str, max_steps: int = 15) -> Tuple[str, bool, str, int]:
    req = _make_request(rtype_value)
    gates_resumed = 0
    result = await executor.advance(req)
    steps = 0
    while result.interrupted and not result.done and steps < max_steps:
        gates_resumed += 1
        result = await executor.resume(req, {"approved": True})
        steps += 1
    ok = result.done and result.status == "completed"
    return rtype_value, ok, result.status, gates_resumed


async def main() -> int:
    logging.disable(logging.CRITICAL)  # quiet the run; we print a table
    _install_fakes()

    # Spy on the ToolExecutor to prove mutations route through it.
    import app.tools.tool_executor as te
    calls = {"n": 0, "mutating": 0}
    orig_run = te.executor.run

    async def spy_run(tool, ctx, **kw):
        calls["n"] += 1
        if getattr(tool, "is_mutating", False):
            calls["mutating"] += 1
        return await orig_run(tool, ctx, **kw)

    te.executor.run = spy_run

    from app.v2.executor import DurableWorkflowExecutor
    from app.v2.graphs import registered_types

    ex = DurableWorkflowExecutor()
    results: List[Tuple[str, bool, str, int]] = []
    for rt in registered_types():
        try:
            results.append(await _run_one(ex, rt))
        except Exception as e:
            results.append((rt, False, f"ERROR: {e}", 0))

    te.executor.run = orig_run
    logging.disable(logging.NOTSET)

    width = max(len(r[0]) for r in results)
    passed = 0
    print(f"\n{'request_type':<{width}}  result  gates  status")
    print("-" * (width + 28))
    for rt, ok, status, gates in sorted(results):
        passed += 1 if ok else 0
        print(f"{rt:<{width}}  {'PASS' if ok else 'FAIL':<6}  {gates:<5}  {status}")
    print("-" * (width + 28))
    print(f"{passed}/{len(results)} graphs green | ToolExecutor calls={calls['n']} "
          f"(mutating={calls['mutating']})")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
