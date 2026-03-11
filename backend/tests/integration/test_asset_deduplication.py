import pytest
import asyncio
from sqlalchemy.orm import Session
from app.models.request import RequestType, RequestStatus
from tests.factories.request_factory import RequestFactory
from tests.harness.context import StateMachineTestHarness
from app.db.request import RequestModel
from app.state_machines.factory import get_state_machine

# Async test marker
pytestmark = pytest.mark.asyncio

async def test_asset_deduplication_job_submission(db_session: Session):
    """
    Verifies that the AssetDeduplication workflow transitions to job_submitted
    and attempts to submit a Databricks job.
    """
    harness = StateMachineTestHarness(db_session)
    
    # 1. Create Deduplication Request
    # Note: We use valid-looking catalog names as required by the job logic
    request = RequestFactory.create(
        db_session,
        type=RequestType.ASSET_DEDUPLICATION,
        title="Integration Test Deduplication",
        state_context={
            "target_catalog": "main",
            "reference_catalog": "samples",
            "requested_by": "Test Suite",
            "requested_by_email": "test@example.com"
        }
    )
    
    print(f"DEBUG: Created Deduplication Request {request.id}")
    
    # 2. Add request_submitted fact (Pending -> Job Submitted)
    from app.state_machines.facts import add_fact
    add_fact(db_session, request.id, "request_submitted", {}, actor="test")
    db_session.commit()

    # 3. Tick the state machine
    # This should trigger on_enter_job_submitted_async
    sm = get_state_machine(request, db_session)
    
    # Tick 1: Pending -> Job Submitted
    # The tick() method in BaseRequestStateMachine handles state transitions.
    # execute_tasks() handles the async on_enter hooks.
    
    sm.tick()
    sm.save()
    db_session.commit()
    
    harness.assert_state(request.id, "job_submitted")
    
    # 4. Execute Tasks (Run on_enter_job_submitted_async)
    # This is where the real Databricks API call happens
    print("DEBUG: Executing job submission task...")
    try:
        await sm.execute_tasks()
        db_session.commit()
    except Exception as e:
        print(f"DEBUG: Job submission failed (this might be expected if environment is still not quite right): {e}")
        # Note: We don't fail the test yet if it's a real API failure, 
        # but we want to see the error in the logs.
        raise e

    # 5. Verify run_id was captured
    db_session.refresh(request)
    from app.state_machines.facts import get_latest_fact
    run_id_fact = get_latest_fact(db_session, request.id, "run_id_created")
    assert run_id_fact is not None, "run_id_created fact should be present"
    assert "run_id" in run_id_fact.event_data
    
    print(f"DEBUG: Job submitted successfully with Run ID: {run_id_fact.event_data['run_id']}")
