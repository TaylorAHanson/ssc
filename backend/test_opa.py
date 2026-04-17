import asyncio
from app.providers.opa.client import OpaProvider
from app.core.config import settings

async def main():
    opa = OpaProvider(settings.opa_provider_config())
    input_data = {
        "workspace": {"name": "ws-enterprise-prod", "type": "enterprise", "environment": "prod"},
        "resource": {"id": "test-app", "type": "app"},
        "request_time": "2026-04-17T00:00:00Z",
        "allowlist_records": []
    }
    result = await opa.evaluate(
        policy_path="policies/apps_and_genie.rego",
        query="data.databricks.governance.apps_and_genie",
        input_data=input_data
    )
    print("Result keys:", result.keys())
    print("violation_reasons:", result.get("violation_reasons"))
    print("reason:", result.get("reason"))

asyncio.run(main())