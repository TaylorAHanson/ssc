# Volume Creation

**Goal**: Help the user create a new Unity Catalog volume.

## Context
This workflow allows users to request the creation of a new volume (managed or external) within a specific catalog and schema.

## Information to Gather
1. **Target Workspace**: Which workspace should this volume be created in?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2. **Volume Name**: The name for the new volume.
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST use `get_volume_list` (passing the `target_host`) to verify the volume doesn't already exist in the target catalog and schema.
3. **Catalog**: The parent catalog name.
3. **Schema**: The parent schema name.
4. **Volume Type**: Either `MANAGED` or `EXTERNAL`.
5. **External Location**: (Required ONLY if type is EXTERNAL) The path to the external storage.
6. **Data Classification**: The sensitivity level of the data that will be stored here.
    *   *Options*: `green`, `yellow`, `red`, `black`.
7. **Owner**: Which LMWS group/list should own this volume?
    *   *Enterprise Policy*: Individual users CANNOT own shared data assets. It must be a group (e.g., `data-eng-team`).
8. **Comment**: (Optional) A description of the volume.

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "volume_creation",
  "parameters": {
    "target_host": "...",
    "name": "...",
    "catalog": "...",
    "schema": "...",
    "volume_type": "...",
    "external_location": "...",
    "data_classification": "...",
    "owner": "...",
    "comment": "..."
  }
}
```
