# Project Onboarding Instructions

**Goal**: Help the user onboard a new project team by provisioning a workspace, creating a repository, and granting access.

## Context
This is a compound workflow that orchestrates:
1. Databricks Workspace Creation
2. GitHub Repository Creation
3. Access Granting

## Information to Gather
You must gather the following information from the user before executing the workflow. Ask one question at a time.

1.  **Project Name**: The name of the project.
    *   *Validation*: Must be alphanumeric, no spaces (use hyphens).
2.  **Cost Center**: The billing code for this project.
    *   *Validation*: Must be a 6-digit number.
3.  **Repository Name**: Name for the GitHub repository.
    *   *Default*: Suggest `{project_name}-repo` if not provided.
4.  **Admin Email**: The email of the primary data owner.
    *   *Default*: Use the current user's email if they confirm.

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "project_onboarding",
  "parameters": {
    "project_name": "...",
    "cost_center": "...",
    "repo_name": "...",
    "admin_email": "..."
  }
}
```

## Confirmation
Before calling the tool, summarize the details to the user and ask for confirmation.
