import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy.orm import Session

from app.models.request import RequestType
from app.state_machines.factory import get_state_machine
from app.state_machines.facts import add_fact, get_latest_fact
from tests.factories.request_factory import RequestFactory
from tests.harness.context import StateMachineTestHarness

pytestmark = pytest.mark.asyncio


@patch("app.state_machines.databricks_job_step.DatabricksProvider")
async def test_asset_deduplication_job_submission(mock_provider_class, db_session: Session):
    """Verify the dedup workflow transitions to ``job_submitted`` and dispatches a job.

    The provider is constructed by ``DatabricksJobStepMixin.build_databricks_provider``,
    so we patch it in the mixin module — the base SM and any single-step
    consumer both build their providers there.
    """
    mock_provider = mock_provider_class.return_value
    mock_provider.import_notebook = AsyncMock(return_value=True)
    # The mixin calls the unified ``submit_job`` primitive, not the
    # back-compat ``submit_notebook_job`` shim.
    mock_provider.submit_job = AsyncMock(return_value="test_run_id_123")

    harness = StateMachineTestHarness(db_session)

    request = RequestFactory.create(
        db_session,
        type=RequestType.ASSET_DEDUPLICATION,
        title="Integration Test Deduplication",
        state_context={
            "target_catalog": "main",
            "reference_catalog": "samples",
            "requested_by": "Test Suite",
            "requested_by_email": "test@example.com",
        },
    )

    # Seed the request_submitted fact so the SM advances pending -> job_submitted.
    add_fact(db_session, request.id, "request_submitted", {}, actor="test")
    db_session.commit()

    sm = get_state_machine(request, db_session)
    sm.tick()
    sm.save()
    db_session.commit()
    harness.assert_state(request.id, "job_submitted")

    # on_enter_job_submitted_async dispatches via the mixin.
    await sm.execute_tasks()
    db_session.commit()

    # The base SM uses step_id="main"; the mixin writes step:<id>:* facts.
    db_session.refresh(request)
    submitted = get_latest_fact(db_session, request.id, "step:main:submitted")
    assert submitted is not None, "step:main:submitted fact should be present"
    assert submitted.event_data["run_id"] == "test_run_id_123"
