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
@pytest.mark.parametrize(
    "expires_at, suppressed",
    [
        pytest.param("__omit__", True, id="key-absent"),
        # The regression: the app ALWAYS emits this key and sets it to null for a
        # never-expiring exception (see sentinel.py's allowlist_records builder).
        # Rego's `not exception.expires_at` doesn't catch null, so every "Never"
        # exception was silently discarded and the resource stayed KILL/HIGH.
        pytest.param(None, True, id="null-never-expires"),
        pytest.param("2099-01-01T00:00:00", True, id="future-date"),
        pytest.param("2020-01-01T00:00:00", False, id="past-date-expired"),
    ],
)
async def test_approved_exception_applies_unless_actually_expired(expires_at, suppressed):
    """An approved exception must suppress enforcement whenever it hasn't expired,
    including when it never expires — however "never" is encoded on the wire."""
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})

    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")

    record = {"resource_id": "test-app", "status": "approved", "justification": "Approved app"}
    if expires_at != "__omit__":
        record["expires_at"] = expires_at

    result = await provider.evaluate(
        policy_path="policies/apps.rego",
        query="data.databricks.governance.apps",
        input_data={
            "workspace": {"name": "ws-enterprise-prod", "type": "enterprise", "environment": "prod"},
            "resource": {"id": "test-app", "type": "app"},
            "request_time": "2026-03-18T00:00:00+00:00",
            "allowlist_records": [record],
        },
    )

    if suppressed:
        assert result.get("action") == "SKIPPED_ALLOWLIST"
        assert result.get("severity") == "NONE"
    else:
        assert result.get("action") == "KILL"
        assert result.get("severity") == "HIGH"


@pytest.mark.asyncio
async def test_exception_for_a_different_resource_does_not_suppress():
    """Guards the fix above from over-correcting into blanket suppression."""
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})

    if not provider.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")

    result = await provider.evaluate(
        policy_path="policies/apps.rego",
        query="data.databricks.governance.apps",
        input_data={
            "workspace": {"name": "ws-enterprise-prod", "type": "enterprise", "environment": "prod"},
            "resource": {"id": "test-app", "type": "app"},
            "request_time": "2026-03-18T00:00:00+00:00",
            "allowlist_records": [
                {"resource_id": "some-other-app", "status": "approved", "expires_at": None}
            ],
        },
    )
    assert result.get("action") == "KILL"
    assert result.get("severity") == "HIGH"


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
