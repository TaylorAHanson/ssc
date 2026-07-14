# Service Principals & Grants

**Who this is for:** Platform Admins and Databricks account admins who provision the identities this app runs as and grant them access. It lists, explicitly, every privilege each service principal (SP) needs and why.

**The short version:** there are two kinds of SP.

1. **The App SP** — the single identity the application itself runs as (its home workspace). It powers everything the app does day to day.
2. **The Sentinel / Target-Workspace SPs** — one identity per *target* workspace the Enforcement Sentinel scans and enforces against.

A target workspace that has no dedicated SP configured falls back to the **App SP** — which only works if the App SP is actually valid in that workspace's account, so cross-account targets always need their own SP.

---

## 1. The App SP

**Identity:** `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`, against `DATABRICKS_HOST` (the app's home workspace). Configured in `databricks.yml` (secrets) and Admin → Settings.

**What it does:**

- **Runs the backend** in its home workspace (the Databricks App identity).
- **Reads secrets** from Databricks secret scopes — most importantly the target-workspace SP scope (`TARGET_WORKSPACE_SP_SECRET_SCOPE`), plus any email (SES) / GitHub scopes. This is how it hands the right credentials to each target workspace.
- **Runs SQL on the warehouse** (`DATABRICKS_WAREHOUSE_ID`) for **read-only discovery**:
  - the **data-asset cache sync** (reads `system.information_schema`),
  - **data-contract discovery** (reads each catalog's `information_schema.table_tags` to find `dataset`-tagged tables).
- **Calls the model-serving endpoint** (`MODEL_SERVING_AGENT_LLM_ENDPOINT`) for the assistant and ODCS contract drafting.
- **Reads/writes app state** in its Lakebase (Postgres) database.
- **Is the Sentinel's fallback identity** for the home workspace and for any target workspace with no dedicated SP. Certification (tag writes, DQ-history reads, RBAC grants reads) actually runs as the **data-certification workspace SP** (§2); the App SP only performs it when `SENTINEL_DATA_CERT_WORKSPACE` is blank and it falls back to itself.

### Grants for the App SP

Secret + compute:

```sql
-- Read the scope that stores the target-workspace SP credentials
GRANT READ ON SECRET SCOPE <TARGET_WORKSPACE_SP_SECRET_SCOPE> TO `<app-sp>`;
-- (plus READ on any SES / GitHub secret scopes you use)

-- Run all of the app's SQL
GRANT CAN USE ON WAREHOUSE <DATABRICKS_WAREHOUSE_ID> TO `<app-sp>`;

-- Serve the assistant / draft ODCS
GRANT CAN QUERY ON SERVING ENDPOINT <MODEL_SERVING_AGENT_LLM_ENDPOINT> TO `<app-sp>`;
```

Unity Catalog (per governed catalog in the home metastore) — the App SP only needs **`BROWSE`** for its read-only discovery:

```sql
-- Surfaces dataset-tagged tables + their tags in information_schema
GRANT BROWSE ON CATALOG <catalog> TO `<app-sp>`;
```

The certification-write grants (`USE CATALOG`, `USE SCHEMA`, `APPLY TAG`, `SELECT` on the DQ schema, and `MANAGE` for the RBAC check) belong to the **data-certification SP** (§2). Grant them to the App SP **only** if you have no dedicated certification-workspace SP (`SENTINEL_DATA_CERT_WORKSPACE` blank), so certification falls back to the App SP:

```sql
-- ONLY if the App SP is also the certification identity (no dedicated cert SP):
-- GRANT USE CATALOG, USE SCHEMA, APPLY TAG ON CATALOG <catalog> TO `<app-sp>`;
-- GRANT USE CATALOG, USE SCHEMA, SELECT ON SCHEMA <DATA_QUALITY_ADOC_SCHEMA> TO `<app-sp>`;
-- GRANT MANAGE ON CATALOG <catalog> TO `<app-sp>`;   -- only if the RBAC check is enabled
```

Workspace + database:

- **Workspace admin** on the home workspace (it is the app host and may enforce there).
- Ownership / connect on its **Lakebase** Postgres database (created with the app).

> **Not required:** `SELECT` on the governed tables/views themselves. `BROWSE` covers discovery; `APPLY TAG` (on the certification identity) covers certification.

---

## 2. The Sentinel / Target-Workspace SPs

**Identity:** one SP per target workspace. All of them are stored in the **single** install-wide secret scope (`TARGET_WORKSPACE_SP_SECRET_SCOPE`); each target workspace names its `client_id_key` / `client_secret_key` in that scope (Admin → Settings → Target Workspaces). Workspaces that share an SP just reference the same key names.

**What they do:** used **only** by the Enforcement Sentinel, which builds a client per target host and:

- **Scans workspace-scoped resources** in that workspace and remediates them per policy (warn / kill / disable): jobs, clusters/compute, apps, SQL warehouses, notebooks, dashboards, volumes, Genie spaces, service principals, Lakebase.
- **For the designated data-certification workspace only** (`SENTINEL_DATA_CERT_WORKSPACE` — data certification is metastore/Unity-Catalog scoped, so it runs once, not per workspace): discovers each data product's tables, reads their tags + DQ history, and applies/removes `system.certification_status`.

### Grants for each Sentinel SP (in ITS target workspace)

Every target SP:

```sql
-- List and act on workspace-scoped resources (jobs, clusters, apps, warehouses, …)
--   Grant workspace admin on the target workspace (Account console → workspace
--   → Permissions, or SCIM), and ensure the SP is assigned to that workspace.

-- Run any SQL the scan needs
GRANT CAN USE ON WAREHOUSE <warehouse-in-that-workspace> TO `<target-sp>`;
```

Additionally, the SP for the **data-certification workspace** (`SENTINEL_DATA_CERT_WORKSPACE`) needs the Unity Catalog grants, since it does the tag + DQ work:

```sql
GRANT BROWSE, USE CATALOG, APPLY TAG ON CATALOG <catalog> TO `<cert-sp>`;
GRANT USE SCHEMA ON CATALOG <catalog> TO `<cert-sp>`;       -- or per-schema
GRANT USE CATALOG, USE SCHEMA, SELECT ON SCHEMA <DATA_QUALITY_ADOC_SCHEMA> TO `<cert-sp>`;
-- Only if the RBAC / access-controls check is enabled (SHOW GRANTS needs MANAGE,
-- ownership, or workspace admin — the cert SP is already workspace admin, so this
-- is usually covered; MANAGE at catalog level is the least-privilege alternative):
-- GRANT MANAGE ON CATALOG <catalog> TO `<cert-sp>`;
```

> The App SP must have **READ** on `TARGET_WORKSPACE_SP_SECRET_SCOPE` or it can never load these SPs (it then silently falls back to itself and fails auth against the target — see the warnings in the run logs).

---

## 3. Quick-reference grant matrix

| Privilege | Granted on | App SP | Sentinel SP | Why |
|---|---|:--:|:--:|---|
| `BROWSE` | catalog | ✓ | ✓† | Surfaces tables + tags in `information_schema` for discovery |
| `USE CATALOG` | catalog | ‡ | ✓† | Required for tag writes (certification) |
| `USE SCHEMA` | schema/catalog | ‡ | ✓† | Required for tag writes (certification) |
| `APPLY TAG` | catalog/schema/table | ‡ | ✓† | Authorizes `ALTER TABLE/VIEW … SET TAGS` (certification) |
| `SELECT` | `DATA_QUALITY_ADOC_SCHEMA` | ‡ | ✓† | Read ADOC data-quality history |
| `MANAGE` (RBAC check only) | catalog | ‡* | ✓*† | Read grants (`SHOW GRANTS`) for the access-controls check; workspace admin also satisfies this |
| `CAN USE` | SQL warehouse | ✓ | ✓ | Runs each SP's SQL (discovery for the App SP; tagging/DQ for the cert SP) |
| `CAN QUERY` | model-serving endpoint | ✓ | — | Assistant + ODCS drafting (app only) |
| `READ` | target-SP secret scope | ✓ | — | App loads each target SP's credentials |
| Workspace admin | target workspace | ✓ (home) | ✓ | Scan/remediate jobs, clusters, apps, warehouses, … |

`✓*` = conditional (only when the RBAC / access-controls certification check is enabled).
`✓†` = only the Sentinel SP for the data-certification workspace (`SENTINEL_DATA_CERT_WORKSPACE`) needs the Unity Catalog grants; SPs that only scan workspace resources don't.
`‡` = the App SP needs these **only** when it is also the certification identity — i.e. no dedicated certification-workspace SP is configured (`SENTINEL_DATA_CERT_WORKSPACE` blank), so certification falls back to the App SP. In the recommended setup they live on the cert SP instead.

**Never needed:** `SELECT` (or ownership) on the governed data tables/views. Discovery is covered by `BROWSE`; certification is covered by `APPLY TAG`.

---

## 4. Notes & gotchas

- **`APPLY TAG`, not "ALTER TABLE".** `ALTER TABLE/VIEW … SET TAGS` is a SQL statement; the *privilege* that authorizes it is `APPLY TAG` (plus `USE CATALOG` + `USE SCHEMA`). You do **not** need `MODIFY` or ownership just to set the certification tag.
- **Views need the tag too.** The certifier issues `ALTER VIEW … SET TAGS` for views (falling back from `ALTER TABLE`), so `APPLY TAG` must cover views as well as tables — a catalog- or schema-level grant covers both.
- **`system.certification_status` is a Databricks-managed tag.** It's a built-in system tag, not a customer-defined governed tag, so `APPLY TAG` (+ `USE CATALOG`/`USE SCHEMA`) is all that's needed to set it — no `ASSIGN` grant required.
- **`information_schema` is metadata-filtered.** A table appears there only if the SP can see it — and `BROWSE` on the catalog is sufficient for that (no `SELECT`/`USE`/ownership on the table required). Row-/column-level security filters *rows*, not table visibility.
- **RBAC / access-controls check is NOT an account-API call.** It reads a table's grants via the Unity Catalog **Grants API** (`SHOW GRANTS`), which is metastore/workspace-scoped. Reading all grants requires `MANAGE`, ownership, or workspace admin — `BROWSE`/`APPLY TAG` are not enough. The `information_schema.table_privileges` view is **not** a reliable substitute: without ownership/metastore-admin it only returns the SP's *own* grants, so it can't tell whether other principals have access. When the SP can't read grants the check is **skipped** (logged), never failed, so a permission gap can't false-flag every table.
- **Fallback = failure across accounts.** If a target workspace has no `client_id_key`/`client_secret_key`, or the App SP can't read the scope, the app uses the App SP against the target host. That only works inside the App SP's own account; cross-account targets will fail with `invalid_client`. Always configure a dedicated SP per target.
- **Where these settings live:** `TARGET_WORKSPACE_SP_SECRET_SCOPE`, `DATABRICKS_WAREHOUSE_ID`, `DATA_QUALITY_ADOC_SCHEMA`, `SENTINEL_DATA_CERT_WORKSPACE`, and the target-workspace list are all in Admin → Settings (or `databricks.yml`).
