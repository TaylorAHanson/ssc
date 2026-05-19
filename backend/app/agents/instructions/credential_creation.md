# Credential Creation

**Goal**: Help the user create a storage credential.

## Context
This workflow allows users to request the creation of a new storage credential in Unity Catalog.

## Information to Gather
1. **Target Workspace**: Which workspace should this credential be created in?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2. **Credential Name**: The name for the new storage credential.
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST use `get_credential_list` (passing the `target_host`) to verify the credential doesn't already exist.
3. **Cloud Provider**: The cloud provider (AWS, Azure, GCP).
3. **Role/Service Principal**: The ARN, Client ID, or Service Account email to use.
4. **Justification**: Why this credential is needed.

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "credential_creation",
  "parameters": {
    "target_host": "...",
    "credential_name": "...",
    "cloud_provider": "...",
    "role_identifier": "...",
    "justification": "..."
  }
}
```
