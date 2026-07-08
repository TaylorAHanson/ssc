"""Unit tests for the agent's workflow-authoring tools.

These wrap WorkflowService + the spec loader + dry-run, so they verify the same
guarantees the visual editor / publish endpoint rely on: validation rejects bad
specs, preview is side-effect free, drafts don't go live, and publish runs the
pre-publish gate. Role gating is handled by the chat endpoint (required_role) and
covered separately; here we exercise behavior with the tools' DB pointed at the
test session.
"""
import pytest

from app.core.config import settings as app_settings
from app.services.workflow_service import WorkflowService
from app.tools.authoring import workflow_authoring as wa


def _valid_spec():
    return {
        "name": "demo_flow",
        "complete_fact": "done",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager"},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "approvals": ["manager"], "args": {
                 "subject": {"$literal": "hi"},
                 "body": {"$literal": "body"},
                 "to_email": {"$literal": "u@corp.com"},
             }},
        ],
    }


@pytest.fixture
def patched_db(db_session, monkeypatch):
    """Point the authoring tools at the test session (and don't let them close it)."""
    monkeypatch.setattr(wa, "_db", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    return db_session


@pytest.mark.asyncio
async def test_building_blocks_lists_real_tools_and_gates():
    out = await wa.list_workflow_building_blocks.execute()
    names = {t["name"] for t in out["step_tools"]}
    assert "send_notification" in names
    assert "manager" in out["gate_types"]
    assert any(op.startswith("$var") for op in out["expression_operators"])


@pytest.mark.asyncio
async def test_validate_accepts_good_and_rejects_unknown_tool():
    ok = await wa.validate_workflow_spec.execute(graph_spec=_valid_spec())
    assert ok == {"valid": True, "warnings": []}

    bad = dict(_valid_spec())
    bad["stages"] = [{"kind": "step", "name": "x", "tool": "not_a_real_tool", "args": {}}]
    res = await wa.validate_workflow_spec.execute(graph_spec=bad)
    assert res["valid"] is False and "not_a_real_tool" in res["error"]


@pytest.mark.asyncio
async def test_validate_warns_on_unknown_and_missing_tool_args():
    """Structurally valid but the tool would silently drop wrong args (**kwargs)."""
    spec = {
        "name": "wf", "complete_fact": "done",
        "stages": [
            {"kind": "gate", "name": "g", "type": "manager"},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "approvals": ["manager"], "args": {"to": {"$literal": "x@y"}, "subject": {"$literal": "s"}}},
        ],
    }
    res = await wa.validate_workflow_spec.execute(graph_spec=spec)
    assert res["valid"] is True
    joined = " | ".join(res["warnings"])
    assert "'to' is not accepted" in joined  # wrong name (should be to_email)
    assert "required arg 'body' is not set" in joined


@pytest.mark.asyncio
async def test_preview_is_side_effect_free_projection():
    out = await wa.preview_workflow_spec.execute(
        graph_spec=_valid_spec(), sample_context={"requested_by_email": "u@corp.com"}
    )
    assert out["ok"] is True
    stage_names = [s["name"] for s in out["projection"]["stages"]]
    assert stage_names == ["manager_approval", "notify"]


@pytest.mark.asyncio
async def test_save_draft_creates_then_updates_and_does_not_publish(patched_db):
    res = await wa.save_workflow_draft.execute(
        key="demo_flow", graph_spec=_valid_spec(), name="Demo Flow",
        request_type="simple_email", _user_email="admin@example.com",
    )
    assert res["ok"] is True and res["action"] == "created"
    assert res["status"] == "draft"

    # Not live until published.
    assert "demo_flow" not in {s.key for s in WorkflowService.list_published(patched_db)}

    # Re-saving updates in place (still a draft).
    res2 = await wa.save_workflow_draft.execute(key="demo_flow", graph_spec=_valid_spec())
    assert res2["action"] == "updated" and res2["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t "])
async def test_save_draft_autogenerates_instructions_when_blank(patched_db, blank):
    """A blank/omitted instructions_markdown must fall back to the generated baseline.

    Agents frequently pass "" for optional fields; that must not defeat the
    "instructions are never empty" safety net (the bug behind a blank Details page).
    """
    res = await wa.save_workflow_draft.execute(
        key="blank_instr", graph_spec=_valid_spec(), instructions_markdown=blank
    )
    assert res["ok"] is True
    workflow = WorkflowService.get_by_key(patched_db, "blank_instr")
    assert workflow.instructions_markdown
    assert workflow.instructions_markdown.strip()
    assert "Instructions" in workflow.instructions_markdown


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_save_draft_flags_auto_generated_instructions(patched_db, blank):
    """Omitting/blanking instructions must surface an explicit, actionable signal
    (not a silent ok) so the agent knows it left a thin baseline and re-authors."""
    res = await wa.save_workflow_draft.execute(
        key="autogen_signal", graph_spec=_valid_spec(), instructions_markdown=blank
    )
    assert res["ok"] is True
    assert res["instructions_auto_generated"] is True
    assert res["instructions_source"] == "auto_baseline"
    assert any("instructions_markdown was NOT provided" in w for w in res["warnings"])


@pytest.mark.asyncio
async def test_save_draft_marks_authored_instructions(patched_db):
    """A real playbook is reported as authored with no auto-generation warning."""
    res = await wa.save_workflow_draft.execute(
        key="authored_signal", graph_spec=_valid_spec(),
        instructions_markdown="# Real playbook\nGather X then Y.",
    )
    assert res["ok"] is True
    assert res["instructions_auto_generated"] is False
    assert res["instructions_source"] == "authored"
    assert not any("auto-generated" in w.lower() for w in res["warnings"])


@pytest.mark.asyncio
async def test_save_draft_keeps_explicit_instructions_and_appends_execution(patched_db):
    """The author's prose is preserved; the canonical Execution block is spliced in."""
    res = await wa.save_workflow_draft.execute(
        key="explicit_instr", graph_spec=_valid_spec(),
        instructions_markdown="# My custom guidance\nGather X then Y.",
    )
    assert res["ok"] is True
    workflow = WorkflowService.get_by_key(patched_db, "explicit_instr")
    assert workflow.instructions_markdown.startswith("# My custom guidance\nGather X then Y.")
    # The execute_workflow call is persisted (not just added at serve time).
    assert workflow.instructions_markdown.count("## Execution") == 1
    assert "execute_workflow" in workflow.instructions_markdown


@pytest.mark.asyncio
async def test_save_draft_blank_does_not_clobber_existing_instructions(patched_db):
    """Re-saving with blank instructions preserves previously-set prose."""
    await wa.save_workflow_draft.execute(
        key="keep_instr", graph_spec=_valid_spec(),
        instructions_markdown="# Keep me",
    )
    await wa.save_workflow_draft.execute(
        key="keep_instr", graph_spec=_valid_spec(), instructions_markdown="",
    )
    workflow = WorkflowService.get_by_key(patched_db, "keep_instr")
    assert workflow.instructions_markdown.startswith("# Keep me")
    # Still exactly one Execution block after a blank re-save (idempotent splice).
    assert workflow.instructions_markdown.count("## Execution") == 1


@pytest.mark.asyncio
async def test_save_draft_rejects_invalid_spec(patched_db):
    bad = {"name": "x", "stages": [{"kind": "step", "name": "s", "tool": "nope", "args": {}}]}
    res = await wa.save_workflow_draft.execute(key="bad_flow", graph_spec=bad)
    assert res["ok"] is False
    assert WorkflowService.get_by_key(patched_db, "bad_flow") is None


@pytest.mark.asyncio
async def test_publish_requires_request_type_then_publishes(patched_db):
    # Draft without a request_type can't be published.
    await wa.save_workflow_draft.execute(key="pub_flow", graph_spec=_valid_spec())
    blocked = await wa.publish_workflow.execute(key="pub_flow")
    assert blocked["ok"] is False and "request_type" in blocked["error"]

    # Add a request_type, then publish succeeds and bumps the version.
    await wa.save_workflow_draft.execute(
        key="pub_flow", graph_spec=_valid_spec(), request_type="simple_email"
    )
    ok = await wa.publish_workflow.execute(key="pub_flow", _user_email="admin@example.com")
    assert ok["ok"] is True and ok["status"] == "published"
    assert "pub_flow" in {s.key for s in WorkflowService.list_published(patched_db)}


@pytest.mark.asyncio
async def test_get_workflow_returns_spec_and_instructions_for_editing(patched_db):
    await wa.save_workflow_draft.execute(key="g_flow", graph_spec=_valid_spec())
    got = await wa.get_workflow.execute(key="g_flow")
    assert got["found"] is True and got["graph_spec"]["name"] == "demo_flow"
    # Editing must build on the existing instructions, so they're returned (and
    # carry the auto-generated baseline, never blank).
    assert "instructions_markdown" in got
    assert got["instructions_markdown"] and got["instructions_markdown"].strip()
    assert "allowed_tools" in got and "policy_ref" in got

    missing = await wa.get_workflow.execute(key="does_not_exist")
    assert missing["found"] is False and "available_keys" in missing


@pytest.mark.asyncio
async def test_write_tools_refuse_when_authoring_locked(patched_db, monkeypatch):
    """In a locked environment (e.g. prod) the write tools refuse and write nothing."""
    monkeypatch.setattr(app_settings, "WORKFLOW_AUTHORING_LOCKED", True)

    saved = await wa.save_workflow_draft.execute(
        key="locked_flow", graph_spec=_valid_spec(), request_type="simple_email"
    )
    assert saved["ok"] is False and saved.get("locked") is True
    # Nothing was persisted.
    assert WorkflowService.get_by_key(patched_db, "locked_flow") is None

    published = await wa.publish_workflow.execute(key="anything")
    assert published["ok"] is False and published.get("locked") is True


@pytest.mark.asyncio
async def test_read_tools_still_work_when_locked(patched_db, monkeypatch):
    """Inspection/validation/preview remain available even when locked."""
    monkeypatch.setattr(app_settings, "WORKFLOW_AUTHORING_LOCKED", True)
    assert (await wa.validate_workflow_spec.execute(graph_spec=_valid_spec())) == {"valid": True, "warnings": []}
    preview = await wa.preview_workflow_spec.execute(graph_spec=_valid_spec(), sample_context={})
    assert preview["ok"] is True
