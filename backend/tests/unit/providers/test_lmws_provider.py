import json
import os

import pytest
from unittest.mock import AsyncMock, patch

from app.core.exceptions import PermanentError, RetryableError
from app.providers.lmws import LmwsAction, LmwsProvider


# ---------------------------------------------------------------------------
# build_parameters — the action/param contract
# ---------------------------------------------------------------------------

def test_build_parameters_normalizes_and_defaults():
    provider = LmwsProvider()
    params = provider.build_parameters(
        LmwsAction.LIST_MEMBERS_ADD,
        list_name="edh_dbx_consultant",
        members=["user1", "user2"],
    )
    assert params["action"] == "list_members_add"
    assert params["list_name"] == "edh_dbx_consultant"
    # list -> CSV
    assert params["members"] == "user1,user2"
    # secret scope passed through for the notebook
    assert params["secret_scope"] == provider.secret_scope
    # defaults applied
    assert params["justification"]
    assert params["clone_source"]
    # every value is a string (notebook widgets are strings)
    assert all(isinstance(v, str) for v in params.values())


def test_build_parameters_accepts_csv_string_members():
    provider = LmwsProvider()
    params = provider.build_parameters(LmwsAction.LIST_MEMBERS_ADD, list_name="l", members="a, b ,c")
    assert params["members"] == "a,b,c"


def test_build_parameters_rejects_unknown_action():
    with pytest.raises(ValueError):
        LmwsProvider().build_parameters("not_a_real_action", list_name="l")


# ---------------------------------------------------------------------------
# parse_output — reading the notebook's JSON result
# ---------------------------------------------------------------------------

def test_parse_output_parses_json_string():
    out = {"notebook_result": json.dumps({"Result": "SUCCESS", "listMembers": ["a", "b"]})}
    result = LmwsProvider.parse_output(out)
    assert result["Result"] == "SUCCESS"
    assert result["listMembers"] == ["a", "b"]


def test_parse_output_accepts_dict_result():
    out = {"notebook_result": {"Result": "SUCCESS"}}
    assert LmwsProvider.parse_output(out)["Result"] == "SUCCESS"


def test_parse_output_raises_on_reported_failure():
    out = {"notebook_result": json.dumps({"Result": "FAILED", "message": "nope"})}
    with pytest.raises(PermanentError):
        LmwsProvider.parse_output(out)


def test_parse_output_raises_on_job_error():
    with pytest.raises(PermanentError):
        LmwsProvider.parse_output({"error": "RuntimeError: boom"})


def test_parse_output_raises_on_empty():
    with pytest.raises(PermanentError):
        LmwsProvider.parse_output({})


# ---------------------------------------------------------------------------
# build_step_kwargs — kwargs for DatabricksJobStepMixin
# ---------------------------------------------------------------------------

def test_build_step_kwargs_targets_vendored_notebook_on_classic_compute():
    provider = LmwsProvider()
    kwargs = provider.build_step_kwargs(
        LmwsAction.LIST_MEMBERS_ADD,
        step_id="lmws_add_x",
        list_name="x",
        members=["u"],
    )
    assert kwargs["step_id"] == "lmws_add_x"
    assert kwargs["notebook_path"] == LmwsProvider.notebook_path()
    assert os.path.basename(kwargs["notebook_path"]) == "lmws_group_management_job.py"
    assert kwargs["parameters"]["action"] == "list_members_add"
    # classic compute (not serverless) for control-plane reachability
    assert kwargs["compute"] is not None
    assert not kwargs["compute"].is_serverless


# ---------------------------------------------------------------------------
# run_action — inline submit + poll path (agent read tools)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_action_inline_success():
    fake_db = AsyncMock()
    fake_db.submit_job.return_value = "999"
    fake_db.get_run_status.return_value = {"is_completed": True, "is_successful": True}
    fake_db.get_run_output.return_value = {
        "notebook_result": json.dumps({"Result": "SUCCESS", "listMembers": ["a"]})
    }

    provider = LmwsProvider()
    with patch.object(provider, "_databricks_provider", return_value=fake_db):
        result = await provider.list_retrieve("edh_dbx_consultant")

    assert result["listMembers"] == ["a"]
    fake_db.import_notebook.assert_awaited_once()
    fake_db.submit_job.assert_awaited_once()
    # action routed correctly
    _, kwargs = fake_db.submit_job.call_args
    assert kwargs["notebook_task"]["base_parameters"]["action"] == "list_retrieve"


@pytest.mark.asyncio
async def test_run_action_inline_raises_on_job_failure():
    fake_db = AsyncMock()
    fake_db.submit_job.return_value = "999"
    fake_db.get_run_status.return_value = {
        "is_completed": True,
        "is_successful": False,
        "state_message": "cluster died",
    }

    provider = LmwsProvider()
    with patch.object(provider, "_databricks_provider", return_value=fake_db):
        with pytest.raises(PermanentError):
            await provider.member_retrieve("user1")
