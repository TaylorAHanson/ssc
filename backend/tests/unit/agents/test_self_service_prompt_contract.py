"""Guards on the runtime agent's clarify-before-workflow and duplicate-check rules.

The regression these pin: asked for "a new domain called supply chain", the agent
found the domain's workspace already existed and replied "this already exists, but
if you still want it I need ..." followed by the provisioning form. Batching every
field into one message was the rule nearest the reply, so these assert that every
rule about asking for or batching fields waits until the path is settled.
"""
from unittest.mock import patch

from app.agents import prompts


def test_triage_comes_before_data_gathering():
    text = prompts.WORKFLOW_EXECUTION_GUIDELINES
    assert "Phase 0: Triage" in text
    assert text.index("Phase 0: Triage") < text.index("Phase A: Data Gathering")


def test_triage_stops_to_ask_instead_of_listing_requirements():
    text = prompts.WORKFLOW_EXECUTION_GUIDELINES
    assert "ask ONE focused question with 2-4 concrete options" in text
    assert "do not list any workflow's fields or requirements" in text
    assert "never pair \"that already exists\"" in text


def test_batching_only_happens_once_the_workflow_is_settled():
    text = prompts.WORKFLOW_EXECUTION_GUIDELINES
    assert "Once the workflow is settled, gather all of its missing fields" in text
    assert "only after triage" in text
    assert "Batch them." not in text


def test_over_ask_rule_is_scoped_to_details():
    assert "Don't over-ask about details." in prompts.CORE_INSTRUCTIONS
    assert "Asking which path the user wants" in prompts.CORE_INSTRUCTIONS


def test_capabilities_intro_defers_playbook_fetch_to_after_triage(monkeypatch):
    monkeypatch.setattr(prompts, "_capabilities_from_db", lambda: ["- demo: A demo workflow."])
    section = prompts._get_cached_capabilities_section()
    assert "Once triage has settled which one the user needs" in section


def test_duplicate_check_runs_before_the_confirmation_summary():
    text = prompts.WORKFLOW_EXECUTION_GUIDELINES
    phase_b = text[text.index("Phase B: Confirmation"):text.index("Phase C: Execution")]
    assert "check_duplicate_requests" in phase_b
    assert phase_b.index("check_duplicate_requests") < phase_b.index("present a summary")
    phase_c = text[text.index("Phase C: Execution"):text.index("Phase D: Post-Execution")]
    assert "possible_duplicates" in phase_c and "acknowledge_duplicates: true" in phase_c


def test_workspace_provision_playbook_clarifies_scope_first():
    from pathlib import Path

    playbook = (
        Path(prompts.__file__).parent / "instructions" / "workspace_provision.md"
    ).read_text()
    assert playbook.index("## Scope Check") < playbook.index("## Information to Gather")
    assert "Do NOT list this workflow's fields in that message" in playbook


class _Tool:
    def __init__(self, name):
        self.name = name


def test_access_rules_section_only_with_the_access_tools():
    assert prompts._get_access_rules_section([_Tool("search_data_assets")]) == ""
    section = prompts._get_access_rules_section([_Tool("find_access"), _Tool("preview_access_change")])
    assert section == prompts.ACCESS_RULES_INSTRUCTIONS


def test_access_rules_section_pins_the_request_contract():
    text = prompts.ACCESS_RULES_INSTRUCTIONS
    assert text.index("`find_access` first") < text.index("`preview_access_change`")
    assert "never by individual" in text and "Never propose a rule naming a person." in text
    assert "Never write, suggest or embellish" in text
    assert "once per\n  proposal" in text and "`group_ref`" in text and "`submit_items`" in text
    assert "never grant or revoke anything live" in text


def test_access_rules_section_answers_do_i_have_access_from_the_users_groups():
    text = prompts.ACCESS_RULES_INSTRUCTIONS
    assert "`held_by_you`" in text and "`mine=true`" in text
    assert 'never say the user is\n  "denied"' in text
    assert "run_sql" in text  # the one-row SELECT is the certain check


def test_data_tools_combine_into_a_table_details_answer():
    text = prompts.UNIFIED_INSTRUCTIONS
    assert "Details about one table" in text
    assert "`data_quality`" in text and "`sla`" in text
    assert "NOT when its data last refreshed" in text and "DESCRIBE HISTORY" in text


def test_building_and_changing_things_is_handed_off_to_the_workspace():
    text = prompts.UNIFIED_INSTRUCTIONS
    assert "Hand off work that belongs in the workspace" in text
    for need in ("troubleshoot a query", "create or edit a dashboard", "change a table or its data"):
        assert need in text
    assert "Never make up a URL" in text
    # Answering a question with rows and editing tags stay with the app.
    assert "use `run_sql`" in text and "Metadata Manager tools" in text


def test_access_for_oneself_never_guesses_the_group_or_requests_what_a_group_has():
    text = prompts.ACCESS_RULES_INSTRUCTIONS
    assert "Never pick the group for them" in text
    assert 'replies only "yes" or "please", ask which one' in text
    assert "Never\n  call `preview_access_change` for a group that already holds" in text


def test_do_i_have_access_is_settled_with_a_probe_not_a_question():
    text = prompts.ACCESS_RULES_INSTRUCTIONS
    assert "settle it yourself in the same turn instead of\n  asking permission" in text
    assert "SELECT 1 FROM catalog.schema.table LIMIT 1" in text
    # Only a real permission error counts as "no access"; other failures are "couldn't check".
    assert "Any other error" in text and "don't claim either way" in text


def test_a_hand_off_is_quick_and_does_not_surface_plumbing():
    text = prompts.UNIFIED_INSTRUCTIONS
    assert "Never make up a URL or a `target_host`" in text
    assert 'link text "SQL editor"' in text
    assert "never report a token, permission or connection error" in text
    # No live research before a hand-off, and nothing the rule then asks for contradicts it:
    # it used to ban lookups and then tell the model to quote columns it had "confirmed".
    assert (
        "never call `get_table_list`, `run_sql`, `find_access` or `search_data_assets` "
        "to prepare a hand-off"
    ) in text
    assert "never run or test the user's query yourself" in text
    # A dashboard hand-off needs no lookups at all, and doesn't ask which dataset first.
    assert "For a dashboard, make no lookups" in text
    assert "Don't search for one or ask which dataset before handing off" in text
    assert "a likely fix you haven't run" in text
    assert "columns you confirmed" not in text


def test_a_bare_name_with_several_matches_is_asked_about_not_guessed():
    flat = " ".join(prompts.UNIFIED_INSTRUCTIONS.split())
    assert "ask which one they mean" in flat
    assert "Don't pick one and answer for it" in flat and "even if one is certified" in flat


def test_a_denied_metadata_user_is_routed_to_the_owner_not_to_an_admin():
    flat = " ".join(prompts.METADATA_INSTRUCTIONS.split())
    assert "has no access to the Metadata Manager" in flat
    assert "Don't tell them to ask an admin for access" in flat
    assert "name the table's owner (`find_owner`)" in flat
    # Reporting is only offered when the feedback tool exists.
    assert "if you have `submit_feedback`" in flat


def test_the_assistant_does_not_loop_on_a_failing_lookup():
    text = prompts.UNIFIED_INSTRUCTIONS
    assert "**Don't loop.**" in text and "don't retry it with variations" in text


def test_dashboards_are_answered_from_the_cache():
    text = prompts.UNIFIED_INSTRUCTIONS
    assert "answered from `downstream_dashboards`" in text and "doesn't record Genie spaces" in text


def test_feedback_is_filed_on_request_and_only_once():
    with patch("app.core.feature_flags.is_feature_enabled", return_value=True):
        text = prompts._get_feedback_section()
    assert "Only call `submit_feedback` when the user has asked you to" in text
    assert "has\n        not asked" not in text  # (wording check below is whitespace-insensitive)
    flat = " ".join(text.split())
    assert "A user who just describes a problem" in flat and "Wait for their yes" in flat
    assert "File each issue once" in flat
    # The rule can't be contradicted by an instruction to file straight away.
    assert "Capture what they saw, file it" not in flat


def _feedback_text(tabs):
    with patch("app.core.feature_flags.is_feature_enabled", return_value=True), patch(
        "app.core.settings_store.effective_ui_tabs", return_value=tabs
    ):
        return " ".join(prompts._get_feedback_section().split())


def test_feedback_says_where_admins_read_it_and_who_can_open_it():
    # The assistant once told a Platform Admin there was no page for this and offered to log a
    # request to build one: it was never told Admin -> Feedback exists.
    flat = _feedback_text({"feedback": True})
    assert "[Admin → Feedback](/admin/feedback)" in flat
    assert "Platform Admin and Governance Admin" in flat
    assert "never offer to log a request for one" in flat
    assert "only when the Roles line under CURRENT USER IDENTITY lists one of those two" in flat
    assert "they can't open that page, and don't link it" in flat
    # A question about feedback is answered, not filed.
    assert "is a question, not a report" in flat


def test_feedback_does_not_send_anyone_to_a_page_an_admin_turned_off():
    flat = _feedback_text({"feedback": False})
    assert "/admin/feedback" not in flat
    assert "switched off here" in flat
