# Governed Tag Creation

**Goal**: Help the user create a governed tag at the account level.

## Context
This workflow allows users to request the creation of a new tag key and optional default value at the Databricks account level.

## Information to Gather
1. **Target Workspace**: Which workspace should this tag be created in?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2. **Tag Name**: The name of the tag key (e.g., `CostCenter`, `Environment`).
3. **Tag Value**: (Optional) A default value for the tag.
4. **Justification**: Why this tag is needed.

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "tag_creation",
  "parameters": {
    "target_host": "...",
    "tag_name": "...",
    "tag_value": "...",
    "justification": "..."
  }
}
```
