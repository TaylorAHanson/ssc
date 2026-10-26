# ADR-0006: Table-backed metadata desired state (tags and descriptions)

- **Status:** Accepted
- **Date:** 2026-10-20
- **Supersedes:** the GitOps and live-read Metadata Manager described in
  [`docs/TAG_MANAGEMENT_EXPANSION_PLAN.md`](../TAG_MANAGEMENT_EXPANSION_PLAN.md)
- **Follows:** [ADR-0005](0005-rbac-access-rules.md) (current-state tables, a change log
  written alongside them, optimistic version locks, one policy module, and preview,
  prepare and record steps)

## Context

The Metadata Manager edits Unity Catalog tags and descriptions. Before this ADR:

- **Every screen and check read UC live.** `api/v1/tags.py`, `services/tag_change/engine.py`
  and `workflows/tag_plan.py` (`fetch_live_state`) query `information_schema` on every
  search, open and preview. Search is slow and its results depend on warehouse load.
- **Two apply paths.** Local mode runs `workflows/tag_apply.py` directly against UC.
  GitOps mode generates a SQL migration (`tag_sql.py`), opens a pull request
  (`open_tag_change_pr`), waits for a `pr_merge` gate in the `tag_change` workflow and
  learns the outcome from a Delta ledger table. Catalog, schema and column changes only
  worked in local mode.
- **No desired state.** What UC holds right now *is* the state. Nothing can declare tags
  for a table before it exists, nothing notices drift, and nothing re-applies tags to a
  table a pipeline drops and recreates.
- **No machine callers.** The auth middleware and `deps.py` assume a human whose email
  the Databricks Apps proxy forwards. Pipelines and other systems can't submit changes.
- **The in-app MCP endpoint isn't ready for write tools.** It isn't an open hole: the Apps
  proxy authenticates every request, no tool is published over MCP by default, and live
  UC reads refuse to run as the app service principal on deployed targets
  (`uc_client_for`). But the shim passes tools only the caller's email, not their groups
  or app roles, so a tool can't check permissions, and its docstring promises a
  service-principal fallback that must not be relied on.
- **Permissions are coarse.** Only Platform and Governance Admins can use the page.

Product decisions confirmed with the owner:

- **One prod instance owns every environment.** Lower environments still need a way to
  test changes before they reach prod.
- **Full enforcement.** The desired row is the complete truth: a tag UC has that the row
  doesn't is removed.
- **Owners write directly.** Authorized owners commit without approval; sensitive changes
  go through an approval; unauthorized users can file a request routed to the owners.
- **Per-tag authorship.** Who set each tag, when and from which system is kept per tag,
  not per object.

## Decisions

### D1. Two Lakebase tables are the single source of truth; a change log is written alongside them

`metadata_objects` holds one row per securable:

- **Identity:** `securable_type` (`catalog`, `schema`, `table`, `view`,
  `materialized_view`, `streaming_table`, `metric_view` or `column`), `catalog`,
  `schema_name`, `object_name`, `column_name`, a normalized unique `fqn_key`
  (lowercased `catalog.schema.table`, with `::column` for a column) and `environment`
  (derived from the catalog, D5).
- **Description:** `comment` (NULL means no description), with its own authorship:
  `comment_set_by`, `comment_set_by_type` (`user`, `service_principal` or `system`),
  `comment_source` (`ui`, `api`, `mcp`, `agent`, `adopt`, `import`, `system` or
  `revert`), `comment_set_at`, `comment_change_set_id` and `comment_version`.
- **Lifecycle:** `status` (`active` or `deleted`), `object_state` (`exists`, `pending` or
  `orphaned`), `orphaned_at`, and `created_at/by/by_type`.
- **Apply tracking:** `last_applied_at`, `last_apply_status`, `last_apply_error`,
  `last_observed_at`, `drift_detected_at` and `desired_hash`.

`metadata_tags` holds one row per tag on an object: `object_id`, `key`, `value`, `set_by`,
`set_by_type`, `source`, `set_at`, `change_set_id` and `version`.

- **Key-only tags:** `value` is NULL. UC reports a key-only tag as an empty string; both
  sides normalize `""` to NULL so diffs stay stable.
- **Removing a tag** deletes its row. Under full enforcement a tag with no row is removed
  from UC (except exempt keys, D3).
- **The API and UI show tags as a dict** (`{"pii": null, "data_owner": "group_a"}`) with
  each tag's authorship alongside.
- **Why one row per tag:** the table answers who set each tag, when and from which system;
  writers editing different keys on the same object never conflict; per-key permissions
  and `key=value` search are plain indexed lookups that also work on SQLite.

The change log is a record, not the source:

- `metadata_change_sets` holds the actor, actor type, source, reason, idempotency key,
  request id and, for a revert, the change set it reverts. `metadata_events` holds one
  event per tag or description change (object, key, before, after) and one per object
  created, deleted or restored.
- It is **written in the same transaction** as the state tables and **never replayed**:
  current state is never computed from it.
- It serves the History tab, revert, approval evidence, and telling user edits apart from
  reconciler bookkeeping.
- **Rejected:** computing state from the log (event sourcing), since every read would pay
  for a replay; state tables with no log, which loses history and revert; Lakebase Change
  Data Feed as the only history, since it carries no actor or reason.

The new tables join the default `LAKEBASE_CDF_TABLES` list, so desired state and its audit
trail can flow into Delta.

### D2. The UI never queries UC live

- `metadata_observed` is a snapshot keyed by the same `fqn_key`. It holds tags (as JSON;
  a snapshot needs no per-tag authorship), comment, owner, table type, data type,
  `created` and `last_altered`, filled from `information_schema` (`catalogs`,
  `schemata`, `tables`, `columns` and the four `*_tags` views) by the governance service
  principal (`get_governance_uc_provider()`).
- Search, the object drawer and the drift view join desired and observed rows in Lakebase.
- Only the reconciler reads UC, in the background: the sweep, the new-object poll and a
  targeted refresh of the objects it is about to change.
- The Discover `data_assets` cache stays as it is; folding it into the snapshot is a
  follow-up.

### D3. The reconciler fully enforces each managed row, with safety rails

- **Diff:** for each managed row, set tags that are missing or have a different value,
  unset tags UC has that the row doesn't, and set or clear the comment.
- **Exempt keys:** `METADATA_EXEMPT_KEYS` (default `system.certification_status`) lists
  keys the reconciler never sets or removes and that edits can't touch. Without it,
  enforcement would fight OmniGuard.
- **Triggers:**
  - committing a change enqueues the affected objects; the poller drains the queue
    within one poll cycle;
  - a new-object poll reads `information_schema.tables` for rows whose `created` or
    `last_altered` is past a watermark, every `METADATA_NEW_OBJECT_POLL_SECONDS`;
  - `POST /api/v1/metadata/reconcile` lets a pipeline notify right after `CREATE`;
  - a cron full sweep (`METADATA_RECONCILE_CRON`) refreshes the snapshot and catches
    drift.
- **Rejected trigger:** audit-log polling as the main trigger, since system tables lag.
- **Rows declared before the object exists** have `object_state=pending`; the create
  event applies them.
- **Dropped objects** become `orphaned` and are re-applied if the object is recreated,
  which pipelines do often. Rows orphaned longer than `METADATA_ORPHAN_RETENTION_DAYS` are
  purged (soft-deleted, with an event).
- **A new object with no row:** `METADATA_NEW_OBJECT_POLICY` defaults to `adopt`: the
  object's create-time tags and comment become its desired state (`source=adopt`, system
  actor), so tags a pipeline sets with `CREATE ... TAGS` aren't stripped. `ignore` leaves
  it unmanaged.
- **Modes:** `METADATA_RECONCILE_MODE` is `off`, `plan` (compute and record the plan,
  write nothing) or `enforce`.
- **Circuit breaker:** a run that would make more than `METADATA_MAX_REMOVALS_PER_RUN`
  tag removals applies nothing, records the run as `paused` and alerts governance.
- **Single writer:** only the poller leader reconciles, under a Postgres advisory lock.
  Each deployment writes only to the catalogs in its `METADATA_RECONCILE_CATALOGS`, and
  the lists of different deployments must not overlap. (Amended 2026-10-20: a per-catalog
  claim tag naming the owning instance was dropped. Production is the only deployment
  that enforces the governed catalogs, and the tag added a write to every catalog.)
- **Statement builders** move out of `tag_plan.py` into `services/metadata/sql.py`. New
  forms cover table, view, materialized view, streaming table and column comments
  (`COMMENT ON TABLE`, `COMMENT ON COLUMN`), next to the existing catalog and schema
  forms.

### D4. Authorization is computed from desired state

It is fast and works for objects that don't exist yet.

- **Owner authority:** a user may edit an object if they belong to the group named by its
  owner tag (`METADATA_OWNER_TAG_KEY`, default `data_owner`). Without an owner tag on the
  object, the parent relation's (for a column), then the schema's, then the catalog's
  owner tag applies. The tag's value may list several groups, comma-separated.
- **UC owner:** with `METADATA_UC_OWNER_IS_AUTHORITY` on, the object's UC owner (from the
  snapshot) also counts, matched against the caller's email or groups.
- **Key policies:** the settings collection `METADATA_KEY_POLICIES` replaces
  `tag_policy.yml` loaded from the governance repo. Per key it sets `applies_to`, allowed
  values, a value pattern, editor groups, whether the key is sensitive and whether it is
  required. `METADATA_KEY_MODE=known_only` refuses keys with no policy row.
- **Sensitive changes need approval:**
  - access-control keys (`approver_group`, `access_group` by default, marked sensitive in
    their key policy);
  - any change to the owner key itself, so nobody can grant themselves authority;
  - a classification downgrade (`METADATA_CLASSIFICATION_KEY` against the ordered
    `METADATA_CLASSIFICATION_LEVELS`), or removing a PII flag (`METADATA_PII_KEYS`);
  - any key marked sensitive;
  - the environment rule (D5).
- **Unauthorized users** can file a request; it routes to the owner groups.
- **Admins:** Platform Admins and the roles in `METADATA_EDITOR_ROLES` (default Governance
  Admin) edit everything; their sensitive changes still need approval.
- **Browsing** is limited to governance (`authz.browse_scope`). Admins, the roles in
  `METADATA_BROWSE_ROLES` (default Governance Admin; blank adds nobody), registered
  principals and the system see every object. Any other user sees only the objects
  they own under the owner rules above (owner tag on the object or above it, plus UC
  owner when that counts), and nothing when they own nothing. Every read applies it in
  SQL (search and its counts, keys, datasets, drift, change history); sync runs are
  governance-only. Anyone may still submit a change, but the preview, result,
  conflicts and change set they get back hide current values of objects outside
  their scope; the filed request keeps the full preview for approvers.
- **System principals:** `metadata_principals`, keyed by application ID, holds each
  machine caller's scope: name globs (`sales_*.bronze.*`), allowed keys and operations,
  environments, a per-change object cap, and whether it may skip approval. A principal
  can edit only inside its scope, and owner tags don't widen it.
- **Unchanged checks:** governed-tag allowed values (`tag_governed.py`), risk, lint and
  ABAC impact (`tag_risk.py`, `tag_lint.py`, `tag_abac.py`) are reused in preview, fed
  from the snapshot instead of live reads.

### D5. Environments: one instance, every environment, testable lower down

- **Mapping:** `METADATA_ENVIRONMENTS` has one row per environment with an explicit
  `catalogs` list and/or a `catalog_pattern` such as `{base}_dev`. A catalog belongs to
  the first environment that lists it or whose pattern matches it; otherwise its
  `environment` is NULL (no environment rules, no counterparts).
- **Counterparts:** the pattern defines them. `sales_dev` matched by `{base}_dev` has base
  `sales`, so its `prd` counterpart under `{base}_prd` is `sales_prd`, with schema, table
  and column names unchanged. Catalogs only in an explicit list have no counterpart.
- **Copy to other environments:** submit accepts "also apply to: dev / tst / stg / prd".
  The service resolves each counterpart, creates pending rows where the object doesn't
  exist yet, and authorizes each target separately.
- **Promote:** copies a lower environment's desired tags and comment to its counterpart as
  an ordinary change set (`replace_tags` + `set_comment`). Test in dev, then promote.
- **Per-environment approval:** each environment's `approval` is `sensitive` (default:
  only sensitive changes need approval) or `all` (every change needs approval).
- **Lower-environment deployments** (dev and stage apps) run
  `METADATA_RECONCILE_MODE=plan`: real diffs against the metastore, no writes. Set
  `METADATA_RECONCILE_CATALOGS` to a sandbox list to enforce only there, and keep those
  catalogs out of production's list.

### D6. One change service for every surface

- **Operations:** `set_tags` (merge), `unset_tags`, `replace_tags` (the given dict becomes
  the full set; exempt keys are untouched), `set_comment` and `clear_comment`.
- **Optimistic concurrency:** each tag and the description take an optional expected
  version (`0` = must not exist yet). A mismatch returns 409. A UI user editing
  `classification` and a pipeline setting another key on the same table never block each
  other.
- **Flow:** preview, then commit. `Idempotency-Key` (header or body) makes retries safe:
  the same actor and key returns the original result without writing again.
- **Approval routing:** `on_approval_needed` is `request` (default: the whole change goes
  to approval if any item needs it), `split` (commit the authorized, non-sensitive items
  now and file the rest) or `reject` (refuse with the reasons).
- **Limits:** `METADATA_MAX_OBJECTS_PER_CHANGE`, which a principal's own cap can raise.
- **Callers:** the UI, REST, MCP and agent tools are thin wrappers over
  `services/metadata/changes.py`.

### D7. Machine identity: Databricks OAuth through the Apps proxy, no app-issued keys

- **How callers authenticate:** a first- or third-party system uses a Databricks service
  principal's OAuth M2M token, which needs `CAN_USE` on the app, and calls the app's URL
  with `Authorization: Bearer <token>`. The Apps proxy validates the token and forwards
  identity headers. The app issues no keys of its own.
- **Recognizing a principal:** `deps.py` treats a caller as a service principal when the
  forwarded email is missing or isn't an email address, or when any forwarded identifier
  (`X-Forwarded-User`, `X-Forwarded-Preferred-Username`, `X-Forwarded-Email`) matches an
  enabled `metadata_principals.application_id`. A registered principal gets an actor of
  type `service_principal` and its scope; an unregistered one is refused with 403 and a
  message naming the identifier to register. Humans resolve exactly as before.
- **MCP identity fix:** the MCP shim resolves the caller's full identity (email,
  Databricks groups, app roles, and principal scope) through the same code `deps.py`
  uses. Tools that write, or that depend on who is calling, refuse on deployed targets
  when no forwarded identity is present, and never fall back to the app service
  principal.

#### Spike result: what the Apps proxy forwards

From the Databricks Apps documentation (HTTP headers, app authorization, AppKit):

- Documented headers on every proxied request: `X-Forwarded-Host`,
  `X-Forwarded-Preferred-Username`, `X-Forwarded-User`, `X-Forwarded-Email`,
  `X-Real-Ip`, `X-Request-Id`; `X-Forwarded-Access-Token` only when user authorization
  is configured.
- AppKit documents `X-Forwarded-User` as always present and `X-Forwarded-Email` as
  optional.
- Bearer-token calls to an app's `/api/` routes are documented; the caller needs
  `CAN_USE`. A service principal obtains its token with
  `WorkspaceClient(host, client_id, client_secret).config.authenticate()`.
- **Not documented:** the exact values the proxy puts in these headers for a service
  principal. The design doesn't depend on them: recognition works on any of the three
  identifiers, `GET /api/v1/metadata/whoami` reports what the app resolved, and the app
  logs the *shape* of the identity headers it receives (which are present, whether the
  email looks like an address; never values of tokens) on a principal's first call so
  an operator can confirm the registration key in their environment.

### D8. Agent workflow `metadata_change`

1. `prepare_metadata_change` (side effect `read`) validates the change, checks
   permissions, classifies sensitivity, records a digest and returns `report_markdown`
   built with `safe_text`/`safe_code`.
2. A `data_owner` gate whose approvers come from the new `metadata_owner` approver source
   in `spec_loader.py`: the owner groups of the objects in the prepared change.
3. An optional governance gate, auto-approved when the change isn't sensitive.
4. `record_metadata_change` (side effect `app_write`) writes only the prepared change,
   rechecked against current versions, then enqueues a reconcile.

Chat tools `search_metadata`, `get_metadata`, `preview_metadata_change` and
`submit_metadata_change` are exposed over MCP (`external=True`) and described in
`agents/instructions/metadata_change.md`. Changes that are authorized and not sensitive
skip the workflow but still produce a change set.

### D9. Remove GitOps for tags

- **Deleted:** `tag_sql.py`, `open_tag_change_pr`, `tag_change.json` (published rows are
  set to draft at startup, as in ADR-0005), the engine's GitOps branches, `/tags/mode`,
  remote policy loading, and the Delta ledger insert in `tag_apply.py`.
- **Retired settings:** `GOVERNANCE_TAGS_*` move into `RETIRED_KEYS`;
  `governance_tags_base_branch` leaves `databricks.yml`.
- **Kept:** the GitHub provider and the `pr_merge` gate (other features use them), and
  `tag_creation.json`.
- **Old `/api/v1/tags` endpoints:** removed with the UI cutover. Old `tag_change` requests
  stay readable in My Requests.

### D10. Search performance

- **Postgres:** `pg_trgm` GIN indexes on `metadata_objects.fqn_key` and `comment`;
  B-tree indexes on `metadata_tags (key, value)` and `(object_id)` serving `key=value`,
  `key=*` and `!key` filters; B-tree indexes on `catalog`, `environment`, `object_state`
  and `drift_detected_at`. If the `pg_trgm` extension can't be created, search still
  works with `ILIKE`, just slower, and startup logs a warning.
- **SQLite:** the same B-tree indexes, with `LIKE` in place of trigram search.
- **Server side:** paging, sorting and facets. Target under 300 ms at 100k rows; the
  local cache today holds about 17.7k objects across 204 catalogs.

### D11. Cutover

1. Run the observed sweep.
2. Adopt the current state into `metadata_objects` and `metadata_tags`: each tag and
   description gets `source=adopt`, the system actor and the time it was observed.
3. Run in `plan` mode for a while and review the drift report.
4. Switch to `enforce`.

The runbook is in [`docs/runbooks/metadata-cutover.md`](../runbooks/metadata-cutover.md).

## Open items, settled

- **Environment catalog naming:** both. An explicit list for membership and a
  `{base}` pattern for membership and counterparts (D5).
- **Service-principal headers:** recognized on any forwarded identifier, confirmed per
  environment with `whoami` and header-shape logging (D7).
- **OmniGuard certification through the change service:** deferred. The exempt key keeps
  the two from fighting; moving certification onto a system principal is a follow-up.

## Consequences

- The page, search and preview stop depending on warehouse latency; the reconciler is the
  only background UC reader and writer.
- Full enforcement removes tags added outside the app on managed objects. Plan mode and
  the drift report make that visible before enforcement starts, and the circuit breaker
  stops a mass removal.
- Existing GitOps users lose the PR review step. Approval of sensitive changes moves into
  the app's own workflow, and history and revert move into the change log.
- The governance service principal needs `APPLY TAG` on managed objects, ownership or
  `MANAGE` to set comments, and `ASSIGN` on governed tags it applies.

## Contract (frozen for the parallel workstreams)

Pydantic shapes live in `backend/app/services/metadata/types.py`; TypeScript mirrors
them in `src/types/metadata.ts`. All routes are under `/api/v1/metadata` and gated by
`features.metadata_v2`. Names travel as query parameters or in the body, never in the
path, since they contain dots.

| Method and path | Body / query | Returns | Who |
|---|---|---|---|
| `GET /whoami` | | `WhoAmI` | signed in |
| `GET /config` | | `MetadataConfig` | browse |
| `GET /objects` | `SearchQuery` as query params (`tag=key=value`, `tag=key`, `tag=!key`, repeatable) | `SearchResult` | browse |
| `GET /object` | `name`, `column?` | `ObjectDetail` | browse |
| `GET /keys` | `q?`, `limit?` | `List[KeyInfo]` | browse |
| `POST /changes/preview` | `ChangeRequest` | `ChangePreview` | browse |
| `POST /changes` | `ChangeRequest`, header `Idempotency-Key?` | `ChangeResult` | signed in |
| `POST /changes/promote/preview`, `POST /changes/promote` | `PromoteRequest` | `ChangePreview` / `ChangeResult` | signed in |
| `GET /changes` | `actor?`, `source?`, `name?`, `page`, `page_size` | `ChangeSetPage` | browse |
| `GET /changes/{id}` | | `ChangeSet` (with events) | browse |
| `POST /changes/{id}/revert/preview`, `POST /changes/{id}/revert` | `RevertRequest` | `ChangePreview` / `ChangeResult` | signed in |
| `POST /reconcile` | `ReconcileNotify` | `ReconcileAccepted` | signed in (principals: in scope) |
| `GET /drift` | `catalog?`, `page`, `page_size` | `SearchResult` (drifted only) | browse |
| `GET /runs`, `GET /runs/{id}` | | `ReconcileRunPage` / `ReconcileResult` | browse |
| `POST /runs` | `RunRequest` | `ReconcileResult` | admin |
| `GET/POST /principals`, `PUT/DELETE /principals/{application_id}` | `PrincipalIn` | `PrincipalOut` | admin |
| `POST /adopt` | `AdoptRequest` | `ChangeResult` | admin |
| `POST /import/preview`, `POST /import` | `ImportRequest` (JSON rows, or CSV text `name,column,comment,tags`) | `ChangePreview` / `ChangeResult` | admin |

Errors: 400 invalid input, 403 not permitted (with the reason), 404 unknown object or
change set, 409 version conflict, 422 blocked by policy. Each body is FastAPI's
`{"detail": ...}`: a plain message for 400/403/404,
`{"message", "conflicts": [VersionConflictItem]}` for 409, and
`{"message", "preview": ChangePreview}` for 422.

MCP and chat tools (`backend/app/tools/governance/metadata.py`, `external=True`):

| Tool | Arguments | Side effect |
|---|---|---|
| `search_metadata` | `query=""`, `catalog=""`, `tag=""`, `securable_type=""`, `editable_only=False`, `limit=25` | read |
| `get_metadata` | `name`, `column=""` | read |
| `preview_metadata_change` | `changes: list[ChangeOp]`, `reason=""`, `environments: list[str]=[]` | read |
| `submit_metadata_change` | `changes`, `reason`, `environments=[]`, `on_approval_needed="request"`, `idempotency_key=""` | app_write |

Workflow tools (`backend/app/workflows/tools/metadata.py`): `prepare_metadata_change`
(read) and `record_metadata_change` (app_write).
