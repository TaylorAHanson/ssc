# Create Catalog or Schema Instructions

**Goal**: Provision a new Catalog or Schema in Unity Catalog.

## Information to Gather
1.  **Type**: Are you creating a `Catalog` or a `Schema`?
2.  **Parent**:
    *   If **Schema**: Which Catalog will it belong to?
    *   If **Catalog**: N/A.
3.  **Name**: What is the name of the new asset?
    *   *Validation*: Alphanumeric and underscores only.
4.  **Owner**: Who or what team should own this asset?
    *   *Default*: Suggest the current user's team if known.
5.  **Discovery**: If a user mentions a parent catalog that does not exist, use the `get_catalog_list` tool to find similar or existing catalogs and suggest them to the user. Do not proceed with schema creation if the parent catalog is invalid.
6.  **Comment**: A brief description of the asset's purpose.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "catalog_schema_table",
  "parameters": {
    "type": "...",
    "parent": "...",
    "name": "...",
    "owner": "...",
    "comment": "..."
  }
}
```
