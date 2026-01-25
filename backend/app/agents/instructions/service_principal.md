# Provision Service Principal Instructions

**Goal**: Provision a new Service Principal for external apps, automation, or CI/CD.

## Information to Gather
1.  **Service Name**: The name for the service principal.
    *   *Naming Convention*: `sp-{team}-{application}-{env}`.
2.  **Team**: The team that owns this service principal.
3.  **Environment**: The environment where this SP will be used.
    *   *Options*: `dev`, `test`, `prod`.
4.  **Access Required**: Brief description of what this SP needs to reach (optional).

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "service_principal_provision",
  "parameters": {
    "service_name": "...",
    "team": "...",
    "environment": "...",
    "access_required": "..."
  }
}
```
