import asyncio
import os
import sys

# Ensure backend is in path
script_dir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, script_dir)

from app.providers.opa.client import OpaProvider
from datetime import datetime

async def test_opa():
    # Use the homebrew OPA binary specified by the user
    os.environ["PATH"] = f"/opt/homebrew/bin:{os.environ.get('PATH', '')}"
    
    # Configure the provider to use the local binary
    provider = OpaProvider({"use_local_binary": True, "policies_dir": "policies", "opa_binary": "/opt/homebrew/bin/opa"})
    
    input_data = {
        "workspace": {"name": "enterprise-prod", "type": "enterprise", "environment": "prod"},
        "resource": {"id": "my-cluster", "type": "cluster", "cluster_type": "interactive", "access_mode": "shared", "policy_id": None},
        "request_time": datetime.utcnow().isoformat(),
        "allowlist_records": []
    }
    
    print("Testing OPA evaluation for 'compute_and_jobs.rego'...")
    try:
        res = await provider.evaluate("compute_and_jobs.rego", "data.databricks.governance.compute_and_jobs", input_data)
        print(f"\\nResult:\\n{res}")
    except Exception as e:
        print(f"Error evaluating OPA: {e}")

if __name__ == "__main__":
    asyncio.run(test_opa())
