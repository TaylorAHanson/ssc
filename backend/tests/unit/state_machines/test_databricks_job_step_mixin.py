"""Unit tests for ``DatabricksJobStepMixin``.

Exercises the full per-step lifecycle in isolation using a stand-in host
class that mixes in the mixin but is not a real ``BaseRequestStateMachine``
(we only need ``self.request`` and ``self.db``). The Databricks provider is
mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.providers.databricks.compute import ComputeSpec
from app.state_machines.databricks_job_step import DatabricksJobStepMixin
from app.state_machines.facts import add_fact, get_latest_fact
from tests.factories.request_factory import RequestFactory


class _StepHost(DatabricksJobStepMixin):
    """Minimal host: just exposes the two attributes the mixin needs."""

    def __init__(self, request, db, provider):
        self.request = request
        self.db = db
        self._provider = provider

    def build_databricks_provider(self):
        return self._provider


@pytest.fixture
def request_and_provider(db_session):
    request = RequestFactory.create(
        db_session,
        type="asset_deduplication",  # any valid type; this test doesn't dispatch
        title="Mixin lifecycle test",
        state_context={},
    )
    provider = MagicMock()
    provider.import_notebook = AsyncMock(return_value=True)
    provider.upload_python_script = AsyncMock(return_value="Workspace/tmp/test_job.py")
    provider.submit_job = AsyncMock(return_value="run-12345")
    provider.get_run_status = AsyncMock(
        return_value={
            "life_cycle_state": "RUNNING",
            "result_state": None,
            "state_message": "",
            "is_active": True,
            "is_completed": False,
            "is_successful": False,
        }
    )
    provider.get_run_output = AsyncMock(
        return_value={"notebook_result": "ok", "truncated": False}
    )
    return request, provider


pytestmark = pytest.mark.asyncio


async def test_submit_phase_writes_submitted_fact(db_session, request_and_provider):
    """First call uploads + submits + writes step:<id>:submitted."""
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    await host.run_databricks_job_step(
        step_id="email",
        python_code="print('hi')",
        parameters=["alice@example.com"],
    )

    assert host.step_submitted("email") is True
    assert host.step_completed("email") is False
    assert host.step_failed("email") is False

    submitted = get_latest_fact(db_session, request.id, "step:email:submitted")
    assert submitted.event_data["run_id"] == "run-12345"
    assert submitted.event_data["compute"] == "serverless"

    provider.upload_python_script.assert_awaited_once()
    provider.submit_job.assert_awaited_once()


async def test_classic_compute_recorded_on_fact(db_session, request_and_provider):
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    await host.run_databricks_job_step(
        step_id="ldap",
        python_code="print('ldap')",
        parameters=[],
        compute=ComputeSpec(existing_cluster_id="cluster-abc"),
    )

    submitted = get_latest_fact(db_session, request.id, "step:ldap:submitted")
    assert submitted.event_data["compute"] == "classic"


async def test_second_call_polls_instead_of_resubmitting(db_session, request_and_provider):
    """When the submitted fact exists, the next call polls."""
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )
    provider.submit_job.assert_awaited_once()  # only on the first call

    # Provider says the run is still going — no terminal fact expected yet.
    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )
    provider.get_run_status.assert_awaited_once()
    assert host.step_completed("email") is False
    assert host.step_failed("email") is False
    # submit_job not called again.
    provider.submit_job.assert_awaited_once()


async def test_poll_writes_completed_fact_with_output(db_session, request_and_provider):
    """Once the run is successful, we capture the output payload."""
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )

    provider.get_run_status.return_value = {
        "life_cycle_state": "TERMINATED",
        "result_state": "SUCCESS",
        "state_message": "",
        "is_active": False,
        "is_completed": True,
        "is_successful": True,
    }

    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )

    assert host.step_completed("email") is True
    completed = get_latest_fact(db_session, request.id, "step:email:completed")
    assert completed.event_data["run_id"] == "run-12345"
    assert completed.event_data["output"]["notebook_result"] == "ok"

    # Helper accessors return the right values.
    assert host.get_step_run_id("email") == "run-12345"
    assert host.get_step_output("email")["notebook_result"] == "ok"


async def test_poll_writes_failed_fact_on_run_failure(db_session, request_and_provider):
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )

    provider.get_run_status.return_value = {
        "life_cycle_state": "TERMINATED",
        "result_state": "FAILED",
        "state_message": "boom",
        "is_active": False,
        "is_completed": True,
        "is_successful": False,
    }

    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )

    assert host.step_failed("email") is True
    assert host.step_completed("email") is False
    assert host.get_step_error("email") == "boom"


async def test_submit_error_writes_failed_fact_and_does_not_raise(
    db_session, request_and_provider
):
    """A submit failure becomes a terminal failed fact, never an exception."""
    request, provider = request_and_provider
    provider.submit_job.side_effect = RuntimeError("kaboom")

    host = _StepHost(request, db_session, provider)

    # Must not raise.
    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )

    assert host.step_failed("email") is True
    assert host.step_submitted("email") is False
    assert "kaboom" in host.get_step_error("email")


async def test_terminal_step_is_idempotent_noop(db_session, request_and_provider):
    """Calling run_databricks_job_step on a completed step is a no-op."""
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    add_fact(
        db_session, request.id, "step:email:completed",
        {"run_id": "abc", "output": {}}, actor="test",
    )

    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )

    provider.submit_job.assert_not_awaited()
    provider.get_run_status.assert_not_awaited()


async def test_multiple_steps_have_independent_facts(db_session, request_and_provider):
    """Two different step_ids don't collide on facts or polling state."""
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    await host.run_databricks_job_step(
        step_id="email", python_code="x", parameters=[]
    )
    await host.run_databricks_job_step(
        step_id="ldap", python_code="y", parameters=[]
    )

    assert host.step_submitted("email") is True
    assert host.step_submitted("ldap") is True
    assert provider.submit_job.await_count == 2


async def test_parameter_type_validated_for_notebook(db_session, request_and_provider):
    """Notebook jobs require dict parameters; passing a list is an immediate error."""
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    # We don't have a real notebook file in this test, so the mixin's import
    # would fail. The validation should kick in *before* that. To exercise
    # only the validation, point notebook_path at a non-existent path and
    # pass the wrong parameter type — the failure handler catches the
    # TypeError and records it as a failed fact.
    await host.run_databricks_job_step(
        step_id="invalid",
        notebook_path="does_not_exist.py",
        parameters=["wrong", "type"],
    )

    assert host.step_failed("invalid") is True
    assert "must be a dict" in host.get_step_error("invalid")


async def test_mutual_exclusion_of_notebook_and_python(db_session, request_and_provider):
    request, provider = request_and_provider
    host = _StepHost(request, db_session, provider)

    with pytest.raises(ValueError, match="exactly one"):
        await host.run_databricks_job_step(
            step_id="bad",
            notebook_path="a.py",
            python_code="print(1)",
            parameters={},
        )

    with pytest.raises(ValueError, match="exactly one"):
        await host.run_databricks_job_step(
            step_id="bad2",
            parameters={},
        )
