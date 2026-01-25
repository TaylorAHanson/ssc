# GitHub Repository Creation Instructions

**Goal**: Create a new GitHub repository in the organization.

## Information to Gather
1.  **Repository Name**: The desired name for the repo.
    *   *Validation*: Alphanumeric with hyphens only.
2.  **Description**: A short description of the repository's purpose.
3.  **Visibility**: Public (Internal) or Private?
    *   *Options*: `internal`, `private`.
4.  **Template**: (Optional) Do you want to start from a template?
    *   *Options*: `python-template`, `react-template`, `none`.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "github_repo_creation",
  "parameters": {
    "repo_name": "...",
    "description": "...",
    "visibility": "...",
    "template": "..."
  }
}
```
