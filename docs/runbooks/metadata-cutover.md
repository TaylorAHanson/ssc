# Runbook: cutting over to the table-backed Metadata Manager

This runbook moves a deployment from the GitOps or live-edit Metadata Manager to the
desired-state model in [ADR-0006](../adr/0006-metadata-desired-state.md): Lakebase holds
every tag and description, and the reconciler applies them to Unity Catalog. Plan for
the plan-mode review in step 5 to take at least a few days.

Do it once, on the production deployment, which owns every environment. Dev and stage
deployments either stay in `plan` mode or enforce only their own catalogs (step 8).

## 1. Before you start

- **Grant the governance service principal** (the identity behind
  `get_governance_uc_provider()`; see `docs/SERVICE_PRINCIPALS.md`) on every catalog it
  will manage, and only those. Give each deployment its own principal, so a dev
  deployment's principal can't write to production catalogs even if the app is
  misconfigured:
  - `BROWSE`, so the sweep can read the catalog from `system.information_schema`
    (enough on its own for plan mode);
  - `USE CATALOG` and `USE SCHEMA`, which Unity Catalog requires alongside the write
    privileges below;
  - `APPLY TAG`, to set and remove tags on catalogs, schemas, tables, views and columns;
  - ownership or `MANAGE`, to set descriptions (`COMMENT ON`);
  - `ASSIGN` on every governed tag it will apply;
  - `READ METADATA`, so previews can read the column masks and row filters that key on
    the changed tags. Without it, previews say the access policies weren't checked; turn
    off **Check access policies in previews** if you'd rather not grant it.
- **Stop the old apply path.** If the governance repository's CI applies tag migrations,
  disable that job. From the cutover on, nothing reads the repository; a migration merged
  afterwards would be reverted by the reconciler under enforcement.
- **Note open `tag_change` requests.** They stay readable in My Requests, but no longer
  apply anything. Re-submit any that still matter in the new Metadata Manager after
  step 3.

## 2. Configure (Admin → Settings → Metadata Manager)

| Setting | Set it to |
| --- | --- |
| **Reconcile mode** | `plan` (the default). Don't start in `enforce`. |
| **Catalogs to reconcile** | The catalogs this deployment manages: names or patterns such as `*_prd`, or `*` for every catalog. It changes, adopts in and accepts edits for these only. Blank means read and plan everything but change nothing, and enforce won't run. |
| **Instance id** | A name unique to this deployment, such as `prod`. |
| **Owner tag key** | The key whose value names the groups that own an object (default `data_owner`). |
| **Governance approver group** | The Databricks group that approves sensitive changes and objects with no owner. Left blank, only Platform Admins can approve them. |
| **Key policies** | One row per governed key: where it applies, allowed values or pattern, editor groups, and whether it's sensitive. These replace `tag_policy.yml` in the governance repository. |
| **Environments** | One row per environment with a catalog pattern such as `{base}_dev`, and whether every change or only sensitive ones need approval. |
| **Max removals per run** | Keep the default (200) until enforcement has run cleanly for a while. Removed tags and cleared descriptions both count. |

`features.metadata_v2` must be on (it is by default).

## 3. Read Unity Catalog and adopt its current state

1. Run a sweep without writing anything. On the **Reconciler runs** tab choose **Run a
   plan**, or call `POST /api/v1/metadata/runs` with `{"kind": "plan"}`. The sweep fills
   the snapshot (`metadata_observed`) from `information_schema`.
2. Adopt what Unity Catalog holds today as the desired state, with **Adopt current
   state** on the **Objects** tab or the API. A catalog or schema name adopts everything
   under it.

   ```http
   POST /api/v1/metadata/adopt
   {"catalogs": ["sales_prd", "finance_prd"], "include_columns": true,
    "reason": "Cutover: adopt current Unity Catalog state"}
   ```

   Name every catalog to adopt; a request naming no catalog or object is rejected. Adopt
   writes one change set per 500 objects; each tag and description is
   recorded with source `adopt` and the system actor. Objects that already have desired state are skipped unless
   `"overwrite": true`.
3. Optional: import tags you want that Unity Catalog doesn't have yet (for example from
   the governance repository) with `POST /api/v1/metadata/import/preview`, then
   `POST /api/v1/metadata/import`. Rows are JSON or CSV with the header
   `name,column,comment,tags`, where `tags` is a JSON object.

## 4. Register system principals

For each pipeline or external system that will call the API or MCP, add a row on the
**System principals** tab: its application id, the name globs it may change (for
example `sales_*.bronze.*`), the keys and operations it may use, and its environments.
Have the system call `GET /api/v1/metadata/whoami` once: it should report
`recognized_as: service_principal`. If it reports `unregistered_service_principal`, the
message names the identifier to register.

## 5. Review in plan mode

Let the scheduled sweeps run for a few days.

- **Drift** lists every managed object whose Unity Catalog state differs from its
  desired state. Right after adopt it should be empty apart from changes made since.
- **Reconciler runs** shows each run's planned statements. Look in particular at
  removals: every tag a statement removes is one that someone added outside the
  Metadata Manager, and enforcement will remove it.
- For each unexpected difference, either add the tag to the desired state (edit the
  object or adopt it again with `overwrite`) or accept that enforcement will remove it.
- Add keys that another system legitimately owns to **Exempt tag keys**.
  `system.certification_status` is already exempt; OmniGuard owns it.

## 6. Switch to enforce

1. Set **Reconcile mode** to `enforce`.
2. On **Reconciler runs**, choose **Run a full check now** and watch the run. It should
   apply only the statements you reviewed. Changes saved while the mode was `plan` were
   only planned, so this run is what applies them.
3. If a run is **paused** by the circuit breaker, it wanted to remove more tags or
   descriptions than **Max removals per run**. Saved changes are applied in batches of up to 200
   objects and the limit counts per batch, while a full check counts the whole run. Open
   the run, check the removals, then either fix the desired state or choose **Apply
   anyway and resume** (`"override_circuit_breaker": true`).

## 7. Retire the old configuration

The old Tag Management screen and its `/api/v1/tags` API are removed; the Metadata
Manager page is now at the same address. The `GOVERNANCE_TAGS_*` settings are retired and
ignored; any stored values stay in the settings table but have no effect. `governance_tags_base_branch` is gone from `databricks.yml`. The
`tag_change` workflow is set to draft at startup, so it no longer appears in the
assistant or the request catalog. The GitHub provider and the `pr_merge` gate are kept
because other workflows use them.

## 8. Lower environments

Dev and stage deployments share the metastore with production, so they must never write
to production's catalogs. **Catalogs to reconcile** is the boundary: a deployment never
changes, claims or adopts tables in a catalog it doesn't list, and refuses edits to one.

- **Report only:** leave **Catalogs to reconcile** blank and **Reconcile mode** at `plan`.
  It shows real diffs against the metastore and writes nothing; enforce won't run
  without a list.
- **Enforce its own catalogs:** list only that environment's catalogs (for example
  `*_dev`), give the deployment its own **Instance id**, and set **Reconcile mode** to
  `enforce`. Its claim tag on those catalogs keeps the production deployment away from
  them, so leave them out of production's list too.
- **Add, don't remove, while you build trust:** set **Max removals per run** to `0`.
  Every run that would remove a tag or clear a description pauses for review on the
  **Reconciler runs** tab, so the deployment only adds unless you choose **Apply anyway
  and resume**.
- **Least privilege:** grant the dev deployment's service principal `APPLY TAG` and
  `MANAGE` on its own catalogs only. That holds even if a setting is wrong.

## Rolling back

- **Stop writing:** set **Reconcile mode** to `plan` or `off`. Desired state and history
  are kept.
- **Undo a change:** open it on the **Changes** tab and choose **Undo this change**, which
  records the inverse as a new change set; the reconciler applies it.
- **Undo enforcement on one object:** edit its desired state to match what Unity Catalog
  should hold; the reconciler converges on the next pass.
