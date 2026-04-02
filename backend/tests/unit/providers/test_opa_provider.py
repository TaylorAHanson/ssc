import pytest
from app.providers.opa.client import OpaProvider

@pytest.mark.asyncio
async def test_opa_provider_local_eval():
    provider = OpaProvider({"use_local_binary": True})
    
    # Check if OPA is installed locally. If not, skip test.
    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")
        
    input_data = {
        "workspace": {"name": "ws-enterprise-prod", "type": "enterprise"},
        "resource": {"id": "test-app", "type": "app"},
        "request_time": "2026-03-18T00:00:00Z"
    }
    
    result = await provider.evaluate(
        policy_path="policies/asset_allowlist.rego",
        query="data.databricks.governance.asset_allowlist",
        input_data=input_data
    )
    
    assert result.get("is_violation") is True
    assert result.get("action") == "KILL"
    assert result.get("severity") == "CRITICAL"
    
    # Test with approved exception
    input_data["allowlist_records"] = [
        {
            "resource_id": "test-app",
            "status": "approved",
            "justification": "Approved app"
        }
    ]
    
    result = await provider.evaluate(
        policy_path="policies/asset_allowlist.rego",
        query="data.databricks.governance.asset_allowlist",
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
        policy_path="policies/asset_allowlist.rego",
        query="data.databricks.governance.asset_allowlist",
        input_data=input_data
    )
    
    assert result.get("is_violation") is True
    assert result.get("action") == "PENDING_EXCEPTION"
    assert result.get("severity") == "MEDIUM"
