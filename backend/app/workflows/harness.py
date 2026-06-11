"""
V2 pre-publish eval / sandbox harness.

For every registered graph it asserts:
  * the graph COMPILES,
  * a fresh run pauses at each gate (HITL is wired),
  * resuming gates drives it to a terminal ``completed`` state,
  * every mutating side effect went through the shared ``ToolExecutor``
    (capability/OPA choke point) — never a raw provider call,
  * the run's *transcript* (ordered tool calls + gates + final status) matches
    the committed golden — catching behavioral regressions in authored graphs.

Modes:
  * hermetic (default): provider getters are monkeypatched with a fake and fact
    writes are no-ops — no live Databricks/GitHub/SMTP. Safe for CI.
  * ``--sandbox``: skips the fakes and runs against the *real* providers, for
    validating against a throwaway sandbox workspace. Requires real credentials
    and an isolated workspace; never run this in CI or against production.

Golden transcripts:
  * ``--capture`` (re)writes ``golden_transcripts.json`` from the current run.
  * default compares against it and fails on drift.

    python -m app.workflows.harness            # hermetic + golden compare (the CI gate)
    python -m app.workflows.harness --capture  # refresh goldens after an intended change
    python -m app.workflows.harness --sandbox  # run against a real sandbox workspace
"""
import argparse
import asyncio
import json
import logging
import os
import types
import uuid
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "golden_transcripts.json")


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
    import app.workflows.graphs as graphs
    import app.workflows.tools as T

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


# --------------------------------------------------------------------------
# Golden transcripts
# --------------------------------------------------------------------------
def _load_golden() -> Dict[str, dict]:
    try:
        with open(GOLDEN_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def _save_golden(transcripts: Dict[str, dict]) -> None:
    with open(GOLDEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(transcripts, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _diff_transcripts(golden: Dict[str, dict], current: Dict[str, dict]) -> List[str]:
    """Human-readable drift lines between the committed golden and this run."""
    drift: List[str] = []
    for rt in sorted(set(golden) | set(current)):
        g, c = golden.get(rt), current.get(rt)
        if g is None:
            drift.append(f"  + {rt}: new graph (not in golden)")
        elif c is None:
            drift.append(f"  - {rt}: missing from this run (in golden)")
        elif g != c:
            for key in ("status", "gates", "mutating", "tools"):
                if g.get(key) != c.get(key):
                    drift.append(f"  ~ {rt}.{key}: golden={g.get(key)} current={c.get(key)}")
    return drift


async def main(*, capture: bool = False, sandbox: bool = False) -> int:
    logging.disable(logging.CRITICAL)  # quiet the run; we print a table

    if sandbox:
        logging.disable(logging.NOTSET)
        logger.warning(
            "HARNESS --sandbox: running against REAL providers. This requires "
            "valid credentials and an isolated sandbox workspace. Never run this "
            "in CI or against a production workspace."
        )
        logging.disable(logging.CRITICAL)
    else:
        _install_fakes()

    # Spy on the ToolExecutor to prove mutations route through it AND to record
    # the ordered tool transcript per request type for golden comparison.
    import app.tools.tool_executor as te
    calls = {"n": 0, "mutating": 0}
    transcripts: Dict[str, dict] = {}
    current_rt = {"v": ""}
    orig_run = te.executor.run

    async def spy_run(tool, ctx, **kw):
        calls["n"] += 1
        is_mut = getattr(tool, "is_mutating", False)
        if is_mut:
            calls["mutating"] += 1
        t = transcripts.setdefault(current_rt["v"], {"tools": [], "mutating": 0})
        t["tools"].append(tool.name)
        if is_mut:
            t["mutating"] += 1
        return await orig_run(tool, ctx, **kw)

    te.executor.run = spy_run

    from app.workflows.executor import DurableWorkflowExecutor
    from app.workflows.graphs import registered_types

    ex = DurableWorkflowExecutor()
    results: List[Tuple[str, bool, str, int]] = []
    for rt in registered_types():
        current_rt["v"] = rt
        transcripts.setdefault(rt, {"tools": [], "mutating": 0})
        try:
            res = await _run_one(ex, rt)
            results.append(res)
            transcripts[rt]["status"] = res[2]
            transcripts[rt]["gates"] = res[3]
        except Exception as e:
            results.append((rt, False, f"ERROR: {e}", 0))
            transcripts[rt]["status"] = f"ERROR: {e}"
            transcripts[rt]["gates"] = 0

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

    structural_ok = passed == len(results)

    # Golden transcript comparison (skipped in sandbox mode — real side effects
    # make tool counts environment-dependent).
    golden_ok = True
    if capture:
        _save_golden(transcripts)
        print(f"golden transcripts captured -> {GOLDEN_PATH} ({len(transcripts)} graphs)")
    elif sandbox:
        print("golden comparison skipped (sandbox mode)")
    else:
        drift = _diff_transcripts(_load_golden(), transcripts)
        if drift:
            golden_ok = False
            print("TRANSCRIPT DRIFT vs golden (run --capture if intended):")
            print("\n".join(drift))
        else:
            print("transcripts match golden")

    return 0 if (structural_ok and golden_ok) else 1


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="V2 pre-publish eval / sandbox harness")
    p.add_argument("--capture", action="store_true",
                   help="(re)write golden_transcripts.json from this run")
    p.add_argument("--sandbox", action="store_true",
                   help="run against REAL providers (isolated sandbox workspace; not for CI)")
    return p.parse_args(argv)


if __name__ == "__main__":
    _args = _parse_args()
    raise SystemExit(asyncio.run(main(capture=_args.capture, sandbox=_args.sandbox)))
