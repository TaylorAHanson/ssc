# Catalog and Schema Management Instructions

**Goal**: Create, manage, and control access to Catalogs and Schemas in Unity Catalog.

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
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST use `get_catalog_list` or `get_schema_list` (passing the `target_host` and optional pattern filter) to verify the catalog or schema doesn't already exist.
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

## Grant Access to Schema/Catalog

### Information to Gather
1.  **Target Workspace**: Which workspace contains this schema/catalog?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2.  **Resource Name**: Which schema or catalog?
3.  **Catalog**: Which catalog contains it (for schemas)?
4.  **Principal**: Who should get access? (user email, group name, or service principal)
5.  **Privileges**: What permissions? Common options:
    *   Schema: `USE_SCHEMA`, `CREATE_TABLE`, `SELECT`, `MODIFY`
    *   Catalog: `USE_CATALOG`, `CREATE_SCHEMA`

### Pre-Check (REQUIRED)
**Before granting access, you MUST call `check_resource_access` (passing the `target_host`) to verify current grants.**

If the principal already has the requested privileges, inform the user:
> "I checked and {principal} already has {privileges} access to {resource_name}. No action is needed."

Do NOT create a request if access already exists.

### Execution
```json
{
  "workflow_type": "catalog_schema_table",
  "parameters": {
    "target_host": "...",
    "action": "grant",
    "type": "Schema",
    "name": "analytics",
    "catalog": "dev_catalog",
    "principal": "data-engineers@company.com",
    "privileges": ["USE_SCHEMA", "SELECT", "CREATE_TABLE"]
  }
}
```

---

## Revoke Access from Schema/Catalog

### Information to Gather
1.  **Target Workspace**: Which workspace contains this schema/catalog?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2.  **Resource Name**: Which schema or catalog?
3.  **Catalog**: Which catalog contains it (for schemas)?
4.  **Principal**: Who should lose access?
5.  **Privileges**: Which specific permissions to revoke? (or omit to revoke all)

### Pre-Check (REQUIRED)
**Before revoking access, you MUST call `check_resource_access` (passing the `target_host`) to verify current grants.**

If the principal doesn't have the specified privileges, inform the user:
> "I checked and {principal} doesn't have {privileges} access to {resource_name}. No action is needed."

Do NOT create a request if there's nothing to revoke.

### Execution
```json
{
  "workflow_type": "catalog_schema_table",
  "parameters": {
    "target_host": "...",
    "action": "revoke",
    "type": "Schema",
    "name": "analytics",
    "catalog": "dev_catalog",
    "principal": "former-contractor@company.com",
    "privileges": ["SELECT"]
  }
}
```

---

## Common Privilege Reference

| Resource | Privilege | Description |
|----------|-----------|-------------|
| Catalog | `USE_CATALOG` | Access catalog metadata |
| Catalog | `CREATE_SCHEMA` | Create schemas in catalog |
| Schema | `USE_SCHEMA` | Access schema metadata |
| Schema | `CREATE_TABLE` | Create tables in schema |
| Schema | `SELECT` | Read data from tables |
| Schema | `MODIFY` | Insert/update/delete data |
