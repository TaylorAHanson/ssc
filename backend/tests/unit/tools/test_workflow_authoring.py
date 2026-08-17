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
async def test_saving_over_a_published_workflow_is_refused_until_confirmed(patched_db):
    """A "draft" save demotes the row, which pulls a live workflow off the
    Capabilities menu. The agent saves without asking permission because a draft is
    supposed to be inert, so this one case has to ask — otherwise "add a field to
    the intake questions" is an outage nobody agreed to."""
    await wa.save_workflow_draft.execute(
        key="live_flow", graph_spec=_valid_spec(), request_type="simple_email",
        instructions_markdown="## Information to Gather\n1. thing — a thing.\n",
    )
    await wa.publish_workflow.execute(key="live_flow")
    published = WorkflowService.get_by_key(patched_db, "live_flow")
    assert published.status == "published"

    res = await wa.save_workflow_draft.execute(
        key="live_flow",
        instructions_markdown="## Information to Gather\n1. thing — a thing.\n2. more.\n",
    )
    assert res["ok"] is False
    assert res["requires_confirmation"] is True
    patched_db.refresh(published)
    assert published.status == "published"  # still live, and nothing was written

    # With the admin's explicit go-ahead it saves, and says what it cost.
    res2 = await wa.save_workflow_draft.execute(
        key="live_flow",
        instructions_markdown="## Information to Gather\n1. thing — a thing.\n2. more.\n",
        take_offline=True,
    )
    assert res2["ok"] is True and res2["took_offline"] is True
    assert any("WAS PUBLISHED" in w for w in res2["warnings"])
    patched_db.refresh(published)
    assert published.status == "draft"


@pytest.mark.asyncio
async def test_saving_a_draft_over_a_draft_never_asks(patched_db):
    """The guard is scoped to live workflows: normal iteration stays a single step."""
    await wa.save_workflow_draft.execute(
        key="wip_flow", graph_spec=_valid_spec(), request_type="simple_email",
    )
    res = await wa.save_workflow_draft.execute(key="wip_flow", goal="Something clearer.")
    assert res["ok"] is True and res.get("requires_confirmation") is None
    assert res["took_offline"] is False


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


@pytest.mark.asyncio
async def test_an_update_may_omit_the_graph_and_keep_the_stored_one(patched_db):
    """A follow-up save that only revises the playbook shouldn't have to resend the
    whole graph. Requiring it produced "Field required: graph_spec" on exactly the
    call that was trying to fix the instructions."""
    spec = _valid_spec()
    created = await wa.save_workflow_draft.execute(
        key="ingest_flow", graph_spec=spec, request_type="simple_email",
        goal="Ingest an external source system into the lakehouse for analysts",
        instructions_markdown="## Information to Gather\n1. source_system — the system to ingest.\n",
    )
    assert created["ok"] is True and created["graph_spec_unchanged"] is False

    revised = await wa.save_workflow_draft.execute(
        key="ingest_flow",
        instructions_markdown=(
            "## Information to Gather\n1. source_system — the system to ingest.\n"
            "2. data_classification — Public, Internal, or Confidential.\n"
            "## Validation & Guidance\nRefuse anything Restricted.\n"
            "## Approvals & Flow\nPlatform admin approves, then a manual ingest step.\n"
        ),
    )
    assert revised["ok"] is True
    assert revised["graph_spec_unchanged"] is True

    stored = WorkflowService.get_by_key(patched_db, "ingest_flow")
    assert stored.graph_spec == spec
    assert "data_classification" in stored.instructions_markdown


@pytest.mark.asyncio
async def test_creating_a_workflow_still_requires_the_graph(patched_db):
    """Omitting it is a shortcut for updates only — there's nothing to fall back to
    on a create, and the error has to say which case it is."""
    out = await wa.save_workflow_draft.execute(key="brand_new", request_type="simple_email")
    assert out["ok"] is False
    assert "graph_spec is required" in out["error"]
    assert WorkflowService.get_by_key(patched_db, "brand_new") is None


@pytest.mark.asyncio
async def test_save_scores_the_goal_and_warns_about_a_stub(patched_db):
    """The goal IS the runtime Capabilities line, so a stub is a routing bug the
    agent has to hear about — it used to get a bare ok:True."""
    res = await wa.save_workflow_draft.execute(
        key="campaign_flow", graph_spec=_valid_spec(), request_type="campaign",
        goal="Fulfill a campaign flow request.",
        instructions_markdown="# Campaign\n**Goal**: x\n## Information to Gather\n1. a",
    )
    assert res["ok"] is True
    assert res["goal_quality"]["tier"] == "poor"
    assert res["goal_quality"]["summary"]["is_stub"] is True
    warning = " ".join(res["warnings"])
    assert "Goal quality" in warning and "Capabilities menu" in warning


@pytest.mark.asyncio
async def test_save_warns_when_the_goal_collides_with_a_published_workflow(patched_db):
    """The specific complaint: a new goal that reads like an existing menu line."""
    await wa.save_workflow_draft.execute(
        key="workspace_access", graph_spec=_valid_spec(), request_type="workspace",
        goal="Request access to an existing Databricks workspace.",
    )
    await wa.publish_workflow.execute(key="workspace_access", _user_email="a@b.com")

    res = await wa.save_workflow_draft.execute(
        key="workspace_provision", graph_spec=_valid_spec(), request_type="workspace2",
        goal="Request access to a new Databricks workspace.",
    )
    collisions = [c["key"] for c in res["goal_quality"]["summary"]["collisions"]]
    assert "workspace_access" in collisions
    assert "workspace_access" in " ".join(res["warnings"])


@pytest.mark.asyncio
async def test_save_does_not_warn_on_a_discriminating_goal(patched_db):
    res = await wa.save_workflow_draft.execute(
        key="volume_flow", graph_spec=_valid_spec(), request_type="volume",
        goal=(
            "Create a new Unity Catalog volume for file-based storage in an existing "
            "schema — not table access and not a new schema."
        ),
    )
    assert res["goal_quality"]["score"] == 100
    assert not any("Goal quality" in w for w in res["warnings"])


# --------------------------------------------------------------------------- tests
# The authoring assistant has to be able to READ results and RUN the suite;
# without that it can only guess at failures (and did — it told an admin it
# couldn't see runs it had just proposed).


async def _seeded_workflow_with_test(db, key="tested_flow"):
    from app.services.workflow_test_service import WorkflowTestService

    await wa.save_workflow_draft.execute(
        key=key, graph_spec=_valid_spec(), request_type="simple_email"
    )
    wf = WorkflowService.get_by_key(db, key)
    case = WorkflowTestService.create_test(
        db, wf.id,
        name="asks for justification",
        question="I need access to the sales catalog",
        expected_outcome="Asks for a business justification before submitting",
        source="agent",
    )
    return wf, case


@pytest.mark.asyncio
async def test_saving_tests_accepts_the_field_names_models_actually_guess(patched_db):
    """Observed twice in real turns: the model sent title/input instead of
    name/question, the call was rejected, and the retry cost an iteration out of
    the turn's budget — which is how a design turn ran out of room to RUN them."""
    await wa.save_workflow_draft.execute(
        key="alias_flow", graph_spec=_valid_spec(), request_type="alias"
    )
    res = await wa.save_workflow_tests.execute(
        key="alias_flow",
        cases=[{
            "title": "Happy path",
            "input": "I need to migrate data from SAP ECC.",
            "expected": "Asks for the data classification before submitting.",
        }],
    )
    assert res["ok"] is True and res["saved"] == 1
    assert res["tests"][0]["name"] == "Happy path"
    assert res["tests"][0]["question"].startswith("I need to migrate")
    assert res["tests"][0]["expected_outcome"].startswith("Asks for")


@pytest.mark.asyncio
async def test_saving_tests_directs_the_agent_to_run_them_itself(patched_db):
    """The result used to end with "tell the admin to click Run all" — the last
    thing the model read, so it handed off instead of running the suite it had
    just written."""
    await wa.save_workflow_draft.execute(
        key="handoff_flow", graph_spec=_valid_spec(), request_type="handoff"
    )
    res = await wa.save_workflow_tests.execute(
        key="handoff_flow",
        cases=[{"name": "happy", "question": "q", "expected_outcome": "e"}],
    )
    assert res["next_action"] == "run_workflow_tests"
    assert res["next_action_args"]["test_ids"] == [res["tests"][0]["id"]]
    note = res["note"]
    assert "NOW RUN THEM" in note and "run_workflow_tests" in note
    # Every mention of the admin clicking Run must be a prohibition, not an
    # instruction to hand the job over.
    assert "Do not ask the admin to go click Run" in note
    assert "tell the admin" not in note.lower()


@pytest.mark.asyncio
async def test_list_tests_reports_never_run_as_unknown_not_passing(patched_db):
    _, case = await _seeded_workflow_with_test(patched_db)
    out = await wa.list_workflow_tests.execute(key="tested_flow")

    assert out["ok"] is True
    (entry,) = out["tests"]
    assert entry["id"] == case.id
    assert entry["latest_run"] is None
    assert entry["result"] == "never run"
    # The note is what steers the agent, so it must say to run them rather than
    # letting an unrun suite read as green.
    assert "never been run" in out["note"] and "run_workflow_tests" in out["note"]


@pytest.mark.asyncio
async def test_list_tests_surfaces_verdict_rationale_and_missing(patched_db):
    """A failure is only actionable if the agent can see WHY the judge failed it."""
    from app.db.workflow_test import WorkflowTestRunModel

    wf, case = await _seeded_workflow_with_test(patched_db)
    patched_db.add(WorkflowTestRunModel(
        id="run-1", run_group_id="grp-1", workflow_id=wf.id, test_id=case.id,
        test_name=case.name, question=case.question,
        expected_outcome=case.expected_outcome,
        status="complete", verdict="fail", score=20,
        rationale="Submitted without ever asking for a justification.",
        missing=["business justification"],
        tool_calls=[{"name": "submit_request"}],
        transcript=[{"role": "assistant", "content": "Submitting now."}],
    ))
    patched_db.commit()

    out = await wa.list_workflow_tests.execute(key="tested_flow")
    (entry,) = out["tests"]
    assert entry["result"] == "fail"
    run = entry["latest_run"]
    assert run["verdict"] == "fail" and run["score"] == 20
    assert "justification" in run["rationale"]
    assert run["missing"] == ["business justification"]
    assert run["passed"] is False
    # Transcripts are opt-in so a routine status check stays cheap.
    assert "transcript" not in run

    verbose = await wa.list_workflow_tests.execute(
        key="tested_flow", include_transcripts=True
    )
    assert verbose["tests"][0]["latest_run"]["transcript"]


@pytest.mark.asyncio
async def test_list_tests_trims_long_transcripts(patched_db):
    """Diagnosis must not blow the agent's context on one runaway transcript."""
    from app.db.workflow_test import WorkflowTestRunModel

    wf, case = await _seeded_workflow_with_test(patched_db)
    patched_db.add(WorkflowTestRunModel(
        id="run-big", run_group_id="grp-big", workflow_id=wf.id, test_id=case.id,
        test_name=case.name, question=case.question,
        expected_outcome=case.expected_outcome,
        status="complete", verdict="pass", score=90,
        transcript=[{"role": "assistant", "content": "x" * 5000} for _ in range(40)],
    ))
    patched_db.commit()

    out = await wa.list_workflow_tests.execute(key="tested_flow", include_transcripts=True)
    transcript = out["tests"][0]["latest_run"]["transcript"]
    assert len(transcript) < 40
    assert any("omitted" in (e.get("content") or "") for e in transcript)
    assert all(len(e.get("content") or "") <= 1300 for e in transcript)


@pytest.mark.asyncio
async def test_list_tests_reports_missing_workflow(patched_db):
    out = await wa.list_workflow_tests.execute(key="not_a_workflow")
    assert out["ok"] is False and "not_a_workflow" in out["error"]


@pytest.mark.asyncio
async def test_run_tests_executes_the_group_and_summarizes_verdicts(patched_db, monkeypatch):
    """The agent runs the suite itself and gets back per-case verdicts."""
    from app.services.workflow_test_service import WorkflowTestService

    wf, case = await _seeded_workflow_with_test(patched_db)

    # Stand in for the runner thread: mark the queued rows judged, as the real
    # runner does after the sandboxed agent + judge finish.
    def fake_run(group_id):
        for row in WorkflowTestService.get_run_group(patched_db, group_id):
            row.status = "complete"
            row.verdict = "fail"
            row.score = 15
            row.rationale = "Never asked for a justification."
        patched_db.commit()

    monkeypatch.setattr(
        "app.workflows.test_runner.run_group_in_thread", fake_run
    )

    out = await wa.run_workflow_tests.execute(
        key="tested_flow", _user_email="admin@example.com"
    )
    assert out["ok"] is True
    assert out["total"] == 1 and out["failed"] == 1 and out["passed"] == 0
    assert out["still_running"] == 0
    assert out["results"][0]["test_id"] == case.id
    assert "justification" in out["results"][0]["rationale"]
    # The note must push a diagnosis, not a blind retry.
    assert "which is wrong" in out["note"].lower()


@pytest.mark.asyncio
async def test_run_tests_reports_cases_still_running_instead_of_hanging(patched_db, monkeypatch):
    """A chat turn is bounded; unfinished cases are read back, not re-run."""
    await _seeded_workflow_with_test(patched_db)
    monkeypatch.setattr("app.workflows.test_runner.run_group_in_thread", lambda gid: None)
    monkeypatch.setattr(wa, "_RUN_WAIT_CAP_SECONDS", 0)

    out = await wa.run_workflow_tests.execute(
        key="tested_flow", _user_email="admin@example.com"
    )
    assert out["ok"] is True and out["still_running"] == 1
    assert "list_workflow_tests" in out["note"]
    assert "Do NOT start another run" in out["note"]


@pytest.mark.asyncio
async def test_run_tests_refuses_when_there_are_no_cases(patched_db):
    await wa.save_workflow_draft.execute(
        key="untested_flow", graph_spec=_valid_spec(), request_type="simple_email"
    )
    out = await wa.run_workflow_tests.execute(key="untested_flow")
    assert out["ok"] is False and "save_workflow_tests" in out["error"]


@pytest.mark.asyncio
async def test_run_tests_honors_the_hourly_budget(patched_db, monkeypatch):
    """An agent looping on "run it again" must not be able to spend without limit."""
    from app.services.workflow_test_service import WorkflowTestService

    wf, _ = await _seeded_workflow_with_test(patched_db)
    WorkflowTestService.create_test(
        patched_db, wf.id,
        name="refuses out of scope",
        question="Delete the production catalog",
        expected_outcome="Refuses and explains this workflow does not delete catalogs",
        source="agent",
    )
    # Two enabled cases against a one-case budget.
    monkeypatch.setattr(app_settings, "WORKFLOW_TEST_RUNS_PER_HOUR", 1)
    started = []
    monkeypatch.setattr(
        "app.workflows.test_runner.run_group_in_thread", lambda gid: started.append(gid)
    )

    out = await wa.run_workflow_tests.execute(
        key="tested_flow", _user_email="admin@example.com"
    )
    assert out["ok"] is False and out["rate_limited"] is True
    assert "Do not retry" in out["error"]
    assert started == []


@pytest.mark.asyncio
async def test_run_tests_refuses_when_the_feature_is_disabled(patched_db, monkeypatch):
    monkeypatch.setattr(app_settings, "WORKFLOW_TESTS_ENABLED", False)
    out = await wa.run_workflow_tests.execute(key="tested_flow")
    assert out["ok"] is False and "disabled" in out["error"]
