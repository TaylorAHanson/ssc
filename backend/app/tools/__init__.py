from app.tools.self_service.catalog_existence import DoesCatalogExistTool
from app.tools.self_service.get_catalog_list import GetCatalogListTool
from app.tools.self_service.get_schema_list import GetSchemaListTool
from app.tools.self_service.get_table_list import GetTableListTool
from app.tools.execute_workflow import ExecuteWorkflowTool
from app.tools.self_service.search_requests import SearchRequestsTool
from app.tools.self_service.search_approvals import SearchApprovalsTool
from app.tools.self_service.search_user_entitlements import SearchUserEntitlementsTool

# FinOps Tools
from app.tools.finops.get_cost_summary import GetCostSummaryTool
from app.tools.finops.get_efficiency import GetResourceEfficiencyTool
from app.tools.finops.check_tagging import CheckTaggingComplianceTool
from app.tools.finops.get_forecast import GetForecastedSpendTool

# Governance Tools
from app.tools.governance.check_permissions import CheckObjectPermissionsTool
from app.tools.governance.audit_access import AuditUserAccessTool
from app.tools.governance.search_audit_logs import SearchAuditLogsTool
from app.tools.governance.check_overprovisioning import CheckOverprovisionedUsersTool
from app.tools.governance.check_orphans import CheckOrphanedAssetsTool
from app.tools.governance.check_quality import CheckAssetQualityTool
from app.tools.governance.list_workspaces import ListWorkspacesTool

# Registry of available tools
AVAILABLE_TOOLS = [
    DoesCatalogExistTool(),
    GetCatalogListTool(),
    GetSchemaListTool(),
    GetTableListTool(),
    ExecuteWorkflowTool(),
    SearchRequestsTool(),
    SearchApprovalsTool(),
    SearchUserEntitlementsTool(),
    GetCostSummaryTool(),
    GetResourceEfficiencyTool(),
    CheckTaggingComplianceTool(),
    GetForecastedSpendTool(),
    CheckObjectPermissionsTool(),
    AuditUserAccessTool(),
    SearchAuditLogsTool(),
    CheckOverprovisionedUsersTool(),
    CheckOrphanedAssetsTool(),
    CheckAssetQualityTool(),
    ListWorkspacesTool()
]

def get_read_only_tools():
    """Returns tools that don't perform destructive actions or workflow executions."""
    # Exclude ExecuteWorkflowTool
    return [t for t in AVAILABLE_TOOLS if not t.name == "execute_workflow"]

