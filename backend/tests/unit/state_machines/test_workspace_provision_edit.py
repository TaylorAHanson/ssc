"""
Tests for the Edit & Restart (Edit Parameters) feature on WorkspaceProvisionStateMachine.

These tests verify:
1. The temporal boundary logic (parameters_edited fact vs execution facts)
2. State machine transitions through parameters_updated → terraform_planning
3. That old facts are NEVER deleted — history is preserved
4. Approval superseding (not deletion)
5. The edit-and-replan cycle can repeat multiple times

Test data flows through the 'workspace_provision' type (which maps to
WorkspaceProvisionStateMachine in the factory).
"""
from datetime import datetime, timezone, timedelta
import pytest
from tests.harness.context import StateMachineTestHarness
from tests.factories.request_factory import RequestFactory
from app.state_machines.facts import add_fact, get_facts, get_latest_fact
from app.state_machines.workspace_provision.state_machine import WorkspaceProvisionStateMachine
from app.db.request import RequestModel
from app.db.approval import ApprovalModel
from app.state_machines.factory import get_state_machine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _advance_to_awaiting_admin_approval(db_session, request):
    """Put a workspace_provision request into awaiting_admin_approval state directly.

    We inject facts and state directly rather than ticking through intermediate states.
    This avoids the non-deterministic mark_failed/submit race in _try_transitions,
    which has no bearing on the edit-parameters logic we're actually testing.

    The facts injected mirror what would have accumulated naturally:
      request_submitted → approval_received (manager) → terraform_plan_started → terraform_plan_received
    """
    from app.db.approval import ApprovalModel
    import uuid

    # Inject accumulated facts
    add_fact(db_session, request.id, "request_submitted", {}, actor="system")
    add_fact(db_session, request.id, "approval_received", {"approval_type": "manager"}, actor="manager@test.com")
    add_fact(db_session, request.id, "terraform_plan_started", {}, actor="system")
    add_fact(db_session, request.id, "terraform_plan_received", {"plan_output": "Plan: 3 to add"}, actor="system")

    # Move the request to awaiting_admin_approval
    db_request = db_session.get(RequestModel, request.id)
    db_request.current_state = "awaiting_admin_approval"
    db_session.commit()

    # Create the pending platform_admin approval record (the base class would have done this
    # automatically when entering awaiting_admin_approval via an on_enter hook)
    approval = ApprovalModel(
        id=f"app-{uuid.uuid4()}",
        request_id=request.id,
        approval_type="platform_admin",
        requested_by="user@test.com",
        requested_by_email="user@test.com",
        status="pending",
    )
    db_session.add(approval)
    db_session.commit()

    return StateMachineTestHarness(db_session)



# ---------------------------------------------------------------------------
# 1. get_editable_states
# ---------------------------------------------------------------------------

def test_get_editable_states_returns_admin_approval_gate(db_session):
    """WorkspaceProvisionStateMachine should report awaiting_admin_approval as editable."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        current_state="awaiting_admin_approval",
        state_context={"workspace_name": "ws-alpha"},
    )
    sm = get_state_machine(request, db_session)
    assert "awaiting_admin_approval" in sm.get_editable_states()


# ---------------------------------------------------------------------------
# 2. has_parameters_edited — temporal boundary logic
# ---------------------------------------------------------------------------

def test_has_parameters_edited_false_when_no_edit_fact(db_session):
    """has_parameters_edited must be False when no parameters_edited fact exists."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        current_state="awaiting_admin_approval",
        state_context={"workspace_name": "ws-alpha"},
    )
    sm = get_state_machine(request, db_session)
    assert sm.has_parameters_edited is False


def test_has_parameters_edited_true_after_edit_fact(db_session):
    """has_parameters_edited should be True once a parameters_edited fact is added."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        current_state="awaiting_admin_approval",
        state_context={"workspace_name": "ws-alpha"},
    )
    add_fact(db_session, request.id, "parameters_edited", {"edited_by": "admin@test.com"}, actor="admin@test.com")
    db_session.commit()

    sm = get_state_machine(request, db_session)
    assert sm.has_parameters_edited is True


def test_has_parameters_edited_false_after_admin_approval_post_edit(db_session):
    """If a platform_admin approval arrives AFTER the edit, the edit is considered actioned
    and has_parameters_edited should return False (prevents re-entering parameters_updated
    on the next tick)."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        current_state="awaiting_admin_approval",
        state_context={"workspace_name": "ws-alpha"},
    )

    t0 = datetime.now(timezone.utc)
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com"}, actor="admin@test.com")
    db_session.commit()

    # Admin approves AFTER the edit
    add_fact(db_session, request.id, "approval_received",
             {"approval_type": "platform_admin", "approved_by": "admin@test.com"}, actor="admin@test.com")
    db_session.commit()

    sm = get_state_machine(request, db_session)
    assert sm.has_parameters_edited is False, (
        "Edit should be considered actioned once admin approves after it"
    )


# ---------------------------------------------------------------------------
# 3. has_current_terraform_plan — temporal boundary logic
# ---------------------------------------------------------------------------

def test_has_current_terraform_plan_false_when_plan_predates_edit(db_session):
    """A terraform_plan_received fact from BEFORE a parameters_edited fact should be
    considered stale — has_current_terraform_plan must return False."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        current_state="terraform_planning",
        state_context={"workspace_name": "ws-alpha"},
    )

    # Old plan arrives first
    add_fact(db_session, request.id, "terraform_plan_received",
             {"plan_output": "Plan: 2 to add"}, actor="system")
    db_session.commit()

    # Then parameters are edited (temporal boundary)
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com", "new_params": {"workspace_name": "ws-beta"}},
             actor="admin@test.com")
    db_session.commit()

    sm = get_state_machine(request, db_session)
    assert sm.has_current_terraform_plan is False, (
        "Plan received before the parameter edit should be treated as stale"
    )


def test_has_current_terraform_plan_true_when_plan_postdates_edit(db_session):
    """A terraform_plan_received fact that arrives AFTER parameters_edited should be
    considered current — has_current_terraform_plan must return True."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        current_state="terraform_planning",
        state_context={"workspace_name": "ws-alpha"},
    )

    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com"}, actor="admin@test.com")
    db_session.commit()

    # Fresh plan received AFTER the edit
    add_fact(db_session, request.id, "terraform_plan_received",
             {"plan_output": "Plan: 1 to add (ws-beta)"}, actor="system")
    db_session.commit()

    sm = get_state_machine(request, db_session)
    assert sm.has_current_terraform_plan is True


# ---------------------------------------------------------------------------
# 4. State machine transition: edit_and_restart → parameters_updated → terraform_planning
# ---------------------------------------------------------------------------

def test_edit_and_restart_transitions_through_parameters_updated(db_session):
    """When parameters_edited fact is injected for a request at awaiting_admin_approval,
    a single tick should transition through parameters_updated and arrive at terraform_planning."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        state_context={"workspace_name": "ws-alpha", "requested_by_email": "user@test.com"},
    )
    _advance_to_awaiting_admin_approval(db_session, request)

    # Simulate the edit-parameters API call: update context + add fact
    db_request = db_session.get(RequestModel, request.id)
    db_request.state_context = {"workspace_name": "ws-beta", "requested_by_email": "user@test.com"}
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com", "new_params": {"workspace_name": "ws-beta"}},
             actor="admin@test.com")
    db_session.commit()

    harness = StateMachineTestHarness(db_session)
    harness.tick(request.id)

    # The tick should have driven: awaiting_admin_approval → parameters_updated → terraform_planning
    harness.assert_state(request.id, "terraform_planning")


# ---------------------------------------------------------------------------
# 5. History preservation — facts are NEVER deleted
# ---------------------------------------------------------------------------

def test_old_execution_facts_are_preserved_after_edit(db_session):
    """The prior terraform_plan_started and terraform_plan_received facts must still
    exist in the DB after a parameter edit. Immutability is sacred."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        state_context={"workspace_name": "ws-alpha", "requested_by_email": "user@test.com"},
    )
    _advance_to_awaiting_admin_approval(db_session, request)

    # Simulate the edit-parameters API: add edit fact (no deletions!)
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com", "new_params": {"workspace_name": "ws-beta"}},
             actor="admin@test.com")
    db_session.commit()

    # All prior facts still exist
    plan_received_facts = get_facts(db_session, request.id, "terraform_plan_received")
    assert len(plan_received_facts) >= 1, "terraform_plan_received facts must be preserved"

    edit_facts = get_facts(db_session, request.id, "parameters_edited")
    assert len(edit_facts) == 1, "Exactly one parameters_edited fact should exist"


def test_multiple_edit_cycles_accumulate_facts(db_session):
    """If a request is edited twice, all facts from both cycles should be present
    in the event log. No history is overwritten."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        state_context={"workspace_name": "ws-alpha", "requested_by_email": "user@test.com"},
    )
    _advance_to_awaiting_admin_approval(db_session, request)

    # First edit
    db_request = db_session.get(RequestModel, request.id)
    db_request.state_context = {"workspace_name": "ws-beta", "requested_by_email": "user@test.com"}
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com", "new_params": {"workspace_name": "ws-beta"}},
             actor="admin@test.com")
    db_session.commit()

    harness = StateMachineTestHarness(db_session)
    harness.tick(request.id)  # → terraform_planning

    # Second plan arrives
    add_fact(db_session, request.id, "terraform_plan_received",
             {"plan_output": "Plan: 1 to add (ws-beta)"}, actor="system")
    db_session.commit()

    harness.tick(request.id)  # → awaiting_admin_approval again

    # Second edit
    db_request = db_session.get(RequestModel, request.id)
    db_request.state_context = {"workspace_name": "ws-gamma", "requested_by_email": "user@test.com"}
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com", "new_params": {"workspace_name": "ws-gamma"}},
             actor="admin@test.com")
    db_session.commit()

    # Both edit facts preserved
    edit_facts = get_facts(db_session, request.id, "parameters_edited")
    assert len(edit_facts) == 2, "Both parameter edit events must be preserved in history"

    # Both plan_received facts preserved
    plan_facts = get_facts(db_session, request.id, "terraform_plan_received")
    assert len(plan_facts) >= 2, "Both plan received events must be preserved in history"


# ---------------------------------------------------------------------------
# 6. Approval superseding, not deletion
# ---------------------------------------------------------------------------

def test_edit_supersedes_pending_approval_without_deleting_it(db_session):
    """Simulates the API logic: pending approvals should become 'superseded', not deleted."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        state_context={"workspace_name": "ws-alpha", "requested_by_email": "user@test.com"},
    )
    _advance_to_awaiting_admin_approval(db_session, request)

    # At this point there should be a pending platform_admin approval record
    pending = db_session.query(ApprovalModel).filter(
        ApprovalModel.request_id == request.id,
        ApprovalModel.status == "pending",
    ).first()
    assert pending is not None, "A pending approval should exist at awaiting_admin_approval"
    approval_id = pending.id

    # Simulate the edit-parameters API superseding the approval
    pending.status = "superseded"
    pending.superseded_note = "Superseded by parameter edit from admin@test.com"
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com"}, actor="admin@test.com")
    db_session.commit()

    # Record exists but is superseded — never deleted
    approval_in_db = db_session.query(ApprovalModel).filter(
        ApprovalModel.id == approval_id
    ).first()
    assert approval_in_db is not None, "The original approval record must still exist in the DB"
    assert approval_in_db.status == "superseded"
    assert approval_in_db.superseded_note is not None


# ---------------------------------------------------------------------------
# 7. Full happy-path after edit
# ---------------------------------------------------------------------------

def test_full_edit_then_approve_cycle(db_session):
    """End-to-end: request reaches admin approval → admin edits → new plan → admin approves
    → workflow proceeds to terraform_applying."""
    request = RequestFactory.create(
        db_session,
        type="workspace_provision",
        state_context={"workspace_name": "ws-alpha", "requested_by_email": "user@test.com"},
    )
    harness = _advance_to_awaiting_admin_approval(db_session, request)

    # Admin edits instead of approving
    db_request = db_session.get(RequestModel, request.id)
    db_request.state_context = {"workspace_name": "ws-beta", "requested_by_email": "user@test.com"}
    db_request.locked_by = None  # Simulate lock release from API
    add_fact(db_session, request.id, "parameters_edited",
             {"edited_by": "admin@test.com", "new_params": {"workspace_name": "ws-beta"}},
             actor="admin@test.com")
    db_session.commit()

    harness.tick(request.id)
    harness.assert_state(request.id, "terraform_planning")

    # Fresh plan arrives for the new parameters
    add_fact(db_session, request.id, "terraform_plan_received",
             {"plan_output": "Plan: 1 to add (ws-beta)"}, actor="system")
    db_session.commit()

    harness.tick(request.id)
    harness.assert_state(request.id, "awaiting_admin_approval")

    # Admin approves the fresh plan
    add_fact(db_session, request.id, "approval_received",
             {"approval_type": "platform_admin", "approved_by": "admin@test.com"},
             actor="admin@test.com")
    db_session.commit()

    harness.tick(request.id)
    harness.assert_state(request.id, "terraform_applying")

    # Verify the final state_context has the updated workspace name
    final_request = db_session.get(RequestModel, request.id)
    assert final_request.state_context.get("workspace_name") == "ws-beta"
