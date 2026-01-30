import asyncio
import os
import sys
from dotenv import load_dotenv

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from app.tools.finops.get_cost_summary import GetCostSummaryTool
from app.tools.finops.get_efficiency import GetResourceEfficiencyTool
from app.tools.governance.check_permissions import CheckObjectPermissionsTool
from app.tools.governance.audit_access import AuditUserAccessTool

async def main():
    print("=== FinOps & Governance Tool Verification Script ===\n")
    
    # 1. Check Cost Summary
    print("--- Testing GetCostSummaryTool ---")
    try:
        tool = GetCostSummaryTool()
        print("Executing get_cost_summary (last 30 days)...")
        # Use dynamic dates
        from datetime import datetime, timedelta
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        result = await tool.execute(start_date=start, end_date=end, granularity="total")
        print(f"Success! Result keys: {result.keys()}")
        print(f"Rows returned: {len(result.get('costs', []))}\n")
    except Exception as e:
        print(f"Failed: {e}\n")

    # 2. Check Efficiency
    print("--- Testing GetResourceEfficiencyTool ---")
    try:
        tool = GetResourceEfficiencyTool()
        print("Executing get_resource_efficiency_metrics (idle clusters over 24h)...")
        result = await tool.execute(metric="idle_time", threshold_hours=24)
        print(f"Success! Found {len(result.get('inefficient_resources', []))} potentially idle resources.\n")
    except Exception as e:
        print(f"Failed: {e}\n")

    # 3. Check Permissions (Governance)
    print("--- Testing CheckObjectPermissionsTool ---")
    try:
        tool = CheckObjectPermissionsTool()
        # Try to find a catalog to check
        # Hardcoding 'main' as it's common, or fallback
        target = "main" 
        print(f"Executing check_object_permissions on CATALOG '{target}'...")
        result = await tool.execute(object_type="CATALOG", object_name=target)
        print(f"Success! Grants found: {len(result.get('grants', []))}\n")
    except Exception as e:
        print(f"Failed: {e}\n")

    print("=== Verification Complete ===")

if __name__ == "__main__":
    asyncio.run(main())
