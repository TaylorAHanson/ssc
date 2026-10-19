# Catalog and Schema Management Instructions

**Goal**: Create Catalogs and Schemas in Unity Catalog.

---

## Create Catalog or Schema

### Information to Gather
1.  **Target Workspace**: Which workspace should this catalog/schema be created in?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2.  **Type**: Are you creating a `Catalog` or a `Schema`?
3.  **Parent**:
    *   If **Schema**: Which Catalog will it belong to?
    *   If **Catalog**: N/A.
4.  **Name**: What is the name of the new asset?
    *   *Validation*: Alphanumeric and underscores only.
    *   *Existence Check (REQUIRED)*: As soon as you know the name and workspace, use `get_catalog_list` or `get_schema_list` (passing the `target_host` and optional pattern filter) to verify the catalog or schema doesn't already exist. If it does, say so and ask whether they need access to it or a different name, before asking for the remaining fields.
5.  **Data Classification**: The sensitivity level of the data that will be stored here.
    *   *Options*: `green`, `yellow`, `red`, `black`.
5.  **Owner**: Which LMWS group/list should own this asset?
    *   *Enterprise Policy*: Individual users CANNOT own shared data assets. It must be a group (e.g., `data-eng-team`).
6.  **Discovery**: If a user mentions a parent catalog that does not exist, use the `get_catalog_list` tool to find similar or existing catalogs.
7.  **Comment**: A brief description of the asset's purpose.

### Execution
```json
{
  "workflow_type": "catalog_schema_table",
  "parameters": {
    "target_host": "...",
    "action": "create",
    "type": "Schema",
    "parent": "my_catalog",
    "name": "my_schema",
    "data_classification": "yellow",
    "owner": "data-team",
    "comment": "Schema for analytics data"
  }
}
```

---

## Access to a Catalog or Schema

This workflow does not grant or revoke access. Access to catalogs, schemas and other
securables is given to groups through the access-rules workflow: use `find_access`
to see who already has it, then `preview_access_change`, and submit
`rbac_access_change` once the user confirms the proposed change.
