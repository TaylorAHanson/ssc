"""Tests for the ``terramate`` gate resolving itself from the Terramate API (ADR-0004).

Under the Terramate GitOps lifecycle:
1. terramate_provision creates the request and opens a PR on GitHub.
2. A human reviews the plan and merges the PR on GitHub.
3. CI applies Terraform and reports outcome back to Terramate API.
4. The terramate gate polls until the request reaches terminal succeeded or failed status.
"""
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.poller import _terramate_gate_from_api, _v2_resume_value

REQUEST = types.SimpleNamespace(id="req-1a2b3c4d", parameters={"terramate_request_id": "tm-req-123"})


def _terramate_created_fact(terramate_request_id="tm-req-123"):
    return types.SimpleNamespace(event_data={"terramate_request_id": terramate_request_id, "status": "pending"})


async def _resolve_gate(api_response, fact=None, api_raises=None, interrupt_payload=None):
    """Run the gate against a stubbed Terramate provider, returning (resume_value, add_fact, provider)."""
    provider = MagicMock()
    if api_raises:
        provider.get_request = AsyncMock(side_effect=api_raises)
    else:
        provider.get_request = AsyncMock(return_value=api_response)

    db = MagicMock()
    result = types.SimpleNamespace(
        interrupted=True,
        interrupt_payload=interrupt_payload or {"type": "terramate", "terramate_request_id": "tm-req-123"},
    )

    with patch(
        "app.state_machines.facts.get_latest_fact",
        return_value=_terramate_created_fact() if fact is None else fact,
    ), patch(
        "app.state_machines.facts.add_fact"
    ) as add_fact, patch(
        "app.workflows.tools._common._get_terramate_provider",
        return_value=provider,
    ):
        value = await _terramate_gate_from_api(db, REQUEST, result)
    return value, add_fact, provider


@pytest.mark.asyncio
async def test_succeeded_status_advances_the_gate():
    """Terminal 'succeeded' means all steps applied; gate must approve."""
    value, add_fact, provider = await _resolve_gate(
        {
            "id": "tm-req-123",
            "status": "succeeded",
            "steps": [{"ordinal": 0, "key": "create", "status": "done"}],
        }
    )
    assert value == {"approved": True}
    provider.get_request.assert_awaited_once_with("tm-req-123")
    _db, request_id, fact_type, payload = add_fact.call_args.args
    assert (request_id, fact_type) == ("req-1a2b3c4d", "terramate_provision_succeeded")
    assert payload["status"] == "succeeded"


@pytest.mark.asyncio
async def test_in_progress_status_keeps_waiting_and_records_active_pr():
    """Non-terminal in_progress status must keep waiting and record active PR."""
    value, add_fact, _ = await _resolve_gate(
        {
            "id": "tm-req-123",
            "status": "in_progress",
            "steps": [
                {
                    "ordinal": 0,
                    "key": "create",
                    "status": "submitted",
                    "pr_url": "https://github.com/org/repo/pull/42",
                }
            ],
        }
    )
    assert value is None
    # Verifies active PR url fact was written
    add_fact.assert_called_once()
    _db, request_id, fact_type, payload = add_fact.call_args.args
    assert (request_id, fact_type) == ("req-1a2b3c4d", "terramate_pr_active")
    assert payload["pr_url"] == "https://github.com/org/repo/pull/42"


@pytest.mark.asyncio
async def test_failed_status_rejects_the_request():
    """Terminal 'failed' must reject with the failed step's details."""
    value, add_fact, _ = await _resolve_gate(
        {
            "id": "tm-req-123",
            "status": "failed",
            "steps": [
                {"ordinal": 0, "key": "create", "status": "failed"},
            ],
        }
    )
    assert value["approved"] is False
    assert "create" in value["reason"]
    _db, request_id, fact_type, payload = add_fact.call_args.args
    assert (request_id, fact_type) == ("req-1a2b3c4d", "terramate_provision_failed")
    assert payload["status"] == "failed"


@pytest.mark.asyncio
async def test_cancelled_status_rejects_the_request():
    """Terminal 'cancelled' must reject the request."""
    value, add_fact, _ = await _resolve_gate(
        {
            "id": "tm-req-123",
            "status": "cancelled",
            "steps": [],
        }
    )
    assert value["approved"] is False
    assert "cancelled" in value["reason"]
    _db, request_id, fact_type, payload = add_fact.call_args.args
    assert (request_id, fact_type) == ("req-1a2b3c4d", "terramate_provision_failed")
    assert payload["status"] == "cancelled"


@pytest.mark.asyncio
async def test_api_being_down_leaves_the_gate_waiting():
    """A transient error contacting the Terramate API leaves the gate waiting."""
    value, add_fact, _ = await _resolve_gate(None, api_raises=RuntimeError("503 Service Unavailable"))
    assert value is None
    add_fact.assert_not_called()


@pytest.mark.asyncio
async def test_not_found_leaves_the_gate_waiting():
    """If the Terramate API reports not found (propagating/transient), leave waiting."""
    value, add_fact, _ = await _resolve_gate(None)
    assert value is None
    add_fact.assert_not_called()


@pytest.mark.asyncio
async def test_missing_request_id_leaves_gate_waiting():
    """If no terramate request id is present anywhere, leave waiting."""
    req_no_params = types.SimpleNamespace(id="req-empty", parameters={})
    db = MagicMock()
    result = types.SimpleNamespace(interrupted=True, interrupt_payload={"type": "terramate"})

    with patch("app.state_machines.facts.get_latest_fact", return_value=None):
        value = await _terramate_gate_from_api(db, req_no_params, result)
    assert value is None


@pytest.mark.asyncio
async def test_manual_succeeded_fact_overrides_api():
    """An existing terramate_provision_succeeded fact short-circuits the lookup."""
    result = types.SimpleNamespace(
        interrupted=True,
        interrupt_payload={"type": "terramate", "terramate_request_id": "tm-req-123"},
    )
    db = MagicMock()
    with patch(
        "app.state_machines.facts.has_fact",
        side_effect=lambda _db, _rid, ft: ft == "terramate_provision_succeeded",
    ):
        value = await _v2_resume_value(db, REQUEST, result)
    assert value == {"approved": True}


@pytest.mark.asyncio
async def test_v2_resume_value_routes_terramate_gate():
    """_v2_resume_value routes gtype='terramate' and 'terramate_status' to _terramate_gate_from_api."""
    for gtype in ("terramate", "terramate_status"):
        result = types.SimpleNamespace(
            interrupted=True,
            interrupt_payload={"type": gtype, "terramate_request_id": "tm-req-123"},
        )
        db = MagicMock()
        with patch("app.state_machines.facts.has_fact", return_value=False), patch(
            "app.workers.poller._terramate_gate_from_api", new_callable=AsyncMock
        ) as mock_gate:
            mock_gate.return_value = {"approved": True}
            value = await _v2_resume_value(db, REQUEST, result)
            assert value == {"approved": True}
            mock_gate.assert_awaited_once_with(db, REQUEST, result)
