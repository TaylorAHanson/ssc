from app.tools.catalog_existence import DoesCatalogExistTool
from app.tools.get_catalog_list import GetCatalogListTool
from app.tools.get_schema_list import GetSchemaListTool
from app.tools.get_table_list import GetTableListTool
from app.agents.tools.execute_workflow import ExecuteWorkflowTool
from app.tools.search_requests import SearchRequestsTool
from app.tools.search_approvals import SearchApprovalsTool
from app.tools.search_user_entitlements import SearchUserEntitlementsTool

# Registry of available tools
AVAILABLE_TOOLS = [
    DoesCatalogExistTool(),
    GetCatalogListTool(),
    GetSchemaListTool(),
    GetTableListTool(),
    ExecuteWorkflowTool(),
    SearchRequestsTool(),
    SearchApprovalsTool(),
    SearchUserEntitlementsTool()
]

