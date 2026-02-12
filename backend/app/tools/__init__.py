from app.tools.self_service.catalog_existence import does_catalog_exist
from app.tools.self_service.get_catalog_list import get_catalog_list
from app.tools.self_service.get_schema_list import get_schema_list
from app.tools.self_service.get_table_list import get_table_list
from app.tools.execute_workflow import execute_workflow
from app.tools.check_resource_access import check_resource_access
from app.tools.self_service.search_requests import search_requests
from app.tools.self_service.search_approvals import search_approvals
from app.tools.self_service.search_user_entitlements import search_user_entitlements
from app.tools.self_service.search_events import search_events
from app.tools.self_service.check_github_repo import check_github_repo
from app.tools.self_service.list_github_templates import list_github_templates
from app.tools.self_service.find_owner import find_owner
from app.tools.self_service.check_training_status import check_training_status

# FinOps Tools
from app.tools.finops.get_cost_summary import get_cost_summary
from app.tools.finops.get_efficiency import get_resource_efficiency_metrics
from app.tools.finops.check_tagging import check_tagging_compliance
from app.tools.finops.get_forecast import get_forecasted_spend

# Governance Tools
from app.tools.governance.check_permissions import check_object_permissions
from app.tools.governance.audit_access import audit_user_access
from app.tools.governance.search_audit_logs import search_audit_logs
from app.tools.governance.check_overprovisioning import check_overprovisioned_users
from app.tools.governance.check_orphans import check_orphaned_assets
from app.tools.governance.check_quality import check_asset_quality
from app.tools.governance.list_workspaces import list_workspaces

# Registry of available tools
AVAILABLE_TOOLS = [
    does_catalog_exist,
    get_catalog_list,
    get_schema_list,
    get_table_list,
    execute_workflow,
    check_resource_access,
    search_requests,
    search_approvals,
    search_user_entitlements,
    get_cost_summary,
    get_resource_efficiency_metrics,
    check_tagging_compliance,
    get_forecasted_spend,
    check_object_permissions,
    audit_user_access,
    search_audit_logs,
    check_overprovisioned_users,
    check_orphaned_assets,
    check_asset_quality,
    list_workspaces,
    search_events,
    check_github_repo,
    list_github_templates,
    find_owner,
    check_training_status
]

def get_read_only_tools():
    """Returns tools that don't perform destructive actions or workflow executions."""
    # Exclude execute_workflow
    return [t for t in AVAILABLE_TOOLS if not t.name == "execute_workflow"]
