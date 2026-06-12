"""Unit tests for deriving runtime instructions from a workflow spec.

The key guarantee here is that the ``execute_workflow`` call contract (the
``workflow_type`` and parameter keys) is ALWAYS derived from the graph — never
hand-typed — so the runtime call can't drift from the spec, even when an admin
hand-writes the prose.
"""
from app.workflows.instructions import (
    execution_contract,
    render_execution_block,
    render_instructions_markdown,
    with_canonical_execution,
)


def _spec_with_inputs():
    return {
        "name": "custom_training",
        "stages": [
            {"kind": "gate", "name": "approval", "type": "manager"},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "approvals": ["manager"], "args": {
                 "to_email": {"$literal": "scheduler@corp.com"},
                 "subject": {"$literal": "Training request"},
                 "body": {"$concat": [
                     "Topics: ", {"$var": "topics"},
                     ", headcount: ", {"$var": "headcount"},
                     ", domain: ", {"$var": "domain"},
                 ]},
             }},
        ],
    }


def test_execution_contract_uses_request_type_and_step_vars():
    contract = execution_contract(_spec_with_inputs(), request_type="custom_training_request")
    assert contract["workflow_type"] == "custom_training_request"
    assert contract["parameters"] == ["topics", "headcount", "domain"]


def test_execution_contract_excludes_platform_vars():
    spec = {
        "name": "wf",
        "stages": [
            {"kind": "step", "name": "s", "tool": "send_notification",
             "args": {"to_email": {"$var": "requested_by_email"}, "body": {"$var": "reason"}}},
        ],
    }
    assert execution_contract(spec, request_type="wf")["parameters"] == ["reason"]


def test_execution_block_renders_each_param():
    block = render_execution_block(_spec_with_inputs(), request_type="custom_training_request")
    assert '"workflow_type": "custom_training_request"' in block
    for var in ("topics", "headcount", "domain"):
        assert f'"{var}"' in block


def test_execution_block_empty_parameters_when_no_inputs():
    spec = {"name": "noop", "stages": [{"kind": "gate", "name": "g", "type": "manager"}]}
    block = render_execution_block(spec, request_type="noop")
    assert '"parameters": {}' in block


def test_with_canonical_execution_replaces_handwritten_block():
    """A stale/wrong hand-written Execution block is replaced with the graph-derived one."""
    handwritten = (
        "# Custom Training Instructions\n\n"
        "**Goal**: schedule training.\n\n"
        "## Information to Gather\n"
        "1. Topics\n2. Headcount\n\n"
        "## Execution\n"
        "Call `execute_workflow` with:\n"
        "```json\n{\n  \"workflow_type\": \"WRONG_TYPE\",\n"
        "  \"parameters\": {\"foo\": \"...\"}\n}\n```\n"
    )
    merged = with_canonical_execution(
        handwritten, _spec_with_inputs(), request_type="custom_training_request"
    )
    # Prose preserved.
    assert "**Goal**: schedule training." in merged
    assert "## Information to Gather" in merged
    # The wrong example is gone; the canonical one is present.
    assert "WRONG_TYPE" not in merged
    assert '"foo"' not in merged
    assert '"workflow_type": "custom_training_request"' in merged
    assert '"topics"' in merged
    # Exactly one Execution section.
    assert merged.count("## Execution") == 1


def test_with_canonical_execution_appends_when_prose_has_no_block():
    prose = "# Title\n\n**Goal**: do a thing.\n\n## Information to Gather\n1. Topics\n"
    merged = with_canonical_execution(prose, _spec_with_inputs(), request_type="custom_training_request")
    assert "**Goal**: do a thing." in merged
    assert merged.count("## Execution") == 1
    assert '"workflow_type": "custom_training_request"' in merged


def test_with_canonical_execution_noop_without_stages():
    prose = "# Title\n\nSome prose.\n"
    assert with_canonical_execution(prose, {"name": "x", "stages": []}) == prose


def test_render_instructions_markdown_includes_canonical_execution():
    md = render_instructions_markdown(_spec_with_inputs(), request_type="custom_training_request")
    assert md.count("## Execution") == 1
    assert '"workflow_type": "custom_training_request"' in md
    assert '"topics"' in md
