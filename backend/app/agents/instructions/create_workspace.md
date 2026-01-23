# Create Workspace Instructions

**Goal**: Provision a new Databricks Workspace.

## Information to Gather
1.  **Workspace Name**: The desired name for the workspace.
    *   *Validation*: Must be globally unique, alphanumeric with hyphens. pattern: `^[a-z0-9-]+$`
    *   *Naming Convention*: Typically `{team}-{env}-{region}` (e.g., `finance-prod-eastus`).
2.  **Environment**: Target environment.
    *   *Options*: `dev`, `test`, `stage`, `prod`.
3.  **Region**: Cloud region.
    *   *Default*: `eastus2` (unless specified otherwise).

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "workspace_provision",
  "parameters": {
    "workspace_name": "...",
    "environment": "...",
    "region": "..."
  }
}
```
