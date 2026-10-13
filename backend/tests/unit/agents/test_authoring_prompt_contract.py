"""Guards on the authoring assistant's instructions.

Behavior has drifted twice because the same rule was stated in two places and the
two disagreed — a system-prompt bullet said "run the tests" while the tool result
said "tell the admin to click Run", and the design-turn checklist said "offer to
save" while another rule said to save and then test. The agent follows whichever
text is nearest, so these assert that the WHOLE chain reads consistently: a build
request is carried through to a saved draft with tests that have actually run, and
only publishing waits for confirmation.

Text-matching tests are blunt, but the alternative here is a behavior regression
nobody notices until a demo.
"""
from app.agents.prompts import (
    get_agent_prompt,
    render_editor_draft_block,
    render_studio_list_block,
)
from app.tools.authoring import workflow_authoring as wa


def _authoring_prompt() -> str:
    return get_agent_prompt(mode="authoring")


def test_authoring_prompt_requires_the_full_chain_in_one_turn():
    prompt = _authoring_prompt()
    for step in (
        "save_workflow_draft",
        "save_workflow_tests",
        "run_workflow_tests",
    ):
        assert step in prompt, f"{step} is never mentioned to the authoring agent"
    assert "FINISH THE JOB IN ONE TURN" in prompt


def test_authoring_prompt_does_not_gate_saving_behind_confirmation():
    """Saving a draft is reversible and not live; stopping to ask left admins with a
    described-but-nonexistent workflow and no tests."""
    prompt = _authoring_prompt()
    for stale in (
        "an explicit offer to save it as a draft",
        "then offer to save.",
        "Only call after the admin has reviewed",
    ):
        assert stale not in prompt, f"stop-and-ask language is back: {stale!r}"


def test_publishing_still_requires_explicit_confirmation():
    """The autonomy above stops at the live boundary."""
    prompt = _authoring_prompt()
    assert "publish_workflow` ONLY after the admin explicitly confirms" in prompt
    assert "Never publish without validating + previewing + explicit confirmation." in prompt


def test_save_tools_do_not_tell_the_agent_to_hand_off_to_the_admin():
    """The tool descriptions are read at call time, so a "tell the admin to run it"
    there beats any rule in the system prompt."""
    save_draft = wa.save_workflow_draft.description
    save_tests = wa.save_workflow_tests.description
    assert "do NOT need permission to save" in save_draft
    assert "ask before PUBLISHING, not before saving" in save_draft
    assert "CALL run_workflow_tests" in save_tests
    assert "review and run" not in save_tests


def test_gate_type_fields_are_documented_where_the_agent_looks():
    """`instructions` is valid only on a manual_task gate. The agent guessed wrong
    and burned an iteration because the building-blocks tool listed gate types as
    bare strings with no field documentation."""
    details = wa._GATE_TYPE_DETAILS
    assert set(details) == set(wa._GATE_TYPES)
    manual = " ".join(details["manual_task"]["extra_fields"])
    assert "instructions" in manual and "due_in_days" in manual
    assert "course_code" in " ".join(details["training"]["extra_fields"])
    # And an approval gate must not advertise fields it would be rejected for.
    assert details["platform_admin"]["extra_fields"] == []


def test_authoring_prompt_defaults_to_the_open_draft():
    prompt = _authoring_prompt()
    assert "Default to EDITING" in prompt
    assert "WORKFLOW STUDIO — LIST VIEW" in prompt


def test_authoring_prompt_does_not_ask_to_take_published_workflows_offline():
    prompt = _authoring_prompt()
    assert "take_offline" not in prompt
    assert "EDITING A WORKFLOW THAT IS ALREADY PUBLISHED IS DIFFERENT" not in prompt
    assert "do not pause to ask whether a published workflow should be taken offline" in prompt.lower()


def test_open_draft_block_tells_the_agent_not_to_start_from_scratch():
    block = render_editor_draft_block(
        {
            "key": "catalog_access",
            "name": "Catalog access",
            "goal": "Grant catalog access",
            "graph_spec": {"stages": [{"kind": "gate", "type": "manager", "name": "mgr"}]},
            "instructions_markdown": "Gather the catalog name.",
        }
    )
    assert "OPEN DRAFT IN THE WORKFLOW EDITOR" in block
    assert "catalog_access" in block
    assert "Default to editing THIS workflow" in block


def test_blank_new_form_is_not_an_open_draft():
    assert render_editor_draft_block({"key": "", "graph_spec": {"stages": []}}) == ""


def test_list_view_block_names_existing_workflows():
    block = render_studio_list_block(
        [
            {"key": "catalog_access", "name": "Catalog access", "status": "published"},
            {"key": "new_workspace", "name": "new_workspace", "status": "draft", "disabled": True},
        ]
    )
    assert "WORKFLOW STUDIO — LIST VIEW" in block
    assert "catalog_access" in block
    assert "turned off" in block
    assert "Do not start a blank new workflow for an edit request" in block
