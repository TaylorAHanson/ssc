# Project Onboarding Instructions

**Goal**: Help the user onboard a new project team by provisioning a workspace, creating a repository, and granting access.

## Context
This is a compound workflow that orchestrates:
1. Databricks Workspace Creation
2. GitHub Repository Creation
3. Workspace Access Grants (for team members)
4. Data Access Grants (for datasets)

## Information to Gather
You must gather the following information from the user before executing the workflow. Batch your questions to gather multiple pieces of information at once.

1.  **Project Name**: The name of the project.
    *   *Validation*: Must be alphanumeric, no spaces (use hyphens).
    *   *Existence Check (REQUIRED)*: You MUST use `list_workspaces` to ensure a workspace for this project doesn't already exist.
2.  **Cost Center**: The billing code for this project.
    *   *Validation*: Must be a 6-digit number.
3.  **Data Sensitivity**: The highest classification of data that will be processed in this project.
    *   *Options*: `green`, `yellow`, `red`, `black`.
4.  **Admin Group**: The Entra ID group that will be granted admin rights over the workspace and repository.
    *   *Enterprise Policy*: Individual users CANNOT be admins of shared resources. It must be an Entra ID group.
5.  **Repository Name**: Name for the GitHub repository.
    *   *Default*: Suggest `{project_name}-repo` if not provided.
6.  **Team Members**: A list of email addresses of team members who need access to the workspace.
7.  **Datasets**: A list of datasets (catalogs, schemas, or tables) the team needs access to.
    *   *Validation*: Ask for the specific names (e.g., `main.default.my_table`).

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "project_onboarding",
  "parameters": {
    "project_name": "...",
    "cost_center": "...",
    "data_sensitivity": "...",
    "admin_group": "...",
    "repo_name": "...",
    "team_members": ["email1@example.com", "email2@example.com"],
    "datasets": [
      {"name": "catalog.schema.table1", "type": "table", "access_level": "read"}
    ]
  }
}
```

## Confirmation
Before calling the tool, summarize the details to the user and ask for confirmation.
