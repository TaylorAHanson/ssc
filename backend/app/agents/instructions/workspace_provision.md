# Create Workspace Instructions

**Goal**: Provision a brand-new Databricks workspace (`ws-<domain>-<env>`) to onboard a new business domain; not for getting access to a workspace that already exists.

## Scope Check (before gathering anything)
A request like "I want a new domain called supply chain" can mean several things: a workspace for a domain that has none, another environment for a domain that already has one, access to the domain's existing workspace, or just a catalog for the domain's data. Before asking for any field below:
1.  Use `list_workspaces` with `name_contains` (or `get_target_workspaces`) to look for existing workspaces for that domain. `name_contains` ignores spaces, dashes and underscores, so "supply chain" also finds `ws-supplychain-prod`; try likely abbreviations too (e.g. `scm`).
2.  If one exists, or the user's goal is unclear, tell them what you found and ask which they need, offering 2-4 concrete options (e.g. access to `ws-supplychain-prod`, a new `dev` environment for it, or a catalog in an existing workspace). Do NOT list this workflow's fields in that message, and do NOT say "it already exists, but if you still want one I need...".
3.  Gather the fields below only once the user confirms they need a new workspace. If they need access, switch to `workspace_access`.

## Information to Gather
1.  **Business Domain**: The specific business unit or domain this workspace will serve (e.g., `legal`, `finance`, `hr`, `marketing`, `product`, `sales`, `engineering`).
2.  **Environment**: Target environment.
    *   *Options*: `dev`, `test`, `stage`, `prod`, or `all` (for all environments).
3.  **Cost Center**: The financial billing code or cost center responsible for the compute costs in this workspace.
4.  **Data Sensitivity**: The highest classification of data that will be processed here.
    *   *Options*: `green`, `yellow`, `red`, `black`.
5.  **Workspace Admin Group**: The LMWS group/list that will be granted admin rights. (Individual users cannot be workspace admins).
6.  **Justification**: A detailed reason for needing a *new* workspace.
    *   *Analysis Logic*: A new workspace is only valid for **new business domain onboarding** (or a missing environment for one). The Scope Check above should already have steered users of an existing domain to request access to its workspace. If the justification is weak or doesn't align with domain onboarding, ask about it before going further rather than recording it as given.
7.  **Workspace Name**: This is hardcoded to a specific format. Do NOT ask the user for the name.
    *   *Format*: `ws-{business_domain}-{environment}` (e.g., `ws-finance-prod`).
    *   *Existence Check (REQUIRED)*: As soon as you know the domain and environment, use `list_workspaces` to verify that this exact workspace name does NOT already exist. If it does, go back to the Scope Check question instead of continuing.

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
