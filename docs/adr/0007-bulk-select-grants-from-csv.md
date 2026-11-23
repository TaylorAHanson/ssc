# ADR-0007: Bulk SELECT grants from a CSV

- **Status:** Accepted
- **Date:** 2026-11-23
- **Follows:** [ADR-0005](0005-rbac-access-rules.md): D1 (packed workbook rows and
  the edit convention), D3 (import), D7 (one policy module) and D8 (direct admin
  edits with safeguards)

## Context

Admins receive read-access asks in batches, often around 100 at a time, as a
two-column list: a Unity Catalog securable and the group that should read it.
Today there's no safe way to load them:

- **Import tab:** this seeds the rules from the workbook once (D3). Its only other
  mode is *reset and re-import*, which replaces every rule. It can't add to the
  current rules.
- **"+ Add row" in the grid:** this stages raw rows one at a time on one sheet.
  Pasting raw rows would bypass D1's edit convention. It would add parallel rows
  instead of appending the group to an existing packed `PrincipalName` list, skip
  `USE_CATALOG` / `USE_SCHEMA`, and duplicate access a group already holds.
- **The chat agent:** this applies the convention correctly, but the model has to
  transcribe every line into a JSON item. Over 100 lines that risks a mistyped
  name or a dropped line nobody notices. It also files approval requests, which
  is heavier than an admin needs.

The packed format must stay, because the Terraform pipeline reads the export as
the workbook's own layout (D1, D2). One row per grant is not an option.

## Decision

### D1. Input: a two-column CSV, parsed by code

| Column | Holds |
|---|---|
| A | `catalog.schema`, `catalog.schema.*` or `catalog.schema.object` |
| B | One group name |

- **CSV only**, uploaded as a file. No xlsx, no pasted text.
- **Header row:** row 1 is skipped only if column A doesn't parse as a securable,
  so files with and without a header both work.
- **Each line means a grant of `SELECT` to a group** in Unity Catalog (the `uc`
  plane):
  - `catalog.schema` and `catalog.schema.*` both mean the schema (securable type
    `schema`).
  - `catalog.schema.object` means a view (securable type `view`).
  - The principal type is always `group`.
- **Environment:** worked out from the catalog name, and never guessed. The
  environment marker isn't always at the end: in `enterprise_external_prd_sfedhfed`
  it's in the middle, and in `enterprise_external_prd_dpfprprd` the last part ends
  in `prd` without being a marker. The rules, in order:
  1. **Listed catalog.** If the catalog is listed under exactly one environment's
     **Catalogs** setting (Admin → Settings → Access Rules), use that environment.
     That setting already restricts which catalogs an environment's rows may
     name, so listing a catalog there is also how an admin resolves an odd name.
  2. **Name segment.** Otherwise, split the catalog name on `_` and match each
     whole segment against the environment keys, aliases and sheet suffixes,
     ignoring case (`layout.resolve_environment`). Exactly one environment must
     match. A segment only counts if it matches in full, so `dpfprprd` doesn't
     match `prd`, but `enterprise_gold_prd`, `iplegal_ingest_prd` and
     `enterprise_external_prd_sfedhfed` all resolve to `prd`.
  3. **Otherwise rejected:** no segment matches, or segments match more than one
     environment. For example, `qa_tools_prd` matches the `qa` alias of `tst` and
     also `prd`. The message points to the Catalogs setting.

  **Cross-check:** if the catalog already has rows on another environment's UC
  sheet, the line is rejected as a mismatch rather than written to a second sheet.
- **Rejected lines**, each listed with its line number:
  - a bare `catalog` or a `catalog.*`;
  - more than three parts, or an empty part;
  - an environment that can't be worked out, or one that conflicts with the
    catalog's existing rows;
  - a blank group, or a group containing `;` (a packed cell pasted by mistake).
- **Normalisation:** cells are trimmed and compared ignoring case. Identical lines
  are collapsed into one and reported.
- **Not checked:** whether the group exists in Databricks. The naming rules in the
  policy module still apply.

### D2. Planning uses the existing engine, unchanged

Each accepted line becomes an `AccessItem` (`grant`, `environment`, `securable_type`,
`catalog`, `schema_name`, `object_name`, `principal_type: group`,
`privileges: [SELECT]`). Items are planned with `cells.add_prerequisites` and
`cells.plan_edits`, the same planner the chat agent uses through
`ledger.prepare_access_change`. So the ADR-0005 D1 convention holds:

- **Schema grants** carry their prerequisite in the same set, as the sheet does:
  `USE_SCHEMA;SELECT`.
- **View grants** are `SELECT` on the view row.
- **Missing prerequisites** are added: `USE_CATALOG` on the catalog, and for views
  `USE_SCHEMA` on the schema. They're skipped when the group already holds them
  or another line in the file grants them.
- **Merging:** a grant is added to the end of the `PrincipalName` list on an active
  row with the same securable, owner and principal type and exactly the same
  privilege set. Only if there's no such row is a new row added.
- **Already held:** a grant the group already has on that securable is skipped and
  reported.
- **Owner:** inferred as the planner does today (`RBAC_DEFAULT_OWNER`, else the
  most common owner for that catalog on the sheet, else on the sheet).

### D3. A view already covered by a schema grant is flagged and not written

This check is new. The planner treats a grant as already held only when the group
has it on that exact securable. A view line is therefore treated as **covered**,
and not written, when one of these already holds:

- the group has `SELECT` on the view's schema or catalog in the current rules; or
- the group gets `SELECT` on the view's schema elsewhere in the same file.

A covered line is an info-level flag in the preview ("covered by `SELECT` on
`catalog.schema`"). It never blocks the save. The rule mirrors how `find_access`
already reports `via: "parent"`.

### D4. Preview, then save as one direct change set

- **Where:** a "Bulk SELECT grants (CSV)" card on the **Import** tab. It's separate
  from the workbook import, because one file can span several sheets (for example
  `Prd` and `Stg`), while the Rules tab's staged-changes tray covers one sheet.
- **Who:** rules admins, the same people who can use the Import tab (`can_import`).
- **Preview** (`POST /rbac/grant-imports/preview`) stores nothing. It returns:
  - counts per sheet;
  - every line with its outcome: added to an existing row, new row, already held,
    covered by a schema or catalog grant, or rejected with the reason;
  - the prerequisites that were added;
  - the row diffs;
  - policy findings, from the same dry run as a grid commit
    (`ledger.preview_change_set_without_reason`);
  - whether the change fits `RBAC_MAX_CHANGES_PER_COMMIT`;
  - a fingerprint of the planned diffs.
- **Save** (`POST /rbac/grant-imports`) needs the same file, the fingerprint and a
  reason. The server parses and plans again. If the fingerprint no longer matches
  (someone changed the rules in between), it refuses and asks for a new preview.
  Otherwise the diffs become a `ChangeSetIn` (creates, plus updates carrying the
  row versions) and go through `ledger.commit_change_set`. That is the ADR-0005 D8
  path, so these rules apply without new code:
  - the policy module;
  - the reason requirement;
  - all-or-nothing writes with version checks;
  - the per-commit cap;
  - the two-person rule, which files a request instead when an affected
    environment has it on.
- **Blocking:** rejected lines, deny findings and going over the cap block the
  save. Already-held lines, covered lines and collapsed duplicates don't.
- **Result:** one change set in History, revertible like any other, and carried
  by the next export.

### D5. Out of scope

- Revokes.
- Privileges other than `SELECT`.
- The metastore and workspace planes.
- xlsx or pasted input.
- Changes to the chat agent's flow.
- Checking that groups exist.

## Consequences

- **Positive:**
  - One-hundred-line batches load in one reviewed, revertible change set.
  - The export still reads as if a person had edited the sheet: groups are added
    to existing lists, and prerequisites travel the way the sheet records them.
  - No new policy or commit path. Bulk saves go through the same safeguards as
    grid edits.
  - The model never transcribes the file.
- **Negative:**
  - A second upload card on the Import tab.
  - The covered-by-parent check (D3) is new logic that the chat agent's planner
    doesn't have, so the agent can still write a redundant view row. It could be
    moved into `cells.plan_edits` later.
- **Follow-ups:** consider the same covered check for the chat agent; revokes if
  asked for.

## Implementation notes

- **Backend:** `services/rbac/grant_import.py` holds the parser, item building, the
  covered check and the fingerprint. It plans through the ledger's existing
  per-plane planner. Two endpoints go in `api/v1/rbac.py`.
- **Frontend:** a new card in `src/components/rbac/ImportTab.tsx`. It reuses
  `FindingList` and the existing preview-table patterns.
- **Tests:** parser cases (header detection, `*`, two- and three-part names,
  rejected forms, `;` in a group, duplicates); environment from the Catalogs
  setting, from a middle segment (`enterprise_external_prd_sfedhfed`), not from a
  partial segment (`dpfprprd`), rejected when ambiguous, and the cross-check
  against existing rows;
  appending to a packed row versus adding a new row; prerequisites added or
  skipped; covered by an existing schema grant, a catalog grant, or the same
  file; a fingerprint mismatch after a concurrent edit; the cap; the two-person
  rule filing a request.
- **User-facing:** a minor-version release note, and the Import section of
  `docs/guide/admin/access-rules.md`.

## Settled during the build

- **The cap counts row changes, not lines.** `RBAC_MAX_CHANGES_PER_COMMIT` ("Max
  row changes per save") is checked against the planned row edits. Grants that
  are added to existing rows count once per row, however many groups they add.
- **An already-held line can still add a prerequisite.** If a group holds the
  grant but not `USE_CATALOG` / `USE_SCHEMA` above it, the planner adds the
  missing prerequisite, as the request flow does. The line still reads
  "Already held", and the prerequisite is listed in the preview.

## Open points

- **Rejected lines block the save** (D4). The alternative is to save the accepted
  lines and list the rejected ones. Blocking keeps the change set exactly what the
  file says.
- **A `;` in column B is rejected** (D1) rather than split into several groups.
- **Environment aliases:** the defaults include `test`, `qa`, `stage`, `prod` and
  similar. A catalog with one of those words as a segment next to a real marker is
  rejected as ambiguous until it's listed under an environment's Catalogs setting.
  Check the configured aliases against the real catalog names before go-live.
