# ATLAS - Platform Setup Guide

Welcome to the ATLAS Platform Setup Guide! This document is intended for **Platform Administrators** or **IT Professionals** who are deploying ATLAS into their organization's Databricks environment.

## Overview

ATLAS (Agentic Control Tower for Lakehouse Automation & Self-Service Experience) is a full-stack web application that runs directly inside your Databricks workspace using **Databricks Apps**. 

To successfully deploy ATLAS, you will need to complete three distinct phases:
1. **Infrastructure Preparation:** Running an automated Databricks Notebook to create the required Unity Catalog volumes and secret scopes.
2. **Configuration:** Customizing the application's appearance and enabling specific features via a configuration file.
3. **CI/CD Deployment:** Configuring GitHub Actions to automatically securely deploy the application into your workspace.

Once deployed, the Databricks App will automatically provision its own Service Principal to securely interact with your data and infrastructure.

---

## 1. Prerequisites

Before beginning the installation, ensure you have:
- [ ] A **GitHub repository** containing the ATLAS source code.
- [ ] A **Databricks workspace** with Unity Catalog enabled.
- [ ] **Account-level admin access** (or workspace admin access) to create a Service Principal for GitHub Actions.
- [ ] A **Databricks Serverless SQL Warehouse** (used by ATLAS to execute backend queries).
- [ ] A **Model Serving Endpoint** running a Foundation Model (e.g., `databricks-gemini-2-5-flash`).

---

## 2. Infrastructure Preparation

ATLAS requires a secret scope and a Unity Catalog Volume to store its configuration and GitOps data. We have provided a Databricks Notebook to automate this process.

### Step 2.1: Run the Installer Notebook
1. Locate the `installer_notebook.py` file in the root directory of your repository or zip file.
2. In your Databricks UI, click **Workspace**, navigate to your personal folder, and select **Import**.
3. Import the `installer_notebook.py` file.
4. Attach the notebook to a cluster.
5. Fill out the widget parameters at the top of the notebook:
   - **Secret Scope Name** (default: `atlas-hub`)
   - **Catalog, Schema, and Volume Names** (where configuration will be stored)
   - **GitHub PAT** (Optional: requires `repo` scope if using direct GitOps integrations)
6. Click **Run All**.

The notebook will automatically create the infrastructure, securely store your secrets, and place a default `configuration.yaml` file into the new volume.

---

## 3. Configuration & Branding

ATLAS is highly customizable. You can control its features, UI tabs, and branding without modifying the core code.

### Step 3.1: Customize the Application
1. Open the `configuration.yaml` file located in the root of your repository.
2. Modify the `branding` section to include your company's name, logo URL, and corporate hex colors.
3. Enable or disable specific `features`, `tools`, or `workflows` as needed.

*Note: If you are using GitHub Actions, you can push these changes to a dedicated configuration branch (e.g., the `lite` version) or modify them directly in your main branch.*

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

---

## 4. CI/CD Deployment

ATLAS uses GitHub Actions to automate deployments to both Development (`develop` branch) and Production (`main` branch) environments.

### Step 4.1: Create CI/CD Service Principal
1. Go to the Databricks Account Console → **User Management** → **Service Principals**.
2. Create a new Service Principal (e.g., `atlas-cicd`).
3. Generate an OAuth secret and securely note the **Client ID** and **Secret**.
4. Grant this Service Principal access to your workspace.

### Step 4.2: Grant CI/CD Permissions
In the Databricks SQL Editor, grant the CI/CD Service Principal the necessary permissions to deploy the app:
```sql
GRANT USE CATALOG ON CATALOG * TO `atlas-cicd`;
GRANT USE SCHEMA ON SCHEMA *.* TO `atlas-cicd`;
GRANT SELECT ON TABLE *.*.* TO `atlas-cicd`;
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
git push origin develop  # → Deploys the 'atlas-dev' app
git push origin main     # → Deploys the 'atlas' production app
```

---

## 5. Post-Deployment Grants

> **IMPORTANT**: Databricks Apps automatically create their own hidden Service Principal when deployed. This "App SP" needs permission to read the secrets and models you configured earlier.

After your first deployment completes successfully:

### Step 5.1: Find the App's Service Principal
1. Go to **Databricks Workspace → Compute → Apps**.
2. Click on your deployed app (e.g., `atlas-dev`).
3. Go to the **Permissions** tab and copy the name of the auto-created service principal (e.g., `app-xxxx-xxxx-xxxx`).

### Step 5.2: Grant Secret & Model Access
1. **Secrets:** Grant READ access to the `atlas-hub` secret scope.
   ```python
   from databricks.sdk import WorkspaceClient
   from databricks.sdk.service.workspace import AclPermission
   
   w = WorkspaceClient()
   w.secrets.put_acl(
       scope="atlas-hub",
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

ATLAS exposes its agent as a native MLflow `ResponsesAgent` (`AtlasResponsesAgent`). The in-app chat uses it directly; registering it to **Model Serving** additionally makes it available to the Databricks Playground, batch eval, and external consumers (e.g. via AI Gateway).

This is a **workspace-run** operation and needs the deploy-time dependencies `mlflow` (full, not -skinny) and `databricks-agents`, which are not part of the app's runtime requirements. Run it from an authenticated workspace shell or notebook:

```bash
pip install "mlflow>=3.1" databricks-agents
python -m scripts.register_responses_agent \
    --uc-model-name <catalog>.<schema>.atlas_self_service_agent \
    --llm-endpoint databricks-gpt-5-4-mini \
    --experiment /Shared/atlas-agent \
    --deploy
```

The script logs the agent (models-from-code), registers it to Unity Catalog, attaches the LLM/gateway serving endpoints as resources for auth passthrough, and (with `--deploy`) provisions the serving endpoint.

> **AI Gateway note:** The new Unity AI Gateway serving objects aren't yet declarable in a Databricks Asset Bundle, which is why this step is a script rather than part of `databricks.yml`. To route the *running app* through a gateway you only need to set the `ai_gateway_endpoint` variable — no code change.
