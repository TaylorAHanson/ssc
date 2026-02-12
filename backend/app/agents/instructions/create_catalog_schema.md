# Catalog and Schema Management Instructions

**Goal**: Create, manage, and control access to Catalogs and Schemas in Unity Catalog.

---

## Create Catalog or Schema

### Information to Gather
1.  **Type**: Are you creating a `Catalog` or a `Schema`?
2.  **Parent**:
    *   If **Schema**: Which Catalog will it belong to?
    *   If **Catalog**: N/A.
3.  **Name**: What is the name of the new asset?
    *   *Validation*: Alphanumeric and underscores only.
4.  **Owner**: Who or what team should own this asset?
5.  **Discovery**: If a user mentions a parent catalog that does not exist, use the `get_catalog_list` tool to find similar or existing catalogs.
6.  **Comment**: A brief description of the asset's purpose.

### Execution
```json
{
  "workflow_type": "catalog_schema_table",
  "parameters": {
    "action": "create",
    "type": "Schema",
    "parent": "my_catalog",
    "name": "my_schema",
    "owner": "data-team",
    "comment": "Schema for analytics data"
  }
}
```

---

## Grant Access to Schema/Catalog

### Information to Gather
1.  **Resource Name**: Which schema or catalog?
2.  **Catalog**: Which catalog contains it (for schemas)?
3.  **Principal**: Who should get access? (user email, group name, or service principal)
4.  **Privileges**: What permissions? Common options:
    *   Schema: `USE_SCHEMA`, `CREATE_TABLE`, `SELECT`, `MODIFY`
    *   Catalog: `USE_CATALOG`, `CREATE_SCHEMA`

### Pre-Check (REQUIRED)
**Before granting access, you MUST call `check_resource_access` to verify current grants.**

If the principal already has the requested privileges, inform the user:
> "I checked and {principal} already has {privileges} access to {resource_name}. No action is needed."

Do NOT create a request if access already exists.

### Execution
```json
{
  "workflow_type": "catalog_schema_table",
  "parameters": {
    "action": "grant",
    "type": "Schema",
    "name": "analytics",
    "catalog": "atlas_dev_catalog",
    "principal": "data-engineers@company.com",
    "privileges": ["USE_SCHEMA", "SELECT", "CREATE_TABLE"]
  }
}
```

---

## Revoke Access from Schema/Catalog

### Information to Gather
1.  **Resource Name**: Which schema or catalog?
2.  **Catalog**: Which catalog contains it (for schemas)?
3.  **Principal**: Who should lose access?
4.  **Privileges**: Which specific permissions to revoke? (or omit to revoke all)

### Pre-Check (REQUIRED)
**Before revoking access, you MUST call `check_resource_access` to verify current grants.**

If the principal doesn't have the specified privileges, inform the user:
> "I checked and {principal} doesn't have {privileges} access to {resource_name}. No action is needed."

Do NOT create a request if there's nothing to revoke.

### Execution
```json
{
  "workflow_type": "catalog_schema_table",
  "parameters": {
    "action": "revoke",
    "type": "Schema",
    "name": "analytics",
    "catalog": "atlas_dev_catalog",
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
