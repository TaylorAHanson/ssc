# Preview Features Tracker — Research & Plan

> **Status (2026-10-02):** Phases 1–3 are built (see [As built](#as-built) for
> where the build differs from this plan). Not yet committed.

**Goal:** an admin tab that regularly pulls Databricks Public Preview and Beta
features, splits them into **account-level** and **workspace-level**, and tracks
each feature per workspace through `not requested → requested → approved →
implemented`. Moving a feature to *requested* starts a governed workflow run
(the same way OmniGuard runs start) covering the selected workspaces, with **one
batch approval**. After approval, workspace-level previews are **turned on by
the service principal**; account-level previews go to a manual **Implement**
task. The platform then **verifies** the feature is actually on, using a
service principal that is workspace admin but **not** account admin.

---

## Research: data sources

Everything marked *verified* was checked with read-only calls on 2026-10-01
against the `gtm-ai-agent` workspace (AWS) and the public docs feed.

### 1. Settings v2 metadata API — primary source for workspace previews (verified)

This is what the workspace **Previews** page reads from, and it is a better
source than the docs feed.

- `GET /api/2.1/settings-metadata` (paginated: `page_token`, 200 items per page
  by default, `page_size` up to 1000, so one call is enough today).
  CLI: `databricks api get /api/2.1/settings-metadata`.
  **Call it through `w.api_client.do(...)`, not the typed SDK method:** the
  backend's SDK (0.100.0) `SettingsMetadata` class has no `preview_phase`
  field and silently drops it (found in Phase 0).
- Each item has: `name`, `display_name`, `description`, **`preview_phase`**,
  `type` (a JSON-encoded value schema) and `docs_link` (rarely filled in: 18 of 256).
- On the test workspace, 256 settings came back: **120 `BETA`, 35
  `PUBLIC_PREVIEW`, 1 `PRIVATE_PREVIEW`, 82 `GA`, and 18 with no phase** (plain
  admin settings such as `disable_legacy_dbfs`). 245 of them are boolean.
- Because it reports `preview_phase`, we can also detect a feature moving from
  Beta to Public Preview to GA. **GA features drop off the list** (decided).
  The tracker keeps `BETA`, `PUBLIC_PREVIEW` and any `PRIVATE_PREVIEW` it can
  see (usually few: only previews the account is enrolled in are listed).
- **The list differs per workspace** (release ring, tier and region), so
  discovery has to combine results from every target workspace and record
  which workspaces each feature is available in.
- Permission: **any workspace user** (verified as a non-admin that only belongs
  to `users` and `account users`; the same identity was refused on the
  admin-only IP access lists call). Discovery doesn't need workspace admin.

### 2. Settings v2 value API — verifying workspace previews (verified)

- `GET /api/2.1/settings/{name}` returns an `effective_*` value, for example
  `{"name":"aha_connector","effective_boolean_val":{"value":false}}`.
  The SDK docstring calls `effective_*` *"the final effective value of setting"*,
  which means any inherited default has already been applied.
  SDK: `workspace_settings_v2.get_public_workspace_setting(name)`.
  Permission: **any workspace user** (verified, same non-admin identity).
- The response also includes the **stored** value (`boolean_val`) **only when
  the setting was set on the workspace itself.** If only `effective_*` is
  present, the value is inherited (a default or an account-level choice). On
  the test workspace, 24 previews were set on the workspace and 32 more were on
  by inheritance. The tab can show this as "On (set here)" versus "On
  (inherited)", and the auto-enable step must PATCH even when the effective
  value is already on but inherited, if the request asks to pin it on the
  workspace. Default: don't PATCH when it's already effectively on.
- **The list changes over time, and a listed setting can fail.** Previews that
  launch can drop out of the list, and a listed setting's GET or PATCH can
  occasionally fail (such as `setting … does not exist`). Treat a missing
  feature as GA or retired, and treat a GET or PATCH failure as "not manageable
  through the API": fall back to the manual task, never fail the request.
- `PATCH /api/2.1/settings/{name}`
  (`workspace_settings_v2.patch_public_workspace_setting`) turns a workspace
  preview on or off. **Verified as a workspace admin** in a sandbox (Phase 0,
  spike b). The body is `{"name": <name>, <field>: …}`, where the field comes
  from the metadata `type` (for example `"boolean_val":{"value":true}`). The
  response returns the new stored and effective value. Use by a non-admin is
  expected to be refused (the Manage previews docs say workspace admins manage
  these). That wasn't tested. This is the auto-implement step.
- Legacy `/api/2.0/workspace-conf` is not needed.

### 3. Account-level previews — the gap

- `GET /api/2.1/accounts/{account_id}/settings-metadata` and `.../settings/{name}`
  exist (`AccountSettingsV2API`) but almost certainly **require account admin**
  (they're account API endpoints, and the docs say account admins manage account
  previews; not tested). Our SP can't call them.
- **Account previews that affect workspaces can be verified from the
  workspace (Phase 0 result).** The public
  [Settings API docs](https://docs.databricks.com/aws/en/admin/workspace-settings/settings-api-manage)
  say the `effective_*` value is "the value the server computes after applying
  defaults and any higher-scope overrides", meaning account-level choices
  count. In practice, `abac_on_views`, which the feed says account admins
  enable from the account console, appears in the workspace metadata with an
  inherited value. Previews that only change the account console itself (for
  example Governance Hub) are **not** in the workspace list, so they need the
  probe or attestation fallbacks. The final end-to-end check (turn an account
  preview on and see the workspace value change) needs an account admin, so
  it's done at the first real account-preview request.
- Fallbacks, from best to worst:
  1. **Effective-value check:** for account previews that appear in the
     workspace list.
  2. **Capability probe:** an optional read-only check stored per feature (a
     REST GET or a SQL statement whose success means the feature is on). It runs
     through the ToolExecutor as a `read` tool.
  3. **Attestation:** whoever completes the Implement task confirms it and
     attaches evidence (a link or note). The UI labels this "Implemented
     (attested)", as opposed to "Implemented ✓ verified".
- No account-level credential will be available (decided), so the account
  endpoints above are out of scope.

### 4. Docs feed `https://docs.databricks.com/aws/en/feed.xml` — source for account previews and context (verified)

- RSS 2.0, **1,279 items from 2025-01-15 to 2026-10-05**. It is product release
  notes, not a list of doc changes. Each item has `title`, `description` (HTML),
  `link`/`guid`, `pubDate` and several `category` tags (`Product`, `Unity
  Catalog`, `Lakeflow Connect`, `whatscoming`, …).
- 493 items mention Preview or Beta, but only as free text in the title or
  description. The phase is not a structured field.
- Account versus workspace scope is also only free text, for example "account
  admins can enable … from the account console **Previews** page". Classify with
  a rule-based pass first, then fall back to the agent, and let an admin
  override the result.
- Uses: (a) find **account-level** previews that the workspace API doesn't list,
  (b) attach the announcement, date and docs link to features found through the
  API (match on `display_name`), (c) backfill history on the first sync.

### 5. Ruled out

- Release-notes HTML pages: the same content as the feed, without structure.
- "What's coming" page: returns 404. The feed's `whatscoming` category covers it.
- System tables: nothing exposes preview state.
- Terraform `databricks_workspace_setting_v2`: wraps the same Settings v2 API.
  It could be useful later for a GitOps implement path, but it isn't a discovery source.

---

## How this fits the existing platform

| Need | Existing pattern to reuse |
|---|---|
| Scheduled sync | Cron check in the poller loop, like `process_enforcement_sentinel_cron` (`backend/app/workers/poller.py:58`) or the semaphore-guarded `_spawn_background` contract sync (`poller.py:254`). The schedule is a `type: "cron"` field in `settings_store.EDITABLE_FIELDS`, like `DATA_ASSET_SYNC_CRON` (`settings_store.py:208`). |
| Starting a run when a feature is requested | Insert a `RequestModel` row with `state_context` and let the poller pick it up, the same way `data_contracts.py:1139` starts an OmniGuard policy check. |
| Workflow as data | A new bundled spec `backend/app/workflows/graphs/catalog/preview_feature_request.json`, seeded by `seed_specs_from_catalog` and editable in Admin → Workflows. |
| Approve, then Implement | A gate with `approver_source` (`spec.py:61-71`), then a `manual_task` gate with `instructions` and `due_in_days` (`spec.py:191`). Both appear in the existing approvals inbox. |
| Approver context | The assess step returns `report_markdown`, which becomes a `step_report` shown on every later card. Build it with `safe_text`/`safe_code` because feature descriptions come from outside. |
| Per-workspace credentials | `get_target_workspaces()` and `_resolve_credentials` (`backend/app/core/workspaces.py:166`). The workspace multiselect lists these workspaces. |
| Tools | `@tool` with `side_effect_class="read"` for discovery and verification, and `app_write` for status writes, all through the ToolExecutor. |
| Feature flag and tab | `features.preview_features` plus `ui.tabs.preview_features` in `CAPABILITIES` (`settings_store.py`), and a route under `/admin/:tab` with a Sidebar entry. |
| Database | New SQLAlchemy models in `backend/app/db/`. `create_all` creates the tables, so `migrate.py` is only needed for later column changes. |

---

## Design

### Data model

**`preview_features`**: one row per feature, combined across sources.
`id`, `setting_name` (null for feed-only account previews), `display_name`,
`description`, `phase` (`BETA|PUBLIC_PREVIEW|PRIVATE_PREVIEW`),
`archived_at` / `archived_reason` (`ga|retired`; archived rows are hidden from
the tab but kept for audit and request history),
`scope` (`workspace|account|unknown`), `scope_source` (`api|inferred|admin`),
`announcement_text` (the feed item's description, kept as untrusted text),
`docs_link`, `docs_link_source` (`api|feed|sitemap|admin`),
`announcement_url`, `announced_at`, `first_seen_at`,
`last_seen_at`, `phase_changed_at`, `value_type`, `probe` (JSON, optional),
`raw` (JSON).

**`preview_feature_targets`**: one row per feature × target (a workspace name,
or `__account__` for account scope).
`feature_id`, `target`, `available` (listed in that workspace's metadata),
`status` (`not_requested|requested|approved|implemented|rejected`),
`action` (`enable|disable`, the direction of the in-flight request), `request_id`,
`requested_by`, `approved_by`, `implemented_by`, `observed_value` (JSON),
`verification` (`api|probe|attested|none`), `last_verified_at`, `drift`
(bool: it was implemented and is now off).
Each workspace row has its own status, because which workspaces want a feature
varies. A single request still covers the whole batch of selected workspaces.
Status history comes from the request's facts and events, so no separate
history table is needed.

### Sync job (`PREVIEW_FEATURE_SYNC_CRON`, default daily)

1. For each target workspace, page through `settings-metadata`, upsert
   features, and mark `available` for that workspace.
2. For each preview × workspace, `GET settings/{name}` to store `observed_value`.
   This shows features that are **already enabled outside this process**, and
   flags drift on rows marked `implemented`. Calls are rate-limited with the
   same concurrency knobs Sentinel uses.
3. Fetch the feed, extract Preview/Beta items, classify scope, link them to
   features from step 1 by name, and create feed-only `account` features for
   the rest.
4. Advance lifecycle state. **GA features drop off the list:** a feature whose
   metadata `preview_phase` is now `GA`, or whose matching feed item says it is
   generally available (the only GA signal for feed-only account previews), is
   archived with reason `ga`. Any in-flight request for it is closed with a
   "now generally available" note so it doesn't wait forever. A feature missing
   from every workspace for N syncs is archived with reason `retired`.
5. Re-verify every `approved` row against the value its request wants (on for
   enable, off for disable). When verification passes, the open request's
   verify step can finish (see below). Also re-check `implemented` rows for
   drift; a row with a disable in flight doesn't count as drift.

### Workflow: `preview_feature_request`

The tab creates **one request per Request action**, covering every selected
workspace (a batch). Its `state_context` holds
`{feature_id, setting_name, scope, value_type, action, targets:[…], justification}`,
where `action` is `enable` or `disable`. **Turning a feature off uses the same
workflow** with `action: disable`, so it gets the same approval, automatic
change, manual fallback and verification. Each target row's status still moves
on its own, because one workspace's automatic change can succeed while
another's fails.

1. **step `assess_preview_feature`** (read): summarizes the feature, its phase,
   docs and announcement, and which selected workspaces don't list it or
   already have it on. Returns `report_markdown` for the approval card. All
   targets become `requested`.
2. **gate `approve`**: **one approval for the whole batch.** Approvers are set
   in the Workflows editor (the Approver setting on the gate: *Default*,
   *Specific group* or *approver_group tag*). No new settings are needed. When
   approved, every target becomes `approved`. Rejected means every target
   becomes `rejected` (re-requestable) and the requester is notified (on_reject).
3. **step `set_preview_setting`** (`side_effect_class="infra"`, mutating),
   with `for_each` over targets and `run_if: scope == "workspace"`. It PATCHes
   `settings/{name}` on each workspace using that workspace's SP, with a value
   (on for enable, off for disable) built from the metadata `type`. It is
   idempotent (it reads the effective value first and skips if it's already
   at the wanted value) and the OPA check runs first as for any mutating tool.
   A failure on one workspace doesn't stop the others; the failed list goes
   into context.
4. **gate `implement`** (`manual_task`): this gate is skipped (`auto_approve`)
   when the scope is workspace and every PATCH succeeded. Otherwise it holds for
   a person. That covers **account scope always**, and any workspaces whose
   PATCH failed. Instructions name exactly what's left (for example "Account
   admin → account console → Previews → turn on *X*", or "turn off" for a
   disable). The assignee group and `due_in_days` are set in the Workflows editor.
5. **step `verify_preview_setting`** (read): checks the effective value, then
   the probe, then attestation, against the wanted value. For an enable,
   targets that pass become `implemented` with the verification method
   recorded (`api`, `probe` or `attested`). For a disable, they go back to
   `not_requested`, and the request history keeps the record. For any that
   fail, the request stays open in "awaiting verification" and the daily sync
   keeps re-checking. After N failures it alerts the implementer.

A small `app_write` tool `set_preview_target_status` makes each status change,
so every change is an audited fact. All of this is data, so an admin can drop
the auto-enable step (making everything manual), change approvers or reorder
steps in the Workflows editor without code changes.

**SP permissions:** discovery and verification only need a workspace user. The
auto-enable step needs the workspace SP to be **workspace admin**, which ours
are. If an SP isn't admin, the PATCH fails, and step 4 falls back to the manual
task for that workspace.

### UI: Admin → **Preview Features**

- Two sub-tabs: **Workspace previews** and **Account previews**, with counts and
  a "New since last visit" badge.
- Filters: phase (Beta, Public Preview, Private Preview), status, workspace,
  category, search. GA features aren't shown.
- Workspace view: a table with one row per feature and a status chip per
  workspace (*Not requested · Requested · Approved · Implemented ✓ verified ·
  Implemented (attested) · Already on · Drift ⚠ · Not available*). A disable in
  flight shows as *Disable requested* or *Disable approved*. The row expands to
  show the description, docs and announcement links, and each workspace's
  request timeline.
- **Request** opens a dialog with a multiselect of target workspaces (workspaces
  where the feature isn't available are disabled, with a tooltip) and a
  justification field. Submitting creates the request.
- **Request disable** opens the same dialog, limited to workspaces where the
  feature is implemented or already on.
- **Every entry shows a description and its documentation when we can find it.**
  The collapsed row shows the description's first line, and the expanded row
  shows the full description, the announcement text, a **Docs** link and a
  **Release note** link. Where each comes from, measured on the test workspace
  (156 previews):
  - **Description:** the metadata `description`, present for **156 of 156**
    (short: median about 100 characters). For feed-only account previews, the
    feed item's description is used. Where both exist, the feed text is shown as
    "Announcement" under the shorter metadata text.
  - **Docs link,** first match wins: (1) metadata `docs_link` (in practice it's
    filled in for **none** of the previews, though it is for some ordinary
    settings); (2) the feature doc that the matching feed item links to (feed
    items name every word of the preview's display name for **61 of 156**
    previews, and 463 of 493 preview/beta feed items link to a feature doc page);
    (3) a title match against the docs sitemap
    (`https://docs.databricks.com/aws/en/sitemap.xml`, about 6,000 pages),
    labeled "suggested"; (4) an admin-entered link, which always wins once set.
    With no match, the row shows "No documentation found" and a docs search
    link for the display name.
  - **Release note link:** the matching feed item's link (`announcement_url`).
  - Link resolution runs in the daily sync and only retries for features that
    don't have a link yet. All text from Databricks is treated as untrusted:
    plain text in the UI, and `safe_text` in any approval report.
- Approve and Implement happen in the existing approvals inbox. The tab links
  to the open card rather than duplicating it.
- Admin → Settings: the sync cron and the feature flag. Approvers and
  implementers live on the workflow (Admin → Workflows), not here.

### Release note

Minor version bump with an "Added: Preview features tracker" entry, and update
`package.json` to match.

---

## Phases

- **Phase 0 — spikes:**
  - (a) Account previews visible from the workspace: **done.** Account previews
    that affect workspaces show through the workspace `effective_*` value
    (public docs and the `abac_on_views` example). Account-console-only
    previews don't appear and need probe or attestation.
  - (b) PATCH in a sandbox workspace: **done** (2026-10-01, FEVM sandbox, as a
    member of `admins`). `PATCH /api/2.1/settings/aha_connector` with body
    `{"name":"aha_connector","boolean_val":{"value":true}}` returned the new
    setting immediately (`boolean_val` and `effective_boolean_val` both true),
    and a fresh GET agreed. Turning it back off worked the same way. Findings:
    - The PATCH response already contains the new effective value, so
      `set_preview_setting` can record it directly. The verify step still
      re-reads it.
    - **There's no "reset to inherited":** the API has no delete. After a
      disable, the setting stays explicitly off on the workspace. A
      workspace-level off may also override an account-level on, which is
      the intent of a disable but worth stating on the approval card.
    - The sandbox lists 287 settings, against 256 on the other test workspace,
      which confirms the list differs per workspace.
    - Not tested: a PATCH by a non-admin (expected to be refused). It wasn't
      worth changing a shared workspace to prove it. The manual fallback
      covers it either way.
  - (c) Sync cost: **done.** One workspace takes about 0.6 s for the metadata (one
    page, 256 settings) plus about 10.7 s for 156 preview GETs at concurrency 8,
    with 0 errors. That's about 11 s per workspace, so a daily sync is cheap.
- **Phase 1 — discovery and read-only tab:** models, sync job (API and feed),
  description and docs-link resolution, account/workspace tabs, already-on
  detection. No workflow yet. Useful on its own.
- **Phase 2 — request workflow:** spec, tools (including
  `set_preview_setting`), request and request-disable dialogs with
  multiselect, batch approval, automatic change with manual fallback, status writes.
- **Phase 3 — verification:** verify step, re-verification on the sync,
  drift detection, GA archiving and closing in-flight requests, probes and
  attestation for account scope.

Tests: parsers (metadata pages, feed), scope classifier, status transitions,
and a spec eval-harness case for the workflow.

---

## Decisions

Settled 2026-10-01:

1. **Variation is per workspace:** which workspaces want a feature differs, so
   status is tracked per workspace and the user picks workspaces in a multiselect.
2. **Final status is `implemented`**, with the verification method (`api`,
   `probe` or `attested`) recorded alongside it, plus `rejected`.
3. **One batch approval** covers every workspace in a request.
4. **Auto-enable:** after approval, the SP turns workspace previews on itself.
   People handle account previews and any workspace whose automatic enable failed.
5. **Approvers and implementers are set in the Workflows editor** (one group per
   gate). Per-workspace approvers would need a new editor option, which isn't
   needed for a batch approval.
6. **The list covers Beta, Public Preview and any visible Private Preview.**
   Most private previews won't be visible. GA features drop off the list; their
   records are archived, not deleted, and any in-flight request is closed.
7. **No account-level credential.** Account previews are verified through the
   workspace effective value (if the Phase 0 spike confirms it), a probe, or
   attestation.
8. **Disable is supported** through the same workflow with `action: disable`.

## As built

Code: `backend/app/services/preview_features/` (sync, feed, docs, settings API,
status, request flow, workflow steps), `backend/app/workflows/tools/preview_features.py`,
`backend/app/workflows/graphs/catalog/preview_feature_request.json`,
`backend/app/api/v1/preview_features.py`, `src/pages/admin/PreviewFeatures.tsx`
(route `/build/preview-features`). Tests: `backend/tests/unit/services/test_preview_features.py`.

Where it differs from the design above:

- **`set_preview_setting` is one step that loops over the targets itself,**
  not a `for_each` step. A `for_each` item failure halts the graph, and
  `writes_context` only lifts keys from a single result. The step returns
  `needs_manual`, `applied_targets` and `manual_targets`, plus a report listing
  what's left for the Implement task.
- **The Implement gate's skip rule** is `scope == "workspace" and needs_manual == false`.
  If an admin removes the apply step, `needs_manual` is missing and the gate
  holds for a person instead of skipping.
- **Unverified targets don't hold the request open.** The verify step marks
  what it can verify. Targets it can't verify stay `approved` with a note, the
  request completes, and the daily sync promotes them to `implemented` when the
  value shows up. There is no alert after N failures yet (`verify_failures` is
  counted).
- **Attestation:** when no workspace can read the value (feed-only account
  previews), completing the Implement task counts as the confirmation
  (`verification = attested`, `implemented_by` = the completer). A readable
  value that disagrees is never overridden by attestation.
- **Account previews found in the feed that also appear in workspace metadata**
  (for example `abac_on_views`) are scoped `account` and verified from the
  workspaces that list them ("on in N of M workspaces").
- **Closing requests for GA/retired features** marks the request completed,
  cancels its pending approvals, and resets the targets to `not_requested` with a note.
- **The approve gate defaults to `platform_admin`.** Change it in Workflow Studio.
- **`verify_preview_setting` is `app_write`,** since it records statuses.
- **Chat agent path.** The agent reads `backend/app/agents/instructions/preview_feature_request.md`
  (seeded into the workflow row like the other bundled instructions) and uses
  the `find_preview_features` tool to explain a feature and its per-workspace
  status before anyone requests it. It starts the workflow with
  `execute_workflow` (`feature`, `action`, `targets`, `justification`), and
  the first step links that request to the feature and targets with the same
  checks the tab uses (`request_flow.link_request`).
- **Docs links** prefer a docs page named after the setting (for example
  `functions/ai_enrich`, source `docs`) over the release note's link.
- Not built: the category filter, "pin it on even if inherited" (already-on
  rows can't be requested), and a persisted sync history (the last run is kept
  in memory; after a restart the tab shows the newest `last_seen_at`).

## Open decisions

None blocking. Phase 0's spike results may reopen the account-verification
approach.
