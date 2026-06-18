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

## Use What You Already Know (do this FIRST)
Act like a concierge, not an intake form. Before asking the user for anything,
reuse the conversation context and your own tools:
- **Reuse assets already surfaced.** If you (or `search_data_assets` / a listing
  tool) already showed an asset earlier, DERIVE the names from it — do not make
  the user retype them. A table `cat.schema.table` you displayed tells you its
  catalog (`cat`) and its schema (`cat.schema`). So if the user then asks for
  "the schema above" or "access to `gold_framework`", resolve it to the qualified
  name yourself (e.g. `enterprise_stg.gold_framework`) and use it.
- **Resolve the workspace yourself.** Call `get_target_workspaces`. If exactly one
  workspace is configured, use its `host` silently — do NOT ask the user which
  workspace. Only ask when there are multiple and you genuinely can't tell which
  holds the asset.
- **Disambiguate type with tools, not interrogation.** If it's unclear whether a
  name is a schema or a table/view, look it up (`search_data_assets`,
  `get_schema_list`, `get_table_list`) and tell the user what you found, offering
  the choice: "`enterprise_stg.gold_framework` is a schema — want read on the
  whole schema, or just a table inside it?"
- **Never re-ask what the user already answered or what you displayed.** If the
  user pushes back ("that *is* a schema", "you listed it above"), accept it,
  restate the resolved full name you'll use, and move forward — do not loop.

## Information to Gather
Gather only what you couldn't already infer from the steps above.
1.  **Target Workspace**: Which workspace contains the asset(s)?
    *   *Action*: Use `get_target_workspaces` to find the `host`. If there is only one workspace, use it automatically and do not ask. Infer from a previously-surfaced asset when possible.
2.  **Asset(s)**: What kind of asset(s) do you need access to? You can request access to multiple assets at once.
    *   *Allowed*: `schema`, `table`, `view`, `volume`.
    *   *Not allowed*: `catalog` — re-prompt the user; do not accept this value.
3.  **Asset Name(s)**: The full name of the asset(s).
    *   *Reuse first*: If the asset (or its parent catalog/schema) was already
        surfaced in this conversation, derive the qualified name from it instead
        of asking. Only ask the user if you truly cannot resolve it.
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
5.  **Justification**: Why do you need this access? This becomes part of the
    approval's **audit record**, so it MUST reflect the user's OWN stated reason.
    *   *Do NOT fabricate it.* Never invent a business need or add specifics the
        user didn't give (project names, use cases, "for analysis/validation/
        reporting", "for EDH workflows", etc.). A made-up justification is a
        compliance problem, even if the user asks you to write one for them.
    *   *If the user asks you to "come up with" or "help with" the justification:*
        don't manufacture one. Ask 1-2 quick questions (e.g. "What will you use
        this data for, and for which project or report?") and assemble the
        justification from THEIR answers. You may tighten the wording, but the
        substance must be theirs.
    *   *Validation*: Must be at least 10 characters and reference a specific
        business need that the user actually provided.
6.  **Manager Email**: The email address of the requester's manager. Data-access
    requests require **manager approval**, so this is **required** — the manager
    is the person the approval is routed to.
    *   *Reuse first*: if the manager is already known from the conversation,
        use it; only ask if you genuinely can't resolve it.
    *   *Validation*: Must be a valid email address (contains `@`). Do not invent
        one — ask the user if unknown.

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
    "justification": "...",
    "manager_email": "..."
  }
}
```
*(Note: For backwards compatibility, a single `asset_type` and `asset_name` at the top level is also supported, but `assets` array is preferred when requesting multiple items).*
