# Request Data Access Instructions

**Goal**: Request access to a catalog, schema, or table.

## Information to Gather
1.  **Asset Type**: What kind of asset do you need access to?
    *   *Options*: `catalog`, `schema`, `table`.
2.  **Asset Name**: The full name of the asset (e.g., `catalog.schema.table`).
    *   *Validation*: Should look like a valid SQL identifier.
3.  **Access Level**: What level of access do you need?
    *   *Options*: `read`, `write`, `manage`.
4.  **Justification**: Why do you need this access?
    *   *Validation*: Must be at least 10 characters.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "data_access_request",
  "parameters": {
    "asset_type": "...",
    "asset_name": "...",
    "access_level": "...",
    "justification": "..."
  }
}
```
