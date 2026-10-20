# Runbook: cutting over to the table-backed Metadata Manager

This runbook moves a deployment from the GitOps or live-edit Metadata Manager to the
desired-state model in [ADR-0006](../adr/0006-metadata-desired-state.md): Lakebase holds
every tag and description, and the reconciler applies them to Unity Catalog. Plan for
the plan-mode review in step 5 to take at least a few days.

Do it once, on the production deployment, which owns every environment. Dev and stage
deployments stay in `plan` mode (step 8).

## 1. Before you start

- **Grant the governance service principal** (the identity behind
  `get_governance_uc_provider()`; see `docs/SERVICE_PRINCIPALS.md`) on every catalog it
  will manage:
  - `USE CATALOG`, `USE SCHEMA` and `BROWSE`, so the sweep can read `information_schema`;
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
| **Catalogs to reconcile** | Blank to manage every governed catalog, or the catalogs to manage first. |
| **Instance id** | A name unique to this deployment, such as `prod`. |
| **Owner tag key** | The key whose value names the groups that own an object (default `data_owner`). |
| **Governance approver group** | The Databricks group that approves sensitive changes and objects with no owner. Left blank, only Platform Admins can approve them. |
| **Key policies** | One row per governed key: where it applies, allowed values or pattern, editor groups, and whether it's sensitive. These replace `tag_policy.yml` in the governance repository. |
| **Environments** | One row per environment with a catalog pattern such as `{base}_dev`, and whether every change or only sensitive ones need approval. |
| **Max tag removals per run** | Keep the default (200) until enforcement has run cleanly for a while. |

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
3. If a run is **paused** by the circuit breaker, it wanted to remove more tags than
   **Max tag removals per run**. Saved changes are applied in batches of up to 200
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

Dev and stage deployments share the metastore, so they must not enforce production's
catalogs:

- keep **Reconcile mode** at `plan` there, which shows real diffs against the metastore
  without writing;
- to try enforcement end to end, list sandbox catalogs in **Catalogs to reconcile** and
  give the deployment its own **Instance id**. Its claim tag on those catalogs keeps the
  production deployment away from them, and vice versa.

## Rolling back

- **Stop writing:** set **Reconcile mode** to `plan` or `off`. Desired state and history
  are kept.
- **Undo a change:** open it on the **Changes** tab and choose **Undo this change**, which
  records the inverse as a new change set; the reconciler applies it.
- **Undo enforcement on one object:** edit its desired state to match what Unity Catalog
  should hold; the reconciler converges on the next pass.
