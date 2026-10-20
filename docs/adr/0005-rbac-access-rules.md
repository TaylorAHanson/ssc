# ADR-0005: RBAC access rules — ledger, workflows, export

- **Status:** Proposed
- **Date:** 2026-10-16
- **Supersedes:** the RBAC automation built on branch `feature/EDTFNDTN-4251`
  (described in [`docs/handovers/rbac.md`](../handovers/rbac.md); not merged)
- **Numbering:** ADR-0004 (Terramate) is referenced from code but lives outside this
  repo, so this record continues at 0005.

## Context

Users need to request Databricks access in chat. An approver signs off, the change is
recorded as desired state, and an existing external Terraform pipeline applies it.
That pipeline is fed by **CSV files a person exports by hand from an Excel RBAC
workbook**: one CSV per sheet, with no action column. The workbook is the desired
state, and a revoke is simply a row that is no longer there.

A coworker built this on another branch in two generations. The second, the one at
the branch head, has 44 commits, about 147 files and ~30k lines. The handover was
produced from that diff. Problems it shows, beyond individual bugs:

- **A second approval system.** RBAC requests skipped the workflow graph and sat in a
  new `pending_approval` status. Platform Admins then approved hour-bucketed
  "bundles" through their own endpoints, and a fake timeline was drawn for those
  requests. The platform's gates, approvals inbox and request statuses were
  duplicated rather than used.
- **The same policy enforced in up to four places.** Rego packages, a
  `before_insert` ORM hook driven by `rbac_config.yaml`, an inline Python fallback
  (which is what actually ran, because OPA was skipped inside the event loop) and the
  agent instructions. They disagreed with each other. For example, a revoke workflow
  shipped while the config auto-denied every revoke made through the agent.
- **Two sources of truth.** Writes went to a Lakebase replica, while most read,
  verify and drift tools still parsed the xlsx.
- **Tool sprawl.** About 20 RBAC tools and 9 workflows. Several were never wired up
  (compensation, Phase 2 stages, the expiry worker), and a critical `NameError`
  meant every replica write after the audit row failed.
- **A direct-edit grid that skipped validation parity.** Its staged edits were
  silently discarded when switching sheets, and its insert dropdowns could not
  introduce new values.
- **Unsafe scaffolding.** Unauthenticated export, download and debug endpoints, a
  download path with no traversal check, crash logs written to a personal workspace
  path, and build pipeline changes made to work around a stale frontend.
- **An export the pipeline cannot consume as it is.** The pipeline reads one CSV per
  sheet, holding that sheet's full contents under its own headers. The branch
  instead wrote one CSV per environment per approved bundle. Each file held only
  that bundle's requests, under the branch's own snake_case columns (one version
  added a `request_type` grant/revoke column), and was named by bundle id. UC
  requests were the only ones exported. Read as desired state, a bundle file would
  drop every grant outside it.

The ideas worth keeping were sound: groups-only grants; system groups are
immutable; hard-denied privileges; justification in the requester's own words;
validating every atom of a packed cell; naming rules aware of the environment;
optimistic concurrency; preserving the workbook's packed-cell format; failing closed
on unsupported types.

### Constraints confirmed with the product owner

1. Exported CSVs must **match the spreadsheet exactly**. Today's production process
   is a person exporting each sheet to CSV, and the Terraform pipeline consumes those
   files as they are.
2. How often the export runs is an **admin setting**, not a fixed hour.
3. The workbook is **imported once**. Because we test repeatedly, a guarded **reset
   and re-import** is required.
4. A **direct-edit grid** for admins is a requirement. It must be audited and made
   safe, not removed.
5. **Scope is groups on securables only.** Nothing here touches individual users.
   **Converge** with the existing workflows that grant securables live, since there
   are no real users yet. Adding users to groups is out of scope, and the existing
   user-to-group workflow (`workspace_access`) stays exactly as it is.
6. **UC securables, UC metastore and WS securables have different approvers.** Each
   type's approver is a Databricks (SCIM) group set in Admin → Settings, not a
   hardcoded name. That group also edits its type's rows directly. There is no new
   app role. Risk-tiered routing comes later.

### Workbook format (confirmed)

The workbook has nine sheets, and each one is exported as is to a CSV with the same
name:

- `WS_Securables_RBAC_Dev`, `WS_Securables_RBAC_Tst`, `WS_Securables_RBAC_Stg`,
  `WS_Securables_RBAC_Prd`
- `UC_metastore_RBAC`
- `UC_Securables_RBAC_Dev`, `UC_Securables_RBAC_Tst`, `UC_Securables_RBAC_Stg`,
  `UC_Securables_RBAC_Prd`

Headers, exact text and order:

- **UC sheets:** `Environment`, `Securable Type`, `Catalog`, `SchemaName`,
  `ObjectName`, `Owner`, `PrincipalType`, `PrincipalName`, `AccessPrivilege`
- **WS sheets:** `Environment`, `PrincipalType`, `PrincipalName`, `WorkspaceName`,
  `SecurableType`, `SecurableName`, `SecurablePermission`
- **Metastore sheet (`UC_metastore_RBAC`):** `Securable Type`, `Catalog`,
  `SchemaName`, `Owner`, `PrincipalType`, `PrincipalName`, `AccessPrivilege` are
  visible, with no `ObjectName`. The handover also lists a leading `Environment`
  column, which may be hidden. The import reads every column, hidden or not, the
  same way Excel's CSV export does, so the round-trip holds either way.

The workbook also has a scratch tab, `Sheet1`. Only the managed sheets are imported:
the two per-environment patterns for each configured environment, plus
`UC_metastore_RBAC`. The preview lists every sheet it skipped.

Values seen in the UC sheets:

- `Securable Type` values include `schema` and `view`. Schema rows leave
  `ObjectName` empty; view rows carry the view name (for example `fiscal_month`).
- `Owner` is the same metastore-admin group on every row seen.
- `AccessPrivilege` packs the prerequisite with the grant on the same row, for
  example `USE_SCHEMA;SELECT` or `USE_SCHEMA;SELECT;MODIFY` on a schema. View rows
  hold `SELECT` or `MODIFY` alone.
- **Principals are packed per privilege set.** A row means "these principals hold
  exactly this privilege set on this securable", and `PrincipalName` can list a
  dozen or more groups separated by `;`.
- People add a principal by appending it to the end of that list, and mark the
  addition with red text or a row fill. CSV carries no formatting, so the import
  ignores it, and the ledger's history replaces that convention.
- A few cells seem to list `sp_…` names under `PrincipalType` `group`. Naming rules
  flag these as import findings; they are not rejected.

Values seen in the WS sheets:

- `Environment` is lowercase (`dev`), while sheet suffixes are title case (`Dev`).
- `PrincipalType` is `group` or `service principal`, with a space and not an
  underscore. Exports must write the workbook's own spelling.
- `SecurableType` includes `workspace` and `workspace entitlement`.
- `SecurablePermission` includes `ADMIN`, `USER`, and packed values such as
  `workspace_access;databricks_sql_access`.

Values seen in the metastore sheet:

- Rows cover every environment in one sheet (names end in `_stg`, `_prd`, …).
- A metastore-level object such as a `connection` carries its name in `Catalog`,
  with `SchemaName` empty.
- Service principals hold `MANAGE;USE_CONNECTION`. `MANAGE` is not on the default
  allow list for new changes (D7), so these rows import with a finding, not an error.

## Decisions

### D1. A Lakebase ledger is the single source of truth, faithful to workbook rows

Table `rbac_rules` holds **one row per workbook row**, packed cells and all. A sheet's
rows are its desired state. Nothing reads the xlsx after import.

Columns: `id`, `sheet`, `plane` (`uc` | `uc_metastore` | `workspace`),
`environment`, `position` (export order), the canonical fields (`securable_type`,
`catalog`, `schema_name`, `object_name`, `workspace_name`, `owner`,
`principal_type`, `principal_name`, `privilege`), `extra` (JSON, for unmapped
columns so the export round-trips), `status` (`active` | `deleted`), `version`
(optimistic lock), `row_hash` (indexed, not unique), `source`
(`import` | `request` | `admin_edit`), `source_ref`, and `created_*` / `updated_*`.

- Storing rows as they appear in the sheet, rather than one row per atom, is what
  makes an exact export possible. Atom-level semantics, the cartesian product of
  packed principals and privileges, live in one module (`cells.py`) used for
  validation, diffing and finding existing grants.
- Changes follow how people already edit the sheet, so the export reads as if a
  person had made them:
  - **Grant:** find an active row on the same sheet with the same securable,
    owner and principal type, and *exactly* the requested privilege set, then append
    the principal to the end of its `PrincipalName` list. If there is no such row,
    append a new row.
  - **Prerequisites** travel in the same privilege set, as the sheet does: a
    `SELECT` on a schema becomes `USE_SCHEMA;SELECT`. A missing `USE_CATALOG` on the
    catalog is added as its own row.
  - **Revoke of a principal's whole privilege set:** remove the principal from that
    row's list.
  - **Revoke of part of a set:** remove the principal from the row, then grant it
    the remaining privileges using the rule above.
  - A row left with no principals or no privileges is soft-deleted.
  - Each rule is deterministic, and `cells.py` tests cover every case.
- Duplicate rows already in the workbook are kept, so the round-trip is exact, and
  flagged as a data-quality finding.
- An append-only `rbac_rule_events` table records every change: `before`, `after`,
  `action`, `actor`, `reason`, `source`, `source_ref` and `change_set_id`. It is the
  audit trail and the basis for history and revert.
- `rbac_imports` and `rbac_exports` record each import (with the header layout
  captured per sheet) and each export run (files, checksums, row counts, change
  summary, ledger watermark).

*Rejected:* the xlsx as the live store (locks, last-write-wins, no concurrency
control), and one row per atom (cannot reproduce the sheet exactly).

### D2. Publish as a scheduled full snapshot: one CSV per sheet, identical to a manual export

The exporter writes **one CSV per sheet**. Headers are the exact text captured at
import, in the same column order, and rows follow `position` order. Every run writes
the **complete** current state of each sheet, never a delta, which is the same
declarative contract the pipeline reads today.

- **Schedule:** `RBAC_EXPORT_CRON`, a live-reschedulable cron setting; an optional
  "export after every change" toggle (debounced); and an "Export now" action.
  A run is skipped when nothing has changed since the last successful export,
  unless forced.
- **Destination:** every path is a setting.
  - Each environment has its own export path (in the environments setting), plus
    `RBAC_METASTORE_EXPORT_PATH` for `UC_metastore_RBAC`, which belongs to no single
    environment.
  - A path can be a UC Volume (`/Volumes/...`), a Workspace path, or a local
    directory. Local directories are allowed only in dev environments, because a
    Databricks App's disk is wiped on restart.
  - A blank path means that environment's sheets are not exported, and the Exports
    tab says so rather than skipping silently.
  - A **Test path** action writes and deletes a probe file, which proves the app's
    service principal can write there.
  - Each file is named after its sheet, as is (`{sheet}.csv`), matching today's
    manual export. There is no file name setting.
- **CSV format: Excel's conventions, each one a setting.** No real manual export is
  available before the customer environment, so the defaults follow what Excel
  writes:
  - comma delimiter;
  - a field is quoted only when it contains a comma, a double quote or a line
    break, and embedded quotes are doubled;
  - CRLF after every row, including the last;
  - every column in the sheet's used range, hidden ones included, at the width
    recorded at import;
  - cell text as stored.

  Encoding defaults to UTF-8 **without** a byte-order mark. The values are ASCII,
  so the mark is the only byte that could differ, and every CSV reader accepts a
  file without one. The mark and the line ending can be changed in Settings.
- **Proving it matches:** the Exports tab has a **Compare with a manual export**
  tool. Upload the CSV a person exported for a sheet, and it reports:
  - whether the files are byte-identical;
  - if not, why: byte-order mark, line endings, header text or order, quoting, and
    rows present on only one side.
  
  This is the cutover check in the customer environment, and it replaces the golden
  fixture we don't have. Repo tests use a synthetic workbook built from the
  confirmed layouts (see Verification).
- **Each run** records a manifest (export id, time, per-file sha256 and row count,
  change summary, watermark) and an archived copy of every file in the database
  (`rbac_exports`, `rbac_export_files`), kept for `RBAC_EXPORT_ARCHIVE_DAYS`. Nothing
  but the sheet CSVs is written to the export folders, because the pipeline reads
  those folders and an extra file there could be mistaken for a sheet.
- **Single flight:** an advisory lock on Postgres, an in-process lock on SQLite.
- After a successful run, each request whose changes were included gets an
  `rbac_exported` fact, which appears on its timeline.

*Rejected:* hour-bucket bundles (they coupled approval to export timing); delta CSVs
(the pipeline cannot consume them); GitOps PRs (not what the pipeline reads; the
publisher sits behind a small interface so a PR publisher can be added later).

### D3. Import the workbook once; reset and re-import is guarded

An admin uploads the workbook, or points at a Volume path, and first sees a
**preview**: rows per sheet, the captured header layout and duplicates. The workbook
is the source of truth, so imported rows are **copied as they are and not judged
against the policy** (D7); production data must load, and a seed import that
reports thousands of policy warnings on approved access only buries real problems.

Headers are mapped to canonical fields using the confirmed layouts above as in-code
defaults. Admins can add aliases if a header is ever renamed. The import records the
actual header text and order, and the export reuses it. Any unknown header passes
through via `extra`, and the preview flags it. The WS columns map as
`WorkspaceName` → `workspace_name`, `SecurableType` → `securable_type`,
`SecurableName` → `object_name` and `SecurablePermission` → `privilege`.

**Reset and re-import** requires `RBAC_ALLOW_RESET`, a typed confirmation and the
Platform Admin role. It is blocked while access requests are in flight. A reset
soft-deletes the current rules as one recorded change set, so history is kept and
the next export reflects the new state.

**The workbook is retired after the seed import.** From go-live:
- The ledger is the desired state, and the scheduled export replaces the manual
  CSV export.
- The Access Rules page replaces editing the workbook, and its History tab replaces
  marking changes with red text.
- Nothing reads the xlsx again.

Cutover:
1. Freeze the workbook (read-only, archived).
2. Run the final import.
3. Check the first export against the last manual CSVs with **Compare with a manual
   export** (D2).
4. Switch the pipeline's input paths to the export.

`RBAC_ALLOW_RESET` defaults to off and stays off in production. A re-import after
go-live would discard every change made in the app since the seed; a reset can be
reverted, but nobody should need to.

*Not planned:* a "download as workbook" (xlsx) export for offline review. The Rules
tab and the CSVs cover that need; add it only if someone asks.

### D4. One workflow instead of nine; retire the live-grant workflows

`rbac_access_change` grants or revokes privileges for **groups** (and, where an
environment's policy allows it, service principals) on UC securables, metastore
objects and workspace items: workspace assignment, workspace entitlements, folder
ACLs and service principal roles. One request can carry many items, which covers
bulk. Fulfilment: ledger write → next export → Terraform.

**One type per request.** Each request covers exactly one type:
- `uc`: the `UC_Securables_RBAC_*` sheets;
- `uc_metastore`: `UC_metastore_RBAC`;
- `workspace`: the `WS_Securables_RBAC_*` sheets.

A request can still span environments and many items within its type.

When an ask spans types (for example workspace `USER` on a workspace plus `SELECT`
on a schema), `preview_access_change` returns one proposal per type. The assistant
files them together as linked requests that share a `group_ref`. Each report lists
its sibling requests. Each type's group approves only its own part, and outcomes
are independent: a rejected workspace part does not undo an approved UC part. A
mixed change set reaching `prepare` is refused with "split by type".

Stages:
1. `prepare` (read): validate, add prerequisites, and diff against the ledger, so
   the request carries its exact row changes. It writes the request's type of rule
   to context (`writes_context: ["plane", "nothing_to_change"]`) and returns a
   `report_markdown` of the prepared change for approvers.
2. Approval gate, type `platform_admin`, with
   `approver: {"source": "access_rule_type"}`: the engine looks up the approver
   group for the request's type from settings (D6).
3. `record`: work the change out again against the current ledger, then write it
   in one transaction, idempotent on the request id. The next export carries it to
   Terraform.

`access_rule_type` is a declarative approver source alongside `group` and
`approver_group_tag`, so the spec itself says who approves and the workflow editor
can show it. It is the only engine change. The gate type stays `platform_admin`:
that type already lets the routed group's members and Platform Admins approve, and
hides Edit & Restart.

**Retired:** `data_access_request`, `batch_data_access` and
`catalog_schema_table_access`, which grant securables live and to individual users.
Their instructions, golden transcripts and any tools nothing else uses
(`grant_uc_access` and `resolve_data_owners` if they have no other callers) go with
them.

**Untouched:** `workspace_access`, the existing user-to-group workflow. It is out of
scope.

**Deferred:** ownership transfer; role assumption (Public Preview, off behind a
feature flag in the original); time-bound access and expiry (the sheet has no
expiry column, so desired state cannot express it); emergency access.

**Drift** is a view, not a workflow (D15).

### D5. No live-grant mode for securable access

The convergence option mentioned keeping live grants as a fulfilment mode. Securable
grants will **only** go through the ledger and Terraform. Terraform owns those
grants, so a live `GRANT` is drift that the next apply removes, and the two paths
would disagree about the truth.

### D6. Standard gates, one approver group per type, and segregation of duties

- **Three settings name the approver groups:** `RBAC_UC_APPROVER_GROUP`,
  `RBAC_METASTORE_APPROVER_GROUP` and `RBAC_WS_APPROVER_GROUP`. Each takes a
  Databricks group's exact display name. The settings are read when a request
  reaches its approval gate, so a change applies to the next request without a
  redeploy.
- **Routing uses what the platform already has.** The poller stores the group name
  in the approval's `assigned_to_role`. The inbox shows the approval, and lets a
  user act on it, when that name is among the user's Databricks groups, which
  `current_user.me()` returns at sign-in (`approval_visibility_filter`,
  `_authorize_approval_actor`). No membership sync and no role mapping are needed.
- **If a type has no group configured,** the approval falls back to Platform Admins,
  who can already act on any approval. The report of the prepared change says so,
  and the approval card reads "Platform Admins (no … group set)".
- The Access Rules page shows each type's group, and warns when the app cannot find
  that group in Databricks. A typo would otherwise leave approvals visible only to
  Platform Admins. The check lists the app workspace's groups (cached for five
  minutes), so an account group not assigned to that workspace also shows as not
  found. That warning is useful too: a group the workspace can't see is unlikely to
  appear in its members' `me().groups`, so they may not see the approval (to be
  confirmed in the customer environment with the other routing checks).
- Group names are matched case-insensitively. Before this change,
  `_authorize_approval_actor` compared `assigned_to_role` only with the user's app
  roles, so a group-routed approval was visible but could not be acted on; it now
  checks the user's Databricks groups too.
- **Verified in the customer environment, not here.** Routing is tested manually
  there (see Verification). That includes whether `me().groups` includes groups a
  user belongs to only through a nested group; if not, the settings help text tells
  admins to add approvers to the group directly. Repo tests mock group membership.
- **No new app role.** Without one, the role-name cleanup that a new role would have
  needed is out of scope. Platform Admin can approve anything, as today.
- Requests use the **standard statuses and Approvals inbox**: no `bundle_id`, no new
  statuses, no parallel endpoints.
- **Requesters cannot approve their own requests**, platform-wide, Platform Admins
  included. This is the `APPROVALS_DENY_SELF_APPROVAL` setting, on by default. The
  platform did not enforce this before. Rejecting your own request stays allowed,
  because it is a withdrawal and needs no second person.
- **Approve several at once** in the Approvals inbox (multi-select, confirm, results
  per item). This keeps what bundles were good for, letting approvers clear a queue,
  without a second system.
- *Rejected:* one request with up to three gates in sequence. A rejection on one
  type would sink the others, and later approvers would wait on earlier ones.
- *Later:* per-environment approver overrides (for example a stricter group for
  `prd`), as an optional field in the environments setting. Also risk-tiered routing. The policy module gains an `escalate` severity, and a
  second gate (for example a security group) auto-approves unless the prepared change
  contains escalated items.

### D7. One policy module, configured from settings, enforced on every write path

`services/rbac/policy.py` evaluates a change set and returns findings per atom with
severity `deny` or `warn`. Every write path calls it: the preview tool (chat
pre-check), the `prepare` step, the `record` step (re-check) and the admin edit API.
The workbook import doesn't call it (D3). There is no other copy: no ORM hook, no
Rego content rules, no inline fallback.

The policy judges **what a change adds**: new atoms and changed values. Atoms a
change leaves alone are not re-judged, so existing rows that predate a rule (for
example `MANAGE` on connections) never block an unrelated edit to the same row.
Removing atoms is always allowed, except on system groups.

Rules, all editable in Admin → Settings → Access Rules, with generic in-code
defaults and no customer naming hardcoded:

- Allowed privileges per securable type, **allow only**: a change can add only the
  privileges listed for its type, and a type with no row takes nothing new. The
  default is the in-code registry (following the Databricks UC model) minus
  `ALL_PRIVILEGES`, `MANAGE`, `SET_SHARE_PERMISSION` and the metastore's
  `CREATE_CATALOG`-style privileges. A privilege Databricks adds later is blocked
  until a Platform Admin lists it. The registry now only supplies the defaults,
  spellings and deprecation hints: a deprecated token not on the list is denied
  with a hint, e.g. `USAGE` → `USE_CATALOG` / `USE_SCHEMA`.
- Per environment, the workspace and catalog names a change may add; blank leaves
  them free (D14).
- Allowed principal types, per environment. The default is `group` and `service
  principal` everywhere. The original allowed service principals in `dev` only,
  but the workbook grants them in every environment and users need to request
  them; an environment's **Service principals** switch can still turn them off.
  `user` is never allowed.
- System groups that self-service may never touch.
- Policy per environment: self-service on/off, write privileges allowed, and
  whether admin edits need a second approver.
- Naming rules: field, regex with an `{env}` placeholder, the securable types it
  applies to, exceptions, and severity. One mechanism covers both the original
  field patterns and its environment-suffix consistency checks.
- Justification minimum length. Warnings for overlap with an in-flight request, and
  for a broad grant where a schema-level one would do.

*Rejected:* Rego for content rules. Locally OPA runs in shadow mode, so the rules
would not enforce there, which is how the original ended up with an inline fallback
that drifted. Admin-editable rules also fit the settings store better than a policy
redeploy. The `ToolExecutor` + OPA chokepoint still governs the mutating `record`
tool like any other. *Dropped:* the "3+ consonants" junk-data regex. It rejects
real words, and naming rules cover the real need.

### D8. Direct admin edits, with the same safeguards as requests

Admins edit the grid directly, which is the requirement, under these safeguards:

- **Who can edit:** a type's approver group edits that type's rows (D6), and Platform
  Admin edits all of them. Whoever may approve a change may also make it directly,
  with a reason and an audit trail. **Viewer roles** are a setting (default:
  Governance Admin). An approver group sees every type but edits only its own.
- **Staged change set:** add, edit and delete rows. A server-side dry run returns
  findings and a diff. **Commit requires a reason** and is all-or-nothing.
- **Concurrency:** each write is
  `UPDATE … WHERE id = :id AND version = :v`, and a mismatched row count returns 409
  with the current row. This closes the read-then-write race in the original.
- **The same policy module** applies. The privilege allow list cannot be bypassed from the grid;
  changing policy is itself an audited settings change.
- **Limits:** a maximum number of changes per commit; an unsaved-changes guard when
  switching sheets or leaving the page.
- **History and revert:** each change set shows who, when, why and the diff.
  Revert creates an inverse change set, which is validated and needs its own reason.
- **Optional two-person rule per environment.** Commit then files an
  `rbac_access_change` request on the admin's behalf. Another member of that type's
  approver group must approve it; self-approval is blocked.
- Admin edits reach Terraform through the same export as requests.

### D9. Preview → confirm → record; recording never goes beyond what was approved

The agent previews the change set, the user confirms, then the agent submits; there
is no shortcut that skips confirmation. At `record`, the change is worked out again
against the current ledger and only the **intersection** with the approved change is
written.
Items that are now no-ops, for example because an admin already added the rule, are
reported, never widened. Idempotency is keyed on the request id and runs through the
`ToolExecutor`.

### D10. Minimal tool surface

| Tool | Class | Where |
|---|---|---|
| `preview_access_change` | read | chat pre-check **and** the `prepare` workflow step (same service) |
| `record_access_change` | data_grant | workflow step only |
| `find_access` | read | chat: which groups hold which privileges on securable X, and what group G can reach |

Three new tools instead of about 20. `find_access` lets the agent check the ledger
before proposing anything. If the group already holds the privilege, or a broader
grant on a parent already covers it, the agent says so instead of filing a request
that would be a no-op.

### D11. Request lifecycle

An `rbac_access_change` request completes when its change is recorded in the
ledger. The completion message names the next scheduled export. The export later
adds an `rbac_exported` fact to the timeline. There are no new statuses or columns
on `requests`.

*Rejected:* holding requests open until export. That would need a new gate type in
the loader, poller and renderer, and the app still cannot see when Terraform applies.
Live verification is part of the drift view (D15).

### D12. Security and operations baseline

- Every endpoint is authenticated and checks roles. File downloads are addressed by
  id (export id + file id), never by path.
- No debug endpoints, crash-log scaffolding, cache-busting hacks or changes to the
  build and deploy pipeline.
- Configuration is read only through `settings`, never `os.environ`.
- The same behaviour on SQLite and Postgres: locking is chosen by dialect, and
  schema changes go through the idempotent helpers in `db/migrate.py`.
- Text from users or the ledger in a `report_markdown` is wrapped with
  `safe_text` / `safe_code`.
- Logging library only. Blocking SDK calls are offloaded with `asyncio.to_thread`.

### D13. The admin UI is one Access Rules page

The page is gated by the `features.rbac` capability. It is open to Platform Admins,
to viewer roles, and to members of any of the three approver groups; editing is per
type (D8). A header strip shows each type's approver group and warns when one is
unset or absent. The workspace's group list and its `me()` leave out account
groups that aren't assigned to the workspace, which Unity Catalog grants often
use. So the existence check falls back to the account's groups (served by the
workspace at `/api/2.0/account/scim/v2/Groups`) and reports `account_only`.
Sign-in adds the user's account groups (`/api/2.0/account/scim/v2/Me`, read with
their own token) to their entitlements, so members of such a group are routed
and can edit. If that read is refused, the user keeps their workspace groups. The
page has four tabs:

- **Rules:** a Type selector, then an Environment selector, to pick the sheet; column
  filters, search, inline editing for editors,
  a tray of staged changes, a review drawer with a reason field, and markers on rows
  that an in-flight request will change.
- **History:** change sets with a diff and revert.
- **Exports:** run history, files with checksums, authorized download, "Export now",
  the next scheduled run, the configured path per environment with **Test path**,
  and **Compare with a manual export** (D2).
- **Import:** preview, import, and reset (Platform Admin).

The table uses **TanStack Table** (headless, styled with the existing Tailwind) for
sorting, filtering and staged editing. No global CSS overrides.

### D14. Configuration

Everything goes in Admin → Settings → **Access Rules**, gated by `features.rbac`:

- environments (key, aliases, sheet suffix, export path, self-service, write
  privileges, two-person rule, the workspace and catalog names the grid offers)
- metastore export path (`RBAC_METASTORE_EXPORT_PATH`), export cron, export on
  change, CSV byte-order mark and line ending, archiving
- approver groups: `RBAC_UC_APPROVER_GROUP`, `RBAC_METASTORE_APPROVER_GROUP`,
  `RBAC_WS_APPROVER_GROUP` (Databricks group display names; they also grant edit
  rights for their type)
- viewer roles
- allow reset
- maximum changes per commit, minimum reason and justification length
- allowed privileges per securable type (`RBAC_ALLOWED_PRIVILEGES`), allowed
  principal types, system groups, default owner, naming rules, header aliases

`APPROVALS_DENY_SELF_APPROVAL` lives in the Approvals group.

### D15. Drift and live verification come in a later phase

The planned design: a scheduled job snapshots `system.information_schema.*_privileges`
and workspace ACLs into Delta. The Rules tab then shows live status per row
(present, missing, unknown), and a drift view lists grants that are live but not in
the ledger, with a one-click proposal for an access change. This is not built in the
first phase, and it is not a workflow.

## Disposition of the original artifacts

| Original | Disposition |
|---|---|
| 9 workflows | → 1 (D4); group membership out of scope (existing `workspace_access` kept); drift → D15; ownership, role assumption, expiry deferred |
| ~20 `rbac_*` tools, `governance_search`, `McpTool.__call__` tool-to-tool calls | → 3 tools (D10); tools call services, not other tools (so nothing bypasses the `ToolExecutor`) |
| `rbac_replica` + `rbac_mutation_records` | → `rbac_rules` + `rbac_rule_events` (D1) |
| `bundle_id`, `pending_approval` / `approved` / `exported` statuses, bundle endpoints, `RbacBundles` page, Approvals bundle section, synthetic timeline | Dropped → standard gates, bulk approve, `rbac_exported` fact (D6, D11) |
| 7 Rego packages, `rbac_config.yaml` agenticPolicies, `before_insert` hook, inline fallback | → one policy module (D7) |
| `estate.yaml`, `privileges.yaml`, `providers.yaml`, `securables.yaml`, `workflow_taxonomy.yaml` | → Admin settings + in-code registry (D7, D14) |
| Workbook parser/serializer/storage (filesystem/SharePoint/Git) | → import-only reader (D3); no writing back to the workbook |
| Per-bundle CSV per environment, own columns, UC only | → full snapshot per sheet with the sheet's own headers, on a schedule (D2) |
| The Excel workbook as the place people edit access | Retired after the seed import (D3) |
| Grid (direct edit) | Kept, with safeguards (D8, D13) |
| Discovery job, drift tools, drift page | Deferred, redesigned (D15) |
| Expiry worker, compensation tools, Phase 2 stages | Dropped (unwired or broken) |
| Unauthenticated debug/export endpoints, `run.py` crash logging, cache-bust redirect, `sync.include` change | Dropped (D12) |

## Consequences

- **Positive:** one approval system, one policy, one source of truth; three new tools
  instead of about 20; exports identical to today's manual process, so the Terraform
  pipeline is unchanged; every change, whichever path made it, is in one audit
  history and can be reverted; self-approval is blocked platform-wide.
- **Negative:** the three live-grant workflows are removed, so live UC grants are no
  longer offered (D5). Time-bound access waits for a desired-state format that can
  express expiry. Byte-level CSV fidelity cannot be proven before the customer
  environment. The defaults follow Excel's conventions, each convention is a
  setting, and the compare tool checks against a real manual export at cutover.
- **Follow-ups:** risk-tiered approvals (D6), drift and verification (D15), ownership
  transfer, and a GitOps publisher if the pipeline moves to YAML.

## Implementation notes

Choices made while building, within the decisions above:

- **Export baseline.** Only a run that wrote every file (`completed`) counts as
  exported. A partial or failed run leaves the baseline where it was, so the next
  scheduled run retries instead of waiting for another change.
- **Runs that skip.** A run is recorded as skipped, not written, when every path is
  blank, or when the ledger has no history yet, so a header-only CSV never reaches the
  pipeline before the seed import. Scheduled skips aren't stored; manual ones are.
- **`rbac_exported`.** One fact per request per newly written sheet, de-duplicated,
  shown under the request's Completed step on the timeline.
- **Import cell values.** The import reads each cell's stored value, which differs
  from Excel's CSV only for formatted values: blank rows inside a sheet are skipped
  (Excel writes them as empty fields), dates come out as ISO 8601, formatted numbers
  as plain numbers, and formulas as their cached values. The confirmed sheets are all
  text, so none of this is expected to matter; **Compare with a manual export** shows
  it if it does.
- **Import findings.** Only facts about the copy are reported: an empty managed
  sheet, an unrecognised column and an identical row. None blocks the import. Policy
  findings were dropped from the import after the first run against the real
  workbook reported over 3,000 warnings on access that was already approved.
- **Export after every change** is detected on the replica running the poller; a
  change made on another replica goes out with the next scheduled run.
- **Approver fallback.** With no group set for a type, the gate's `access_rule_type`
  source resolves no approver, so the approval is unassigned and only Platform Admins
  can act on it; the report and the approval card say so.
- **Nothing to change.** When every item is already in place, the gate approves
  itself and `record` writes nothing, so approvers never see a no-op.
- **Two-person requests can't be forged.** `submit_row_changes` (and the revert
  equivalent) records a server-side filing fact with a digest of the change; the
  prepare step refuses a direct-edit request without a matching fact, so the agent
  can't file one. `record` also refuses when the approver is the editor, on top of the
  platform-wide self-approval block. A request filed from chat that touches a
  two-person environment gets the same check, so it holds even with that block off.
- **The approved change is what gets recorded.** The gate is marked `editable: false`,
  so Edit & Restart isn't offered: editing a waiting request and approving it would
  let one Platform Admin author and approve the same change. As a second layer, the
  prepare step records a digest of the prepared change as a fact
  (`rbac_change_prepared`). `record` refuses a change that no longer matches it, and
  checks a direct edit against its filing fact again.
  The requester is always the request row's requester, never a context value.
- **The securable type decides the type of rule.** A caller can't route an item to
  another type's sheet and approvers by naming a plane: a mismatch is refused. On
  the grid, a row whose securable type belongs to another type of sheet is refused
  on commit.
- **Export watermark on Postgres.** Postgres assigns event ids at insert, so a
  later id can commit first. Ledger writes hold an advisory lock in shared mode
  until they commit. The export takes it exclusively just to read the watermark,
  so no uncommitted id sits below it. SQLite already has a single writer.
- **Reverts follow the two-person rule too.** Where it applies, a revert files a
  request instead of writing; approving it records the revert, whose reason ends with
  the request id. A revert spanning types can't be split the way a commit can, so it
  becomes one request that Platform Admins approve. A second open revert of the same
  change set is refused.
- **Agent guidance** is a prompt section that appears only when `find_access` or
  `preview_access_change` is available to the user.
- **Renamed steps.** The steps were first called `plan` and `apply` (tools
  `rbac_plan_access_change` and `apply_access_change`, fact `rbac_plan_recorded`,
  context key `rbac_plan`, approvers routed with `approvers_from`). They were renamed
  to `prepare` and `record` because people read them as a Terraform plan and apply.
  Old requests keep working: the old tool names still resolve (but aren't offered
  in the editor), each step lists its old name in `renamed_from` so a request
  checkpointed in it resumes, and `record` accepts a change prepared under the old
  key and fact. The prepare step still returns `approvers`, for an admin copy of the
  workflow that routes with `approvers_from`.
- **Retired workflows.** At startup, published rows for the three retired workflows
  (and admin copies of them) are set to draft and disabled, once. Their historical
  requests still open; one still in progress shows its last status, not Completed.
  The `catalog_schema_table` workflow keeps creating catalogs and schemas, but its
  grant and revoke instructions are removed, since those wrote grants to a second
  Terraform source (D5).
- **Allow only, not never-grant** (customer feedback). A deny list let every new or
  unlisted privilege through, so `RBAC_ALLOWED_PRIVILEGES` (one row per securable
  type) replaced `RBAC_HARD_DENIED_PRIVILEGES`, and one `privilege_not_allowed`
  finding (deny on both paths) replaced `hard_denied_privilege` and
  `unknown_privilege`. Its message lists what the type allows. Unknown or empty
  securable types are denied on direct edits too, where they used to warn. A saved
  override of the old key is retired and skipped at startup. Write-privilege
  detection still uses the registry and `CREATE_*`, so a newly allowed write
  privilege outside both isn't treated as a write.
- **Dropdown choices come from settings.** `GET /sheets` returns each sheet's
  `options`: its environments, the securable types and privileges on the allow list
  for its plane, the principal types per environment, and the environment's
  workspaces (WS sheets) or catalogs (UC sheets). Policy enforces the same lists, so
  a hand-built request can't step outside them. A new or changed `WorkspaceName`,
  workspace or workspace-entitlement `SecurableName`, or UC `Catalog` outside a
  configured list raises `value_not_in_options` (requests blocked, direct edits
  warned). Matching is case-insensitive, and request items take the configured
  spelling.

## Verification

No real workbook, manual CSVs, paths or groups are available outside the customer
environment, so verification is split.

**In the repo (automated):**
- A **synthetic workbook** is generated at test time with `openpyxl` (already a
  backend dependency). It follows the confirmed sheet names, headers and values,
  with generic names:
  - packed principal lists and `USE_SCHEMA;SELECT` sets;
  - `service principal` with a space;
  - a hidden column;
  - the metastore sheet both with and without a hidden `Environment` column;
  - a `Sheet1` scratch tab;
  - a value containing a comma and quotes, to exercise quoting.
  
  Import, then export, must reproduce each sheet under the Excel conventions in D2.
- Routing and edit rights are tested with mocked group membership.

**In the customer environment (manual, at cutover; checklist in the admin guide):**
1. Set the export path for each environment and for the metastore sheet; **Test
   path** passes for each.
2. Import the workbook, then use **Compare with a manual export** on all nine
   sheets against the last manual CSVs. Adjust the byte-order mark or line-ending
   setting if the tool points at either, until every sheet is byte-identical.
3. Set the three approver groups. Check that:
   - a member sees and can approve a request of their type;
   - a non-member cannot;
   - a member through a nested group behaves as the settings help text says;
   - a requester cannot approve their own request.
4. Run the pipeline against the exported files once before switching it over.
