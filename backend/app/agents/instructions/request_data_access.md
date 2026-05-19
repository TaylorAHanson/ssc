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
1.  **Target Workspace**: Which workspace contains the asset(s)?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2.  **Asset(s)**: What kind of asset(s) do you need access to? You can request access to multiple assets at once.
    *   *Allowed*: `schema`, `table`, `view`, `volume`.
    *   *Not allowed*: `catalog` — re-prompt the user; do not accept this value.
3.  **Asset Name(s)**: The full name of the asset(s).
    *   *Format*:
        *   `schema` → `catalog.schema` (must contain exactly one `.`)
        *   `table` / `view` / `volume` → `catalog.schema.object` (must contain exactly two `.`)
    *   *Validation*: A bare catalog name (no `.`) is invalid — re-prompt for a
        schema or object underneath the catalog.
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST verify the asset(s) actually exist using `get_schema_list`, `get_table_list`, or `get_volume_list` (passing the `target_host`) depending on the asset type.
3.  **Access Level**: What level of access do you need?
    *   *Options*: `read`, `write`, `manage`.
    *   *Note*: `write` on a view falls back to `read` (SELECT) since views are not directly writable.
4.  **Duration**: Is this access permanent or temporary?
    *   If temporary, ask for an expiration date (e.g., "30 days", "until Dec 31st").
5.  **Justification**: Why do you need this access?
    *   *Validation*: Must be at least 10 characters and reference a specific business need.

## Validation Loop
Before calling `execute_workflow`:
*   If any `asset_type` is `catalog` OR any `asset_name` has no `.` → refuse, explain
    that catalog-level access is not allowed, and ask the user to scope their
    request to a schema/table/view/volume. Repeat until valid.
*   Only once all fields are valid AND scoped below catalog level, present
    the confirmation summary and proceed.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "data_access_request",
  "parameters": {
    "target_host": "...",
    "assets": [
      {
        "asset_type": "...",
        "asset_name": "..."
      }
    ],
    "access_level": "...",
    "duration": "...",
    "justification": "..."
  }
}
```
*(Note: For backwards compatibility, a single `asset_type` and `asset_name` at the top level is also supported, but `assets` array is preferred when requesting multiple items).*
