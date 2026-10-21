# Self-Service Hub - Platform Setup Guide

Welcome to the Self-Service Hub Platform Setup Guide! This document is intended for **Platform Administrators** or **IT Professionals** who are deploying Self-Service Hub into their organization's Databricks environment.

## Overview

Self-Service Hub (Agentic Control Tower for Lakehouse Automation & Self-Service Experience) is a full-stack web application that runs directly inside your Databricks workspace using **Databricks Apps**. 

To successfully deploy Self-Service Hub, you will need to complete three distinct phases:
1. **Infrastructure Preparation:** Running an automated Databricks Notebook to create the required Unity Catalog volumes and secret scopes.
2. **Configuration:** Customizing branding, feature flags, and catalogs via **Admin → Settings** (live, no redeploy for most settings) plus deploy-time variables in `databricks.yml`.
3. **CI/CD Deployment:** Configuring GitHub Actions to automatically securely deploy the application into your workspace.

Once deployed, the Databricks App will automatically provision its own Service Principal to securely interact with your data and infrastructure.

---

## 1. Prerequisites

Before beginning the installation, ensure you have:
- [ ] A **GitHub repository** containing the Self-Service Hub source code.
- [ ] A **Databricks workspace** with Unity Catalog enabled.
- [ ] **Account-level admin access** (or workspace admin access) to create a Service Principal for GitHub Actions.
- [ ] A **Databricks Serverless SQL Warehouse** (used by Self-Service Hub to execute backend queries).
- [ ] A **Model Serving Endpoint** running a Foundation Model (e.g., `databricks-gemini-2-5-flash`).

---

## 2. Infrastructure Preparation

Self-Service Hub requires a secret scope and a Unity Catalog Volume to store its configuration and GitOps data. We have provided a Databricks Notebook to automate this process.

### Step 2.1: Run the Installer Notebook
1. Locate the `installer_notebook.py` file in the root directory of your repository or zip file.
2. In your Databricks UI, click **Workspace**, navigate to your personal folder, and select **Import**.
3. Import the `installer_notebook.py` file.
4. Attach the notebook to a cluster.
5. Fill out the widget parameters at the top of the notebook:
   - **Secret Scope Name** (default: `app-secrets`)
   - **Catalog, Schema, and Volume Names** (where configuration will be stored)
   - **GitHub PAT** (Optional: requires `repo` scope if using direct GitOps integrations). For the exact API-call-to-permission mapping, fine-grained alternatives, and a request message for your GitHub org admin, see [GITHUB_TOKEN_PERMISSIONS.md](./GITHUB_TOKEN_PERMISSIONS.md).
6. Click **Run All**.

The notebook will automatically create the secret scope, Unity Catalog infrastructure, and (optionally) store your GitHub PAT in the secret scope.

---

## 3. Configuration & Branding

Self-Service Hub is highly customizable. Most day-to-day settings — branding, features and navigation, Community Links, embedded apps, target workspaces, and the system banner — are edited live under **Control Tower → Admin → Settings** and take effect immediately (persisted in the app database).

Code defaults for those settings live in `backend/app/core/default_config.py`. Deploy-time infrastructure and governance posture variables live in `databricks.yml` (see Step 3.2).

### Step 3.1: Customize the application (Admin → Settings)

1. Deploy the app (or run locally with `./dev.sh`).
2. Sign in as a **Platform Admin**.
3. Open **Control Tower → Admin → Settings**.
4. Settings are grouped like the sidebar. Start with **General → Appearance** (branding, colors, system banner) and **General → Features & Navigation** (turn capabilities on or off and choose which sidebar pages show), then adjust **Learn & Share → Links & Embedded Apps**, **Watch Tower → Target Workspaces**, **General → Notifications**, and the other pages as needed. Settings for a capability that is switched off are collapsed under **Inactive settings**.

*Note: Workflows are **data-driven** — the source of truth is published workflows in the database plus the seed catalog under `backend/app/workflows/graphs/catalog`. To disable a workflow, unpublish it in **Control Tower → Workflow Studio**, or set `execute_workflow: false` under the `tools` section in `default_config.py` (global kill switch — requires redeploy unless overridden via DB).*

### Step 3.2: Update databricks.yml
Open `databricks.yml` in the root of your repository and update the `variables` block to match your environment:
- `lakebase_host` (The hostname of your Databricks Lakebase database)
- `model_serving_endpoint` (the agent's LLM endpoint, e.g. `databricks-gpt-5-4-mini`)
- `gitops_volume_path` (The Unity Catalog volume path you created in Step 2.1)

**Governance & observability variables** (sensible defaults provided):
- `agent_tool_opa_enforce` (default `true`) — enforce the agent-tool OPA policy in this environment (deny + approval gates actually halt mutating tools). The app starts an embedded OPA server automatically. Leave `true` for any deployed env; it is `false`/shadow only for local dev.
- `workflow_authoring_locked` (default `false`; **`true` in the `prod` target**) — when `true`, in-place workflow (Workflow) authoring is disabled: no create/edit/publish/delete via the UI, API, or the agent. The only way to change workflows is an all-or-nothing **bundle import** (promotion). Build and prove workflows in lower envs, then promote the vetted bundle into prod. Reads, export, validate, and dry-run stay available.
- `ai_gateway_endpoint` (default empty) — if set, the agent's LLM calls route through this AI Gateway endpoint (model routing/A-B, rate + cost limits, and input guardrails are configured **on the gateway**, not in app code). Leave empty to call `model_serving_endpoint` directly.
- `mlflow_tracing_enabled` (default `false`) / `mlflow_experiment` — enable one MLflow trace per agent turn into the given experiment.
- `identity_provider` (default `noop`) — pluggable identity-group backend: `noop`, `rest` (SCIM/Entra/Okta), or `lmws`.
- `telemetry_catalog` / `telemetry_schema` (per target, `REPLACE_WITH_...` placeholders) and `lakebase_cdf_tables` (default `none`; `default` in the `dev`, `stage` and `prod` targets) with `cdf_catalog` / `cdf_schema` — optional exports of the app's logs and of its Lakebase change history to Unity Catalog. See [Section 7](#7-optional-export-app-logs-and-lakebase-history-to-unity-catalog).

---

## 4. CI/CD Deployment

Self-Service Hub uses GitHub Actions to automate deployments to both Development (`develop` branch) and Production (`main` branch) environments.

### Step 4.1: Create CI/CD Service Principal
1. Go to the Databricks Account Console → **User Management** → **Service Principals**.
2. Create a new Service Principal (e.g., `selfservice-cicd`).
3. Generate an OAuth secret and securely note the **Client ID** and **Secret**.
4. Grant this Service Principal access to your workspace.

### Step 4.2: Grant CI/CD Permissions
In the Databricks SQL Editor, grant the CI/CD Service Principal the necessary permissions to deploy the app:
```sql
GRANT USE CATALOG ON CATALOG * TO `selfservice-cicd`;
GRANT USE SCHEMA ON SCHEMA *.* TO `selfservice-cicd`;
GRANT SELECT ON TABLE *.*.* TO `selfservice-cicd`;
```
*(You will also need to ensure the Service Principal has "Can Query" access on your Model Serving endpoint, and "Can Use" access on your SQL Warehouse).*

### Step 4.3: Configure GitHub Environments
1. In your GitHub repository, go to **Settings → Environments**.
2. Create environments for `development` and `production`.
3. In each environment, add the following **Secrets**:
   - `DATABRICKS_CLIENT_ID` = (Your SP Client ID)
   - `DATABRICKS_CLIENT_SECRET` = (Your SP Secret)
4. Add the following **Variables**:
   - `DATABRICKS_HOST` = (e.g., `https://your-workspace.cloud.databricks.com`)

### Step 4.4: Deploy
Push your code to trigger the GitHub Actions:
```bash
git push origin develop  # → Deploys the dev app
git push origin main     # → Deploys the production app
```

---

## 5. Post-Deployment Grants

> **IMPORTANT**: Databricks Apps automatically create their own hidden Service Principal when deployed. This "App SP" needs permission to read the secrets and models you configured earlier.

After your first deployment completes successfully:

### Step 5.1: Find the App's Service Principal
1. Go to **Databricks Workspace → Compute → Apps**.
2. Click on your deployed app (e.g., `<app-name>-dev`).
3. Go to the **Permissions** tab and copy the name of the auto-created service principal (e.g., `app-xxxx-xxxx-xxxx`).

### Step 5.2: Grant Secret & Model Access
1. **Secrets:** Grant READ access to the `app-secrets` secret scope.
   ```python
   from databricks.sdk import WorkspaceClient
   from databricks.sdk.service.workspace import AclPermission
   
   w = WorkspaceClient()
   w.secrets.put_acl(
       scope="app-secrets",
       principal="<APP_SP_APPLICATION_ID>",
       permission=AclPermission.READ
   )
   ```
2. **Model Serving:** Go to **Serving** → Your endpoint → **Permissions** → Add the App SP with **Can Query**.
3. **Database:** If using Lakebase, grant the App SP **Can Use** permissions on the Lakebase SQL instance.

### Step 5.3: Restart the App
Go back to **Compute → Apps**, select your app, and click **Stop**, followed by **Start** to apply the new permissions.

### Step 5.4: Verify Governance Posture
On startup the backend logs its governance posture. Confirm the logs show **ENFORCE** mode (not SHADOW) in production:
```
GOVERNANCE: agent-tool OPA is in ENFORCE mode (mutating policy gates active).
LLM routing: via AI Gateway endpoint '...'   # or "direct to Model Serving" if no gateway
Observability: MLflow tracing ENABLED/disabled
```
If you see `SHADOW mode`, set `agent_tool_opa_enforce: "true"` for the environment and redeploy.

---

## 6. (Optional) Register the Agent to Model Serving

Self-Service Hub exposes its agent as a native MLflow `ResponsesAgent` (`SelfServiceResponsesAgent`). The in-app chat uses it directly; registering it to **Model Serving** additionally makes it available to the Databricks Playground, batch eval, and external consumers (e.g. via AI Gateway).

This is a **workspace-run** operation and needs the deploy-time dependencies `mlflow` (full, not -skinny) and `databricks-agents`, which are not part of the app's runtime requirements. Run it from an authenticated workspace shell or notebook:

```bash
pip install "mlflow>=3.1" databricks-agents
python -m scripts.register_responses_agent \
    --uc-model-name <catalog>.<schema>.self_service_agent \
    --llm-endpoint databricks-gpt-5-4-mini \
    --experiment /Shared/selfservice-agent \
    --deploy
```

The script logs the agent (models-from-code), registers it to Unity Catalog, attaches the LLM/gateway serving endpoints as resources for auth passthrough, and (with `--deploy`) provisions the serving endpoint.

> **AI Gateway note:** The new Unity AI Gateway serving objects aren't yet declarable in a Databricks Asset Bundle, which is why this step is a script rather than part of `databricks.yml`. To route the *running app* through a gateway you only need to set the `ai_gateway_endpoint` variable — no code change.

---

## 7. (Optional) Export App Logs and Lakebase History to Unity Catalog

Two independent, opt-in exports. Both write Unity Catalog managed Delta tables, and neither is needed to run the app.

| | App telemetry export | Lakebase Change Data Feed |
|---|---|---|
| Captures | The app's OpenTelemetry logs (spans and metrics only with custom instrumentation) | Every insert, update and delete on the app tables you list |
| Configured in | `databricks.yml`, per target | `databricks.yml` variables, plus `scripts/ensure_cdf.sh` after deploy |
| Lands in | `otel_logs`, `otel_metrics`, `otel_spans` | `lb_<table>_history`, one per source table |

### Step 7.1: App telemetry export (OpenTelemetry → Unity Catalog)

**Requirements:** the workspace is in a [region that supports app telemetry](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/observability#requirements); the destination schema has managed storage in the workspace's region; Databricks CLI 0.294.0+ (the bundle pins this).

1. **Choose a destination** catalog and schema per environment. Environments can share one schema: every row carries the app name in `service_name`.
2. **Grant access.** The app's service principal (Step 5.1) writes the tables; the identity that runs `bundle deploy` (the CI/CD SP, or you) needs `CREATE TABLE` so the tables can be created on first export:
   ```sql
   GRANT USE CATALOG ON CATALOG <telemetry_catalog> TO `<app-sp>`;
   GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY ON SCHEMA <telemetry_catalog>.<telemetry_schema> TO `<app-sp>`;
   GRANT USE CATALOG ON CATALOG <telemetry_catalog> TO `<deployer>`;
   GRANT USE SCHEMA, CREATE TABLE ON SCHEMA <telemetry_catalog>.<telemetry_schema> TO `<deployer>`;
   ```
3. **Fill in the placeholders** in `databricks.yml` under the target (`dev`, `stage` or `prod`): set `telemetry_catalog` and `telemetry_schema` (they ship as `REPLACE_WITH_<ENV>_TELEMETRY_CATALOG` / `REPLACE_WITH_<ENV>_TELEMETRY_SCHEMA`), then uncomment that target's `telemetry_export_destinations` block (in `stage` the whole commented `resources:` block). The block ships commented out because the app update during `bundle deploy` may reject a destination that doesn't exist, so a target carries it only once its values are real.
4. **Deploy** the target as usual. Telemetry starts flowing once the app redeploys.
5. **Verify** in Databricks SQL after opening the app once:
   ```sql
   SELECT * FROM <telemetry_catalog>.<telemetry_schema>.otel_logs LIMIT 10;

   -- Recent errors for one environment's app:
   SELECT time, body FROM <telemetry_catalog>.<telemetry_schema>.otel_logs
   WHERE service_name = '<app-name>' AND severity_text = 'ERROR'
   ORDER BY time DESC LIMIT 100;
   ```

`otel_spans` and `otel_metrics` stay empty until OpenTelemetry instrumentation is added to the app; the agent's own traces go to MLflow (`mlflow_tracing_enabled`), not here. Ingestion is billed as Lakeflow Connect (Jobs Serverless SKU), each log line is capped at 1 MB, and Databricks recommends predictive optimization on the three tables.

### Step 7.2: Lakebase Change Data Feed (app tables → `lb_<table>_history`)

[Lakebase Change Data Feed](https://docs.databricks.com/aws/en/oltp/projects/lakehouse-sync) (Public Preview; called *Lakehouse Sync* during its Beta) streams changes from Lakebase Postgres into append-only Delta tables named `lb_<table>_history`. Each row is a source row plus `_pg_change_type` (`insert`, `update_preimage`, `update_postimage` or `delete`), `_pg_lsn`, `_pg_xid`, `_timestamp` and `_sort_by`. A feed covers one Postgres schema and can't be declared as a bundle resource. The bundle opts the tables in (`lakebase_cdf_tables`) and names the destination (`cdf_catalog`, `cdf_schema`), and `scripts/ensure_cdf.sh <target>` creates the feed after deploy, skipping when one already exists. You can instead start it by hand in the Lakebase UI. The app's part is making its tables eligible.

**Where the app's data lives.** Database: the one attached as the app's `postgres` resource (`lakebase_database_id` in `databricks.yml`; normally `databricks_postgres`). Schema: **`selfservice`** (the `DB_SCHEMA` setting), not `public`. The same schema holds the LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`), which are large and rewritten on every workflow step. The feed skips any table without `REPLICA IDENTITY FULL`, which is how they stay out. **Don't** run the Databricks docs' "all tables in a schema" loop or install their auto-apply event trigger on `selfservice`: either would pull the checkpoints in.

**Requirements:**
- A workspace admin enables the **Lakebase Change Data Feed** preview on the workspace Previews page.
- The Lakebase project runs Postgres 16, 17 or 18. Check with `SHOW server_version;` in the Lakebase SQL editor.
- Whoever starts the feed has `CAN MANAGE` on the Lakebase project (the bundle grants it to the deploying user) and `USE CATALOG`, `USE SCHEMA` and `CREATE TABLE` on the destination.
- The destination catalog doesn't use default storage, and its managed storage isn't reachable only through a private endpoint (for example AWS PrivateLink). The feed can't write to either. Use a catalog whose managed location is publicly reachable.

1. **Opt the tables in.** Set `lakebase_cdf_tables` for the target in `databricks.yml` and deploy:
   ```yaml
   targets:
     prod:
       variables:
         lakebase_cdf_tables: "default"
   ```
   `default` means `requests`, `events`, `approvals`, `enforcement_audit`, `rbac_rules`, `rbac_change_sets` and `rbac_rule_events`. You can list table names instead, or combine them (`"default,failures"`); `none`, the shipped value, turns this off. On startup the app runs `ALTER TABLE ... REPLICA IDENTITY FULL` on each listed table that isn't already full. Only the table owner can do that, and the app's service principal owns them. The backend log reports `Lakebase CDF: REPLICA IDENTITY FULL in schema selfservice: set now [...]`, plus a warning for any table it couldn't find or alter (it retries on the next start). The current value is shown read-only under **Admin → Settings → Infrastructure**.
   - **Trade-off on `requests`:** full replica identity makes Postgres write the whole old row to the write-ahead log on every update and delete, and the feed stores before and after images. Enforcement Sentinel request rows carry a very large `state_context` (it can reach hundreds of MB), so their updates get expensive and their history rows are huge. If that matters, list tables explicitly and leave out `requests`. `events`, which is insert-only, still records each request's lifecycle.
   - Taking a table off the list doesn't switch it back. It stays `FULL` (and in the feed) until its owner runs `ALTER TABLE ... REPLICA IDENTITY DEFAULT`.
2. **Choose and grant the destination** catalog and schema, and set them as `cdf_catalog` and `cdf_schema` for the target (they ship as `REPLACE_WITH_<ENV>_CDF_CATALOG` / `REPLACE_WITH_<ENV>_CDF_SCHEMA`). The identity that runs `scripts/ensure_cdf.sh` (the CI/CD SP, or you) needs the grants:
   ```sql
   GRANT USE CATALOG ON CATALOG <cdf_catalog> TO `<you>`;
   GRANT USE SCHEMA, CREATE TABLE ON SCHEMA <cdf_catalog>.<cdf_schema> TO `<you>`;
   ```
   The history tables hold full row contents, including request conversations and approval comments. Grant `SELECT` on them as carefully as you would grant access to the app database.
3. **Start the feed.** Scripted: after the app has started once (so its tables exist), run `scripts/ensure_cdf.sh <target>` (add `--dry-run` to preview). The `deploy-develop` workflow runs it after `bundle run`; wire the same step into your stage and prod pipelines. It creates a feed named after the schema (`selfservice`) with the destination you set, does nothing if the schema already has a feed, and warns, without changing anything, if the existing feed writes somewhere else (a feed's destination is immutable; remove it with `databricks postgres delete-cdf-config` to move it). It needs the CLI's `databricks postgres create-cdf-config` (Beta).

   By hand instead: open **Lakebase Postgres** from the app switcher, then the app's project (its name is `database_instances.edh_ssc_db.name` in `databricks.yml`) and the `production` branch. Click the branch name to open **Branch overview**, open the **Lakebase CDF** tab and click **Start**. Choose the app's database, schema `selfservice`, and your destination catalog and schema, then click **Start**. A schema has only one feed: tables you opt in later join it automatically, so never click **Start** again for them.
4. **Verify.** In the Lakebase SQL editor:
   ```sql
   -- 'f' = full replica identity (in the feed); 'd' = default (skipped)
   SELECT c.relname, c.relreplident FROM pg_class c
   JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'selfservice' AND c.relkind = 'r' ORDER BY 1;

   SELECT * FROM wal2delta.tables;  -- SNAPSHOTTING, then STREAMING, per table
   ```
   Then in Databricks SQL:
   ```sql
   SELECT _pg_change_type, _timestamp, id, status
   FROM <cdf_catalog>.<cdf_schema>.lb_requests_history
   ORDER BY _sort_by DESC LIMIT 10;
   ```

A table appears only once it has at least one row. An app upgrade that adds a column to a captured table triggers a full re-snapshot of that table. Never add row filters or column masks to the `lb_*_history` tables, or turn on Delta change data feed on them: the first stops the feed writing to that table and the second breaks re-snapshots. JSON columns arrive as `STRING`. To stop the feed, use **Disable** on the same tab; it stops the feed for every schema in the project.
