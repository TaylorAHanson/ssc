# Request Data Access Instructions

**Goal**: Request access to a schema, table, view, or volume.

> **Policy — Catalog-level access is NOT allowed**.
> If the user asks for catalog-level access (or provides an `asset_name` with no `.`
> in it, which would be a bare catalog name), you MUST refuse and re-prompt.
> Keep asking until the user picks `schema`, `table`, `view`, or `volume` AND
> provides a properly qualified `asset_name`. Do NOT proceed to confirmation or
> call `execute_workflow` until the request is scoped to one of these.
>
> Example refusal: "Catalog-level access isn't permitted. Please pick a specific
> schema (e.g. `my_catalog.my_schema`), table, view, or volume underneath it.
> Which schema or object would you like access to?"

## Information to Gather
1.  **Asset Type**: What kind of asset do you need access to?
    *   *Allowed*: `schema`, `table`, `view`, `volume`.
    *   *Not allowed*: `catalog` — re-prompt the user; do not accept this value.
2.  **Asset Name**: The full name of the asset.
    *   *Format*:
        *   `schema` → `catalog.schema` (must contain exactly one `.`)
        *   `table` / `view` / `volume` → `catalog.schema.object` (must contain exactly two `.`)
    *   *Validation*: A bare catalog name (no `.`) is invalid — re-prompt for a
        schema or object underneath the catalog.
3.  **Access Level**: What level of access do you need?
    *   *Options*: `read`, `write`, `manage`.
    *   *Note*: `write` on a view falls back to `read` (SELECT) since views are not directly writable.
4.  **Justification**: Why do you need this access?
    *   *Validation*: Must be at least 10 characters.
5.  **Manager Email**: Email of your manager who will approve this request.
    *   *Validation*: Must be a valid email address.

## Validation Loop
Before calling `execute_workflow`:
*   If `asset_type` is `catalog` OR `asset_name` has no `.` → refuse, explain
    that catalog-level access is not allowed, and ask the user to scope their
    request to a schema/table/view/volume. Repeat until valid.
*   Only once all five fields are valid AND scoped below catalog level, present
    the confirmation summary and proceed.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "data_access_request",
  "parameters": {
    "asset_type": "...",
    "asset_name": "...",
    "access_level": "...",
    "justification": "...",
    "manager_email": "..."
  }
}
```
