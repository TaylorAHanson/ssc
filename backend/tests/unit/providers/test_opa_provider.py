import pytest
from app.providers.opa.client import OpaProvider

@pytest.mark.asyncio
async def test_opa_provider_local_eval():
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})
    
    # Check if OPA is installed locally. If not, skip test.
    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")
        
    input_data = {
        "workspace": {"name": "ws-enterprise-prod", "type": "enterprise", "environment": "prod"},
        "resource": {"id": "test-app", "type": "app", "idle_days": 40},
        "request_time": "2026-03-18T00:00:00Z",
        "allowlist_records": []
    }
    
    result = await provider.evaluate(
        policy_path="policies/apps.rego",
        query="data.databricks.governance.apps",
        input_data=input_data
    )
    
    assert result.get("is_violation") is True
    assert result.get("action") == "KILL"
    assert result.get("severity") == "HIGH"
    
    # Test with approved exception
    input_data["allowlist_records"] = [
        {
            "resource_id": "test-app",
            "status": "approved",
            "justification": "Approved app"
        }
    ]
    
    result = await provider.evaluate(
        policy_path="policies/apps.rego",
        query="data.databricks.governance.apps",
        input_data=input_data
    )
    
    assert result.get("is_violation") is True
    assert result.get("action") == "SKIPPED_ALLOWLIST"
    assert result.get("reason") == "Approved app"
    assert result.get("severity") == "NONE"
    
    # Test with pending exception
    input_data["allowlist_records"] = [
        {
            "resource_id": "test-app",
            "status": "pending",
            "justification": "Pending app"
        }
    ]
    
    result = await provider.evaluate(
        policy_path="policies/apps.rego",
        query="data.databricks.governance.apps",
        input_data=input_data
    )
    
    assert result.get("is_violation") is True
    assert result.get("action") == "PENDING_EXCEPTION"
    assert result.get("severity") == "MEDIUM"


@pytest.mark.asyncio
async def test_genie_space_requires_allowlist_in_enterprise_prod():
    """A Genie space in enterprise prod must be allowlisted, exactly like an app:
    no exception -> KILL/HIGH; approved -> SKIPPED_ALLOWLIST/NONE; pending ->
    PENDING_EXCEPTION/MEDIUM."""
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})

    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")

    input_data = {
        "workspace": {"name": "ws-enterprise-prod", "type": "enterprise", "environment": "prod"},
        "resource": {"id": "test-genie", "type": "genie_space"},
        "request_time": "2026-03-18T00:00:00Z",
        "allowlist_records": [],
    }

    result = await provider.evaluate(
        policy_path="policies/genie_spaces.rego",
        query="data.databricks.governance.genie_spaces",
        input_data=input_data,
    )
    assert result.get("is_violation") is True
    assert result.get("action") == "KILL"
    assert result.get("severity") == "HIGH"

    # Approved allowlist exception suppresses the kill.
    input_data["allowlist_records"] = [
        {"resource_id": "test-genie", "status": "approved", "justification": "Approved genie space"}
    ]
    result = await provider.evaluate(
        policy_path="policies/genie_spaces.rego",
        query="data.databricks.governance.genie_spaces",
        input_data=input_data,
    )
    assert result.get("is_violation") is True
    assert result.get("action") == "SKIPPED_ALLOWLIST"
    assert result.get("reason") == "Approved genie space"
    assert result.get("severity") == "NONE"

    # Pending exception holds enforcement.
    input_data["allowlist_records"] = [
        {"resource_id": "test-genie", "status": "pending", "justification": "Pending genie space"}
    ]
    result = await provider.evaluate(
        policy_path="policies/genie_spaces.rego",
        query="data.databricks.governance.genie_spaces",
        input_data=input_data,
    )
    assert result.get("is_violation") is True
    assert result.get("action") == "PENDING_EXCEPTION"
    assert result.get("severity") == "MEDIUM"


@pytest.mark.asyncio
async def test_genie_space_outside_enterprise_prod_is_allowed():
    """Genie spaces are only gated in enterprise prod; elsewhere they pass."""
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})

    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")

    input_data = {
        "workspace": {"name": "ws-domain-dev", "type": "domain", "environment": "dev"},
        "resource": {"id": "test-genie", "type": "genie_space"},
        "request_time": "2026-03-18T00:00:00Z",
        "allowlist_records": [],
    }
    result = await provider.evaluate(
        policy_path="policies/genie_spaces.rego",
        query="data.databricks.governance.genie_spaces",
        input_data=input_data,
    )
    assert result.get("is_violation") is False
    assert result.get("action") == "ALLOW"


@pytest.mark.asyncio
async def test_lakebase_requires_allowlist_in_enterprise_prod():
    """A Lakebase database instance in enterprise prod must be allowlisted, like
    apps/genie: no exception -> KILL/HIGH; approved -> SKIPPED_ALLOWLIST/NONE;
    pending -> PENDING_EXCEPTION/MEDIUM."""
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})

    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")

    input_data = {
        "workspace": {"name": "ws-enterprise-prod", "type": "enterprise", "environment": "prod"},
        "resource": {"id": "edh-ssc-prod-v5", "type": "lakebase"},
        "request_time": "2026-03-18T00:00:00Z",
        "allowlist_records": [],
    }

    result = await provider.evaluate(
        policy_path="policies/lakebase.rego",
        query="data.databricks.governance.lakebase",
        input_data=input_data,
    )
    assert result.get("is_violation") is True
    assert result.get("action") == "KILL"
    assert result.get("severity") == "HIGH"

    # Approved allowlist exception suppresses the kill.
    input_data["allowlist_records"] = [
        {"resource_id": "edh-ssc-prod-v5", "status": "approved", "justification": "Approved lakebase"}
    ]
    result = await provider.evaluate(
        policy_path="policies/lakebase.rego",
        query="data.databricks.governance.lakebase",
        input_data=input_data,
    )
    assert result.get("is_violation") is True
    assert result.get("action") == "SKIPPED_ALLOWLIST"
    assert result.get("reason") == "Approved lakebase"
    assert result.get("severity") == "NONE"

    # Pending exception holds enforcement.
    input_data["allowlist_records"] = [
        {"resource_id": "edh-ssc-prod-v5", "status": "pending", "justification": "Pending lakebase"}
    ]
    result = await provider.evaluate(
        policy_path="policies/lakebase.rego",
        query="data.databricks.governance.lakebase",
        input_data=input_data,
    )
    assert result.get("is_violation") is True
    assert result.get("action") == "PENDING_EXCEPTION"
    assert result.get("severity") == "MEDIUM"


@pytest.mark.asyncio
async def test_lakebase_outside_enterprise_prod_is_allowed():
    """Lakebase instances are only gated in enterprise prod; elsewhere they pass."""
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})

    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")

    input_data = {
        "workspace": {"name": "ws-domain-prod", "type": "domain", "environment": "prod"},
        "resource": {"id": "edh-ssc-prod-v5", "type": "lakebase"},
        "request_time": "2026-03-18T00:00:00Z",
        "allowlist_records": [],
    }
    result = await provider.evaluate(
        policy_path="policies/lakebase.rego",
        query="data.databricks.governance.lakebase",
        input_data=input_data,
    )
    assert result.get("is_violation") is False
    assert result.get("action") == "ALLOW"
