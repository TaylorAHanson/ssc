from app.tools.catalog_existence import DoesCatalogExistTool
from app.agents.tools.execute_workflow import ExecuteWorkflowTool

# Registry of available tools
AVAILABLE_TOOLS = [
    DoesCatalogExistTool(),
    ExecuteWorkflowTool()
]

