# Metadata Change Request Instructions

**Goal**: Change Unity Catalog tags or descriptions on catalogs, schemas, tables, views or columns by updating the Metadata Manager's desired state, with approval from the object's owner groups when the caller can't change it directly.

## Look it up first
Call `search_metadata` or `get_metadata` for the objects the user names before
asking anything else. If the object already has the tag or description, say so and
stop: there's nothing to change. These read the Metadata Manager's own tables, not
Unity Catalog live. `get_metadata` shows the per-key drift if the user asks whether
Unity Catalog matches.

## Rules for what can be changed
- **Tags and descriptions only.** Grants, ownership and object creation are other
  workflows.
- **No live ALTER statements.** This changes the desired state; the reconciler
  applies it to Unity Catalog and reverts edits made outside it.
- **The object doesn't have to exist.** Tags declared for a table that hasn't been
  created yet are applied the moment it appears.
- `changes` holds one entry per object: `op` (`set_tags` merges, `unset_tags` removes keys,
  `replace_tags` makes the given tags the full set, `set_comment` / `clear_comment`),
  `name` (`catalog`, `catalog.schema` or `catalog.schema.table`), an optional
  `column`, and `tags` as `{key: value}`; a `null` value is a key-only tag.
- **Many objects at once:** when the user describes a set ("every table in
  sales_dev.bronze", "all email columns in sales_*"), use one entry with `match`
  (a pattern: `*` and `?` within one part, e.g. `sales_dev.bronze.*`) instead of
  `name`, plus `column` for a column name or pattern. Tell the user how many
  objects the preview says each pattern matched before asking for a yes.
- **Every environment:** when the user means a catalog in all its environments
  ("the sales bronze tables everywhere"), put the catalog part in braces:
  `{sales}.bronze.*` matches `sales_dev`, `sales_prd` and the rest.
- To copy to another environment (for example after testing in dev), pass
  `environments: ["prd"]` and the same changes apply to the matching objects there.
  Objects without a counterpart there are changed only where they are; mention the
  preview's `no_counterpart` warning if there is one.

## Preview, confirm, then submit
1. Call `preview_metadata_change` with the `changes` (and the reason once the user has
   given it). Follow its `next_step`.
2. Present its `report_markdown`: what changes on each object, anything already in
   place or blocked by a policy, which items the user can change directly and which
   need approval, and who approves (the owner groups on the object or its schema or
   catalog, plus governance for sensitive tags such as classification or PII).
3. **Reason** (`reason`): it goes on the change log and every approval, so it MUST be
   the user's own words. Never write it for them.
4. Get an explicit yes, then call `submit_metadata_change` with the same changes, the
   reason and `environments`. It writes what the user may change directly and files
   one `metadata_change` request for the rest. Don't call `execute_workflow`.

## After submitting
Report the `status`: `committed` (written; the reconciler applies it within
minutes), `pending_approval` (share the request link and say who approves),
`partial` (some written, the rest filed) or `noop`. If it reports a conflict,
someone changed the object since you read it: read it again, show the current
values and ask before re-applying.

Each request is **prepared** (the exact tag and description changes and who
approves), then **approved** by the owner groups (and governance when sensitive),
then **recorded** in the desired state; the reconciler then applies it to Unity
Catalog. Use those words for the steps.

## Execution
Call `submit_metadata_change` with:
```json
{
  "changes": [{"op": "set_tags", "name": "catalog.schema.table", "column": "email", "tags": {"pii": null}}],
  "reason": "...",
  "environments": []
}
```
