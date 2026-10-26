# What Unity Catalog information a regular user can see

A "regular user" is anyone in the workspace `users` group with no app admin role.

## In short

- **Metadata of the scanned catalogs is shared with every user.** A service principal
  holds `BROWSE` only on the catalogs the app scans (`SCAN_CATALOGS`), not on the rest
  of the metastore. The app shows those catalogs' metadata to every user, whatever
  that user's own grants. Catalogs the service principal can't browse never reach the
  app.
- **Row data is never shared.** Previews, metric view values, Genie and SQL always run
  as the user, with the user's own `SELECT`.
- **View SQL is never read by a service principal.**

## The three identities

| Identity | Privileges | What it powers |
|---|---|---|
| **User (on-behalf-of)** | The user's own grants | Table details, lineage tab, data preview, metric view definitions and values, Genie, SQL, the assistant's catalog/schema/table listings |
| **Governance SP** | `BROWSE` only on the scanned catalogs; `SELECT` only on the data-quality schema and `system.access.table_lineage` | The Discover catalog, data contracts, certification, Metadata Manager |
| **App SP** | Workspace admin | Discover lists of dashboards, jobs, apps and Genie spaces; the assistant's `find_owner` tool |

The governance SP falls back to the App SP when `SENTINEL_DATA_CERT_WORKSPACE` is not
set. Grants are listed in [SERVICE_PRINCIPALS.md](./SERVICE_PRINCIPALS.md).

## What every user can see

| Object | Fields shown to every user | Where |
|---|---|---|
| Table, view, metric view | Name, type, description, owner (often a person's email), created date, **all table tags and their values** | Discover, assistant search |
| Columns of tables tagged `dataset` | Name, data type, description | Data contract on the Discover Schema tab |
| Catalogs, schemas, tables and columns the user owns (owner tag on the object or above it; UC owner if `METADATA_UC_OWNER_IS_AUTHORITY`) | Owner, description, tags and their values, timestamps. Other objects only to governance. | Metadata Manager page, assistant and MCP tools |
| Lineage of dataset tables | Upstream and downstream table names, limited to `SCAN_CATALOGS` | Data contract, Discover Lineage tab |
| Metric views | Dashboards that read them (name, link; the ID only when the SP can't list it) | Discover |
| Data products | Data-quality rule results: failed-row counts, scores, thresholds | Discover, certification report |
| Dashboards, jobs, apps, Genie spaces | Name, description; creator email for jobs and apps | Discover |
| Access rules (the app's record of grants, applied to Databricks by the Terraform pipeline) | Which groups and service principals hold which privileges on any catalog, schema, table or workspace object, in every environment; everything a named group holds | Assistant (`find_access`). The Access Rules page is limited to approver groups and viewer roles. |

Columns of tables that are *not* in a data contract are read as the user. A user
without access sees "You don't have access".

## What is never shared

- Table and view rows, row counts, sizes and storage locations.
- View SQL and metric view YAML. Measure expressions are shown only to users who can
  `DESCRIBE` the view.
- Live Unity Catalog grants. Access rules cover only groups and service principals.
  The assistant's live grant check (`check_resource_access`) runs as the user, so it
  shows only what the user's own `SHOW GRANTS` would.

## Filters on what is shared

- **`SCAN_CATALOGS`** limits the Discover catalog and data contracts.
  **`METADATA_RECONCILE_CATALOGS`** limits Metadata Manager. When blank, every catalog
  the SP can see is included, except `system` and `samples`.
- **`METADATA_BROWSE_ROLES`** (default Governance Admin), plus Platform Admins and
  `METADATA_EDITOR_ROLES`, see every Metadata Manager object. Other users see only the
  objects they own, on the page and through the assistant and MCP tools, and users who
  own nothing see nothing. Blank adds nobody. Sync runs are governance-only.
- **`DISCOVER_HIDDEN_TAG_KEYS`** hides tagged assets from Discover and assistant search.
  It is a tidiness control, not access control: it doesn't cover contracts, Metadata
  Manager or `find_owner`.

## Known gaps

Open:

| Gap | Severity |
|---|---|
| Anyone may submit a Metadata Manager change on any object. Current values outside their scope are hidden, but a guessed value can be confirmed (the change comes back as having nothing to do) | Low |
| A cached metric view definition can outlive a revoked grant by up to 10 minutes | Low |

Accepted by design:

- Data contracts show column names, types and descriptions of `dataset` tables to
  every user, so certified datasets are discoverable before access is requested.
- Table tag values and owners are shown to every user, so users know whom to ask.
- Any user can ask the assistant which groups hold access to a securable, so they
  can request the right group instead of a new grant.
- `find_owner` runs as the App SP, so users can find who owns a table they can't see
  yet. It only reaches catalogs the App SP itself can browse.
- Discover lists every dashboard, job, app and Genie space in the home workspace (names
  only); opening one still needs the user's own permission.

Fixed in 2.5.0:

- Saving data contracts and data product documents, drafting data products, syncing
  contracts and running policy checks are limited to Platform and Governance Admins.
- Metadata Manager is limited to governance and to owners' own objects, on the page and
  through the assistant and MCP tools (it used to be open to every user). A submitted change's preview, result, conflicts and change set
  no longer show current values of objects outside the submitter's scope.
- The assistant's `draft_odcs_contract` is Governance Admin only and refuses tables
  outside `SCAN_CATALOGS`. Contract lineage is filtered to `SCAN_CATALOGS`.
- The unused App SP endpoints `/data-assets/databricks/{catalogs,schemas,tables}` are
  removed.
- `check_resource_access` runs only as the user on deployed targets.

## Keeping this page true

Re-check it when a code path starts using the governance or App SP:

```bash
rg -n "get_governance_uc_provider\(|client_id=settings.DATABRICKS_CLIENT_ID" backend/app
```
