# Provision Service Principal Instructions

**Goal**: Provision a new Service Principal for external apps, automation, or CI/CD.

## Information to Gather
1.  **Target Workspace**: Which workspace should this service principal be created in?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2.  **Service Name**: The name for the service principal.
    *   *Naming Convention*: `sp-{team}-{application}-{env}`.
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST use `does_service_principal_exist` (passing the `target_host`) to verify the service principal doesn't already exist.
3.  **Team**: The Entra ID group that will own and manage this service principal.
3.  **Environment**: The environment where this SP will be used.
    *   *Options*: `dev`, `test`, `prod`.
4.  **Cost Center**: The financial billing code responsible for any compute costs incurred by this SP.
5.  **Access Required**: Brief description of what this SP needs to reach (optional).
6.  **Justification**: Why is a service principal needed instead of user credentials?

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "service_principal_provision",
  "parameters": {
    "target_host": "...",
    "service_name": "...",
    "team": "...",
    "environment": "...",
    "cost_center": "...",
    "access_required": "...",
    "justification": "..."
  }
}
```
