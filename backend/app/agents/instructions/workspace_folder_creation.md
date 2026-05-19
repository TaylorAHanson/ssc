# Workspace Folder Creation

**Goal**: Help the user create a new folder in a Databricks workspace.

## Context
This workflow allows users to request the creation of a new folder in the Workspace file system.

## Information to Gather
1. **Target Workspace**: Which workspace should this folder be created in?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2. **Folder Path**: The full path where the folder should be created (e.g., `/Shared/Projects/MyProject`).
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST use `check_workspace_path` (passing the `target_host`) to verify the folder doesn't already exist.
3. **Justification**: Why this folder is needed.

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "workspace_folder_creation",
  "parameters": {
    "target_host": "...",
    "folder_path": "...",
    "justification": "..."
  }
}
```
