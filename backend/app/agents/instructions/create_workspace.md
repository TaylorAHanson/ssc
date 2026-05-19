# Create Workspace Instructions

**Goal**: Provision a new Databricks Workspace.

## Information to Gather
1.  **Business Domain**: The specific business unit or domain this workspace will serve (e.g., `legal`, `finance`, `hr`, `marketing`, `product`, `sales`, `engineering`).
2.  **Environment**: Target environment.
    *   *Options*: `dev`, `test`, `stage`, `prod`, or `all` (for all environments).
3.  **Cost Center**: The financial billing code or cost center responsible for the compute costs in this workspace.
4.  **Data Sensitivity**: The highest classification of data that will be processed here.
    *   *Options*: `green`, `yellow`, `red`, `black`.
5.  **Workspace Admin Group**: The Entra ID group that will be granted admin rights. (Individual users cannot be workspace admins).
6.  **Justification**: A detailed reason for needing a *new* workspace.
    *   *Analysis Logic*: A new workspace is only valid for **new business domain onboarding**. If the user is part of an existing domain that already has a workspace, push them to request access to that existing workspace instead. If the justification is weak or doesn't align with domain onboarding, challenge it.
7.  **Workspace Name**: This is hardcoded to a specific format. Do NOT ask the user for the name.
    *   *Format*: `ws-{business_domain}-{environment}` (e.g., `ws-finance-prod`).
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST use `list_workspaces` to verify that this exact workspace name does NOT already exist.

## Requirements
*   **Training**: Note that the user will need to complete the **Platform Administration** training path before the workspace can be fully provisioned. Offer a link to that training path in the response.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "workspace_provision",
  "parameters": {
    "business_domain": "...",
    "environment": "...",
    "cost_center": "...",
    "data_sensitivity": "...",
    "admin_group": "...",
    "justification": "...",
    "workspace_name": "ws-{business_domain}-{environment}",
    "region": "eastus2"
  }
}
```
