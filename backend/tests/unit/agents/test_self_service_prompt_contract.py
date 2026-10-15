"""Guards on the runtime agent's clarify-before-workflow and duplicate-check rules.

The regression these pin: asked for "a new domain called supply chain", the agent
found the domain's workspace already existed and replied "this already exists, but
if you still want it I need ..." followed by the provisioning form. Batching every
field into one message was the rule nearest the reply, so these assert that every
rule about asking for or batching fields waits until the path is settled.
"""
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
