# Workspace Access Instructions

**Goal**: Request access to an existing Databricks workspace.

## Information to Gather
1. **Workspace Name**: The name of the workspace the user needs access to.
   * *Action*: You MUST use the `list_workspaces` tool to query `system.access.workspaces_latest` and find the exact workspace name and ID. Do not guess the name. If the user provides a partial name, look it up and confirm the exact name with them.
2. **Justification**: A brief reason why the user needs access to this workspace.
   * *Action*: For lower environments (`dev`, `tst`, `stg`), critically evaluate the user's justification. Lower environments are for active development and testing. If their justification is weak or sounds like they just want to view production data, challenge them and ensure they actually need development access.

## Guidance & Guardrails
* **Existing vs. New**: This workflow is ONLY for gaining access to an **already existing** workspace. If the user is asking to create a *new* workspace, stop and guide them to the `workspace_provision` workflow instead.
* **Workspace Type & Environment Determination**: Determine the `workspace_type` and `environment` based on the exact workspace name.
  * *Type*: If the workspace name contains `enterprise` (case-insensitive), it is an `enterprise` workspace. Otherwise, it is a `domain` workspace.
  * *Environment*: The name will typically contain the 3-letter environment (e.g., `dev`, `tst`, `stg`, or `prd`). Extract this.
* **Approval Process**: Inform the user about the approval process based on the workspace type and environment:
  * **Enterprise Prod**: Access to `enterprise` workspaces in the `prd` environment is automatically approved with no human-in-the-loop required.
  * **Lower Environments & Domain Workspaces**: Access to ANY lower environment (`dev`, `tst`, `stg`) OR any `domain` workspace requires Manager Approval before access is granted.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "workspace_access",
  "parameters": {
    "workspace_name": "exact-workspace-name-from-tool",
    "workspace_id": "exact-workspace-id-from-tool",
    "workspace_type": "enterprise or domain",
    "environment": "dev, tst, stg, or prd",
    "justification": "..."
  }
}
```