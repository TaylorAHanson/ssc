# Tag Management Expansion — Status & Plan

> **Status (2026-10-01):** Phases 0–2 and their follow-ups are done (admin
> tagging of any table, view or column), except the GitOps-mode items, which
> are deferred. Phase 3 (self-service requests through the agent) is not
> started and is waiting on customer UX input — see [Open decisions](#open-decisions).
>
> **Update (2026-10-06):** the page is now the **Metadata Manager** (route
> `/governance/metadata`; internal ids such as the `tag_management` tab key and
> the `/tags` API are unchanged). Catalogs and schemas are targets too: a
> 1- or 2-part `table` in a change item, with an optional `desired_comment`
> (catalogs and schemas only) that plans a `COMMENT ON` before the tag
> statements. Both are Local-mode only; GitOps refuses them. A key rename uses
> `GET /tags/key-usage` to find every object and column carrying the key, then
> stages the rename client-side as ordinary edits.

**Goal:** extend tag management beyond governed datasets so that (1) Governance
Admins can tag any individual table or view, (2) columns can be tagged, and
(3) end users can request tag changes through the agent, which applies them on
their behalf under the normal approval and guardrail stack.

---

## Done

### Phase 0 — Shared tag-change engine

- **Engine:** `backend/app/services/tag_change/`
  - `engine.py`: `evaluate()` reads live state, plans the change and runs every
    check; `submit()` records the `TAG_CHANGE` request and applies it directly
    or queues the GitOps PR. `api/v1/tags.py` is now a thin HTTP layer over it.
    **Phase 3 tools should call `evaluate` / `submit` rather than re-implementing
    anything.**
  - `datasets.py`: dataset listing and member discovery. Discovery is one
    `system.information_schema` query instead of one per catalog (about 20s →
    about 5s), falling back to per-catalog queries if that view can't be read.
  - `search.py`: the quick search behind the picker.
- **Targets:** a change item is `{table, column?, desired_tags}`, and
  `dataset_id` is optional. Plans key targets as `fqn` or `fqn::column`
  (`tag_plan.target_key`). Up to 500 targets per change (`engine.MAX_TARGETS`).
  This differs from the plan: there's no separate `targets[]` field; `column`
  was added to the existing `tables[]` items.
- **Safe SQL:** names with quotes, semicolons, whitespace or control characters
  are refused (`tag_plan.validate_target`), and local-mode statements
  backtick-quote every name part.
- **GitOps:** statement forms are unchanged, because `tag_sql.py` is a contract
  with the governance repo, so SQL generation wasn't unified across modes as
  planned. The preview now shows the SQL that will actually be committed.

### Phase 1 — Tag any table or view

The planned "By dataset / By object" mode switch and catalog-tree picker were
replaced by a simpler blended design:

- **One search box.** Clicking it lists every governed dataset (scrollable, and
  filtered in the browser, so 200+ is fine). Typing also finds tables and views
  in the governed catalogs. Each result has a checkbox meaning "in my list" that
  adds or removes it immediately. A ticked dataset adds all its tables;
  unticking it removes only the tables it alone brought in.
- **Search syntax:** words (all must match), globs (`main.sales.*`), and tag
  filters (`classification=restricted`, `data_owner=*`, `!data_owner`). Names
  come from the Discover data-asset cache (instant); tag filters read live
  values from Unity Catalog (about 1–2s).
- **Scope:** `SCAN_CATALOGS`, the same governed-catalog list everything else
  uses. No new `GOVERNANCE_TAGS_CATALOGS` setting was added.
- **Editing:** each object is one row with tag chips (green new, amber changed,
  red removed; edits listed first). Click a row to edit. Bulk edits are
  **"Set tag … on all N shown"**: narrow the list with the filter (name,
  `key=value`, `!key`) first. There are no per-row selection checkboxes, which
  user testing found confusing.
- **Layout:** two tabs styled like the Admin page: **Edit Tags** (search, list,
  review) and **Change History** (`?tab=history`, with a "Submitted by"
  column). Edits survive switching tabs.
- **Reserved keys:** only `system.certification_status` (OmniGuard sets it on
  certification and removes it when certification lapses;
  `tag_plan.RESERVED_KEYS`). It's flagged in the editor and refused in Review
  with a clear reason. Before, the whole `system.*` prefix was hidden and
  silently dropped, which made the change look like an empty plan. Other
  `system.*` tags are now shown and editable. The built-in default policy uses
  `reserved_keys`; a governance-repo policy can still declare
  `reserved_prefixes`.
- **Endpoints:** `GET /tags/search`, `POST /tags/objects`, `GET /tags/columns`,
  `GET /tags/governed`, all admin-only like the rest of `/tags`.

### Phase 2 — Column tags

- **Read:** `information_schema.columns` and `column_tags`, fetched only when a
  table's Columns panel is opened.
- **Write:** table columns use `ALTER TABLE … ALTER COLUMN … SET/UNSET TAGS`.
  View columns use `SET TAG ON COLUMN` / `UNSET TAG ON COLUMN`, one tag per
  statement, unsetting first because `SET TAG` won't overwrite an existing key.
- **Policy:** keys may declare `applies_to: [table, column]` in
  `tag_policy.yml`. The default policy marks `dataset`, `data_owner`,
  `approver_group`, `access_group` and `reliability_window` as table-only.
  Required table keys aren't protected on columns.
- **Risk:** new `sensitivity_downgrade` factor (classification lowered or
  removed, PII flag cleared). The certified-object factor now actually fires;
  before, it read `system.*` tags from a map that excludes them.
- **UI:** a Columns panel per table. Tables with 6 or more columns get a column
  filter and their own "Set tag … on all N shown columns" bar.
- **GitOps mode:** column edits are read-only in the UI and blocked by the
  engine, and `build_tag_sql` refuses column changes outright.

### Unity Catalog governed tags (added after user testing)

UC tag policies can restrict a key to an allowed list of values, which UC only
enforces when the statement runs (`UC_TAG_POLICY_VALUE_NOT_ALLOWED`). This could
leave a change partly applied in Local Execution Mode. Now:

- `workflows/tag_governed.py` looks up each key being set
  (`tag_policies.get_tag_policy`, about 0.1s per key; `NotFound` means the key
  isn't governed).
- A disallowed value is a **policy violation** in Review, so Apply stays
  disabled.
- The editor's value fields suggest the allowed values and flag a disallowed
  one as you type (`TagValueInput`, which calls `GET /tags/governed`).
- If a policy can't be read, Review shows a **warning** instead of a block; UC
  still enforces it on apply.

### Phase 2 follow-ups (done 2026-10-01)

- **Materialized views and streaming tables** now get their own statements:
  `ALTER MATERIALIZED VIEW` / `ALTER STREAMING TABLE … [ALTER COLUMN …] SET/UNSET
  TAGS`. Metric views are treated as views. Before, they got `ALTER TABLE`. The
  grammar was verified against the live warehouse by running each form against
  a name that doesn't exist (it parses, then fails with "not found"; nothing is
  written). `ALTER VIEW … ALTER COLUMN` is a syntax error, which confirms that
  view columns need `SET TAG ON COLUMN`. Type mapping is in
  `tag_plan.relation_type`, and the TABLE/VIEW retry in `tag_apply` no longer
  fires for these types.
- **ABAC impact** (`workflows/tag_abac.py`): for each changed table, the column
  masks and row filters that apply (`policies.list_policies(...,
  include_inherited=True)`, in parallel, up to 200 tables) are parsed for
  `has_tag` / `has_tag_value` conditions:
  - Column matches are checked against column changes; the policy's `when`
    condition is checked against table changes.
  - A hit adds the `abac_impact` risk factor (25 points each, capped at 75) and
    a Review warning naming the policy.
  - Tables whose policies can't be read (the governance SP needs
    `READ METADATA`) are listed as unchecked.
  - This metastore has no ABAC policies, so this is covered by unit tests only.
- **ASSIGN on governed tags:** no API exposes it (see "still open"). When a
  change sets governed keys, Review says they need `ASSIGN` for the governance
  service principal.
- **Governed key suggestions:** `GET /tags/governed/keys?q=` searches every tag
  policy (about 3,000 in this account; listed in about 1.4s, cached for 5
  minutes, prefix matches first). `TagKeyInput` merges them with the built-in
  keys as you type and labels them "governed · N allowed values".
- **Dataset-membership callout:** a row whose edit adds, moves or removes its
  `dataset` / `data_set` tag shows a "→ dataset" badge and an explanation
  under its editor.

### Tests and docs

- About 65 new backend tests (plan, policy, risk, engine, search, API, governed
  tags, ABAC, relation types). The full backend suite passes (1174 tests).
- Frontend typecheck and lint are clean for the changed files.
- Updated `GOVERNANCE.md` §4.3, `SERVICE_PRINCIPALS.md` (governance SP writes
  tags in Local Execution Mode), and the `RELEASE_NOTES.md` 1.2.0 entry.

---

## To do

### Phase 2 follow-ups still open

- **GitOps mode (deferred).**
  - GitOps writes `ALTER TABLE` even for views (and for materialized views and
    streaming tables); confirm the governance repo's apply step handles it, or
    change the contract on both sides.
  - Column tags in GitOps mode need a column statement form in the governance
    repo contract; then remove the block in `engine.evaluate` and
    `build_tag_sql`.
- **ASSIGN pre-check isn't possible today.** No API exposes who holds `ASSIGN`
  on a governed tag: the Permissions API rejects every object type tried, and
  UC grants don't support `TAG_POLICY`. Review shows a reminder instead; revisit
  if Databricks adds an API.
- **Bulk/CSV import:** see open decision 5.

### Phase 3 — Self-service tag requests through the agent

Not started. Build it as a **published workflow** (`tag_change_request`), not a
free-standing mutating tool, so it inherits approval gates, durable waiting and
the audit trail. Every step goes through `services/tag_change/engine.py`, which
already does validation, governed-tag checks, risk and audit.

**Tools**

| Tool | Side-effect class | Identity | Purpose |
|---|---|---|---|
| `get_object_tags` | `read` | OBO (on behalf of the user) | Current tags on a table/view and its columns, bounded by the user's own UC visibility. |
| `preview_tag_change` | `read` | OBO for state; engine for checks | Wraps `engine.evaluate`. Returns `report_markdown` (built with `safe_text`/`safe_code`) so approvers see the diff and risk on every approval card. |
| `apply_tag_change` | new `metadata_write` (or reuse `data_grant`) | Service principal | Re-runs `engine.evaluate` against live state, checks it against the approved plan's hash, then calls `engine.submit`. |

A new side-effect class also needs `MUTATING_SIDE_EFFECT_CLASSES` in
`tools/mcp.py` and the OPA rego updated.

**Graph spec (sketch)**

1. Step: resolve targets, then `preview_tag_change`. This writes the risk band
   and flags into the workflow context.
2. Gate: `data_owner`, with `approvers_from` coming from `resolve_data_owners`
   on the target tables.
3. Gate: **`governance_admin`**, a new gate type (`GATE_TYPES` in
   `workflows/spec_loader.py` doesn't have it). It auto-approves unless any of
   these apply:
   - risk is high or critical
   - a protected or access-control key is touched
   - a classification is downgraded
4. Step: `apply_tag_change`.
5. GitOps mode only: the existing `pr_merge` gate.

**Guardrails**

- **Access-control keys can escalate privileges.** `approver_group` and
  `access_group` drive access-request approvals. Changes to them always need
  both the data owner and a Governance Admin. The requester must never approve
  their own request, including through group membership.
- **Key allowlist.** `GOVERNANCE_TAGS_SELF_SERVICE_KEYS` (a setting) lists the
  keys requesters may set. `system.*` stays reserved.
- **Size limits.** A per-request object cap (a setting, below `MAX_TARGETS`).
  Globs always need a preview first.
- **Stale approvals.** Re-check the plan at apply time and fail with a reason if
  live state has changed the diff.
- **Audit:** keep using `TAG_CHANGE` requests, so agent changes appear in Tag
  Manager history and the requester's My Requests.

**Agent instructions.** Add `agents/instructions/tag_change.md`. Example flow
for "mark the email column on sales.crm.customers as PII":

1. Resolve the object with `search_data_assets` or `get_table_list`.
2. Read its tags with `get_object_tags`.
3. Propose the diff and run `preview_tag_change`.
4. Confirm with the user, then call `execute_workflow`.

**Other:**

- Feature flag `features.tag_self_service` in `CAPABILITIES`.
- Optional "Request tag change" action on Discover asset pages.

**Phase 3 tests:**

- Unit tests for the stale-plan check and blocking self-approval.
- Workflow eval golden transcripts for a simple column PII request, an
  `approver_group` escalation attempt, and a bulk glob request.

---

## Open decisions

Waiting on customer input, especially on UX:

1. **Execution identity for self-service.** Choose one:
   - The service principal always applies after approvals. This matches Tag
     Manager and `grant_uc_access`.
   - The user applies on their own behalf (OBO) when they already hold
     `APPLY TAG` (and `ASSIGN` for governed keys). This skips approvals and
     makes UC grants the limit.
2. **Execution mode for self-service.** Should agent changes follow the global
   local/GitOps mode, or always go through GitOps? Note that GitOps can't do
   column tags yet (see Phase 2 follow-ups).
3. **End-user scope.** Should end users only request tags on assets they own or
   can see, or anywhere in the governed catalogs?
4. **ABAC usage.** Are ABAC masks or row filters keyed on governed tags in the
   target metastores? Impact detection is built, but it needs the governance SP
   to have `READ METADATA` on governed tables to see the policies.
5. **UX surface.** Is chat-only enough for end users, or do they also need a
   form or Discover-page entry point? Does the admin page need bulk/CSV import?

Settle decision 1 before building Phase 3; most of the governance design
depends on it.
