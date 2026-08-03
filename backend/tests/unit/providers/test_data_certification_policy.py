"""Certification policy behaviour, with emphasis on the empty-contract case.

Every rule in ``data_certification.rego`` is scoped by
``some asset in input.resource.assets``. That makes an empty asset list the most
dangerous input in the policy: no rule can fire, so the product scores a vacuous
100% and the sentinel auto-certifies it. It is also a *routine* input — contract
generation skips tables the service principal can't read and emits an empty
``schema: []`` — so these tests pin the behaviour rather than treating it as an
edge case.
"""
import pytest

from app.providers.opa.client import OpaProvider

CERT_POLICY = "policies/data_certification.rego"
CERT_QUERY = "data.databricks.governance.data_certification"

# An asset that satisfies every rule, used as the baseline for "should certify".
COMPLIANT_ASSET = {
    "name": "main.sales.orders",
    "type": "table",
    "tags": {
        "dataset": "sales-order",
        "reliability_window": "7",
        "data_owner": "owner@example.com",
        "approver_group": "approvers",
        "access_group": "readers",
    },
    "failed_rule_count": 0,
    "failed_rules": [],
    "table_exists": True,
    "catalog_description": "Sales catalog",
    "schema_description": "Orders schema",
    "all_columns_have_descriptions": True,
    "missing_column_descriptions": [],
    "rbac_defined": True,
    "rbac_readable": True,
}


@pytest.fixture
def provider():
    p = OpaProvider({"use_local_binary": True, "policies_dir": "policies"})
    if not p.health_check():
        pytest.skip("OPA binary not found on path, skipping local eval test")
    return p


async def evaluate(provider, assets, invalid_yaml=False):
    return await provider.evaluate(
        policy_path=CERT_POLICY,
        query=CERT_QUERY,
        input_data={
            "resource": {
                "id": "sales-order",
                "type": "data_product",
                "invalid_yaml": invalid_yaml,
                "assets": assets,
            }
        },
    )


def failing_rule_ids(result):
    return {r["id"] for r in result.get("rule_results", []) if not r["passed"]}


@pytest.mark.asyncio
async def test_contract_declaring_no_assets_does_not_certify(provider):
    """The regression: an empty contract used to pass every rule and CERTIFY."""
    result = await evaluate(provider, assets=[])

    assert result["action"] != "CERTIFY"
    assert result["is_violation"] is True
    assert "assets_declared" in failing_rule_ids(result)


@pytest.mark.asyncio
async def test_missing_assets_key_is_treated_as_no_assets(provider):
    """Discovery builds `assets` from the contract, so the key can be absent
    entirely rather than present-and-empty. Both mean 'nothing was checked'."""
    result = await provider.evaluate(
        policy_path=CERT_POLICY,
        query=CERT_QUERY,
        input_data={"resource": {"id": "sales-order", "type": "data_product"}},
    )

    assert result["action"] != "CERTIFY"
    assert "assets_declared" in failing_rule_ids(result)


@pytest.mark.asyncio
async def test_unparseable_contract_reports_only_the_parse_failure(provider):
    """Invalid YAML implies an empty asset list; reporting both would bury the
    actual cause under a symptom."""
    result = await evaluate(provider, assets=[], invalid_yaml=True)

    assert failing_rule_ids(result) == {"yaml_valid"}
    assert result["action"] != "CERTIFY"


@pytest.mark.asyncio
async def test_fully_compliant_product_still_certifies(provider):
    """Guards the fix above from over-correcting into 'nothing can certify'."""
    result = await evaluate(provider, assets=[COMPLIANT_ASSET])

    assert result["action"] == "CERTIFY"
    assert result["is_violation"] is False
    assert failing_rule_ids(result) == set()


@pytest.mark.asyncio
async def test_declared_asset_is_still_held_to_the_other_rules(provider):
    """assets_declared passing must not imply anything else passed."""
    bare = {
        "name": "main.sales.orders",
        "type": "table",
        "tags": {},
        "failed_rule_count": -1,
        "table_exists": True,
        "all_columns_have_descriptions": False,
        "rbac_defined": False,
        "rbac_readable": True,
    }
    result = await evaluate(provider, assets=[bare])

    failing = failing_rule_ids(result)
    assert "assets_declared" not in failing
    assert {"required_tags", "reliability_window_tag", "access_controls_defined"} <= failing
    assert result["action"] != "CERTIFY"
