# Enforcement Sentinel: OPA & Rego Policy Implementation Plan

## Executive Summary
This document outlines the architecture for the Enforcement Sentinel—an automated Governance Pipeline. The Sentinel scans Databricks workspaces, discovers policy violations, remediates them, and handles exception allowlisting.

Key architectural features include:
- **Policy Consolidation**: A generalized `asset_allowlist` Rego policy governs all environments and assets.
- **Matrix Enforcement**: The Rego policy natively supports arrays for environments (`["enterprise", "prod"]`) and assets (`["app", "genie_space", "dashboard", "job", "notebook"]`), meaning a single policy definition handles all combination variants without complex Python conditionals.
- **Clean Agent Instructions**: Agent context is minimized. The Agent only gathers parameters and executes the workflow. The execution architecture details reside entirely in this backend documentation.
- **Robust Execution Context**: Execution phases, UI rendering patterns, and the Resource Handlers architecture use an extensible abstract factory pattern for discovering and killing Databricks resources.

## Overview
The system relies on a declarative policy engine using **Open Policy Agent (OPA) and Rego**. This cleanly separates *policy logic* (what is allowed) from *enforcement logic* (how to scan and delete). 

By adopting Rego:
1. **Decoupling**: Platform admins can write, update, and test `.rego` policies independently of the Python backend code.
2. **Deterministic Agent Feedback**: The Agent can perform "dry runs" against OPA to accurately tell users what is allowed, why it's blocked, and what exception is needed.
3. **Auditability**: OPA decisions can be logged entirely, providing a cryptographic-level audit trail of *why* a resource was killed or spared at an exact point in time.

This plan details how the Allowlist exception process uses OPA to create a flexible, scalable Governance Pipeline.

---

## 1. The Rego Policy Architecture

The Sentinel evaluates Databricks resources against Rego policies. The Allowlist database acts as **context data** injected into the OPA evaluation.

### Example Rego Policy (`policies/asset_allowlist.rego`)
```rego
package databricks.governance.asset_allowlist

import future.keywords.in
import future.keywords.contains
import future.keywords.if

default action := "ALLOW"
default is_violation := false
default reason := "Resource is permitted."

restricted_environments := ["enterprise", "prod"]
restricted_assets := ["app", "genie_space", "dashboard", "job", "notebook"]

# Identify if the resource violates the baseline rule
is_violation if {
    input.workspace.type in restricted_environments
    input.resource.type in restricted_assets
}

# Find matching allowlist records for this resource
matching_exceptions := [
    e | e := input.allowlist_records[_]; 
    e.resource_id == input.resource.id
]

# ALLOW: If there is an approved exception that hasn't expired
action = "SKIPPED_ALLOWLIST" {
    is_violation
    some exception in matching_exceptions
    exception.status == "approved"
    # Check expiry (simplified for example)
    exception.expires_at > input.request_time
}
reason = exception.justification { action == "SKIPPED_ALLOWLIST"; some exception in matching_exceptions }

# REPRIEVE: If there is a pending request, spare it temporarily
action = "PENDING_EXCEPTION" {
    is_violation
    not action == "SKIPPED_ALLOWLIST"
    some exception in matching_exceptions
    exception.status == "pending"
}
reason = "Exception request is pending admin approval." { action == "PENDING_EXCEPTION" }
```

---

## 2. Database Table (The Context Provider)
The database still stores the stateful information about exceptions. This data is pulled by the backend and fed into OPA as `input`.

**Proposed Model: `AllowlistModel`** (Lakebase/PostgreSQL)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | String | Primary Key | Unique UUID for the allowlist record. |
| `resource_id` | String | Required | Normalized Databricks ID or path of the resource. |
| `resource_type` | String | Required | Enum: `app`, `notebook`, `dashboard`, `genie_space`, etc. |
| `workspace` | String | Required | The workspace ID or name where this resource lives. |
| `justification` | String | Required | Reason for the exception (e.g., "Critical enterprise app"). |
| `status` | String | Required | Status of the exception: `pending`, `approved`, `rejected`. |
| `request_id` | String | Optional | FK to `requests.id`. Links governance back to the user's ticket. |
| `approved_by` | String | Optional | Email or ID of the admin who approved the exception. |
| `expires_at` | DateTime| Optional | Date when the exception naturally revokes. |

---

## 3. Enforcement Sentinel Integration (The OPA Loop)

The Sentinel Python State Machine acts as a "Scanner and Executor", relying on OPA as the "Decision Maker".

1. **Discovery Phase**: 
   - Sentinel queries Databricks APIs to list all resources (apps, clusters, notebooks).
   - Sentinel queries the `AllowlistModel` to fetch all exceptions for the target workspace.
2. **Evaluation Phase (The Dynamic OPA Loop)**:
   - The Sentinel dynamically loads all `.rego` policy files from the `backend/policies/` directory using globbing.
   - If the user specified a subset of policies to run, it filters the loaded files.
   - For each resource, the Sentinel prepares a JSON payload:
     ```json
     {
       "input": {
         "workspace": { "name": "ws-enterprise-prod", "type": "enterprise" },
         "resource": { "id": "fin-forecast-app", "type": "app" },
         "request_time": "2026-03-18T12:00:00Z",
         "allowlist_records": [
           { "resource_id": "fin-forecast-app", "status": "approved", "expires_at": "2027-03-18T12:00:00Z", "justification": "Finance team critical app" }
         ]
       }
     }
     ```
   - For each policy file, the Sentinel queries the OPA provider using the dynamically derived package path (e.g., `data.databricks.governance.asset_allowlist`).
   - OPA responds with: `{"result": {"is_violation": true, "action": "SKIPPED_ALLOWLIST", "reason": "Finance team critical app"}}`.
3. **Enforcement Phase**:
   - If `action == "KILL"`, the Sentinel executes the Databricks delete API using Resource Handlers.
   - If `action == "SKIPPED_ALLOWLIST"` or `"PENDING_EXCEPTION"`, it is logged in the report but spared.

---

## 4. Agent Knowledge Base (Dry-Run Capabilities)

Because the policies are defined in Rego, the Agent can perfectly understand the rules without parsing Python or reading Markdown instructions.

### New Agent Tool: `evaluate_policy(workspace, resource_type, resource_id)`
The Agent can do a **dry-run** against OPA to determine if an action is allowed *before* the user even does it.

**Flow:**
- User: "Can I build an app called 'test-app' in ws-enterprise-prod?"
- Agent calls `evaluate_policy(workspace="ws-enterprise-prod", resource_type="app", resource_id="test-app")`.
- The tool fetches `AllowlistModel` (likely finding nothing for `test-app`) and sends the payload to OPA.
- OPA returns: `{"is_violation": true, "action": "KILL", "reason": "Unauthorized resource in Enterprise Hub"}`.
- Agent replies: "No, apps are not allowed in the Enterprise Hub by default. However, I can help you file an exception request. Would you like to do that?"

### New Agent Tool: `check_allowlist_status(resource_id)`
For users asking about existing exceptions:
- User: "What is the status of my exception for fin-forecast-app?"
- Agent queries the database tool directly to see if `status == 'pending'` or `'approved'`, and provides an update.

---

## 5. Practical Example Flow

1. **The Inquiry**: A user asks to deploy an app. The Agent dry-runs OPA, sees it would be blocked, and guides the user to file a `allowlist_exception` workflow.
2. **The Ticket**: The workflow immediately creates a row in the `AllowlistModel` database with `status = 'pending'`.
3. **The Sentinel Runs (Mid-Approval)**: The Sentinel scans the workspace, finds the app, and queries OPA. OPA sees the `pending` database record injected in the context. OPA returns `action = PENDING_EXCEPTION`. The Sentinel spares the app.
4. **The Approval**: A Platform Admin clicks "Approve" in the UI. The database updates to `status = 'approved'`.
5. **The Sentinel Runs (Post-Approval)**: The Sentinel scans the workspace, queries OPA. OPA sees the `approved` record and returns `action = SKIPPED_ALLOWLIST`. The Sentinel spares the app, logging the admin's justification in the final audit report.

---

---

## 7. Resource Handlers Architecture (The Execution Layer)

Once OPA determines a resource must be killed (or warned), the Sentinel needs a reliable, scalable way to execute those actions. Since deleting a Databricks App requires a different SDK call than deleting a Job or terminating a Cluster, we will use a **Resource Handler Factory** pattern.

This architecture ensures the Sentinel core loop remains clean: it just tells the handler to execute the action, and the handler figures out the Databricks SDK specifics.

### Abstract Base Class (`BaseResourceHandler`)
Location: `backend/app/providers/databricks/handlers/base.py`

```python
from abc import ABC, abstractmethod

class BaseResourceHandler(ABC):
    def __init__(self, workspace_client):
        self.workspace_client = workspace_client

    @abstractmethod
    async def discover(self) -> list[dict]:
        """Query Databricks and return a list of resources of this type."""
        pass

    @abstractmethod
    async def kill(self, resource_id: str) -> bool:
        """Execute the destructive action for this specific resource type."""
        pass

    @abstractmethod
    async def warn(self, resource_id: str, message: str) -> bool:
        """Send a targeted warning to the resource owner."""
        pass
```

### Concrete Implementations
Each resource type gets its own handler, inheriting from `BaseResourceHandler`.

**App Handler (`handlers/app_handler.py`)**
```python
class AppResourceHandler(BaseResourceHandler):
    async def discover(self):
        return [{"id": app.name, "type": "app"} for app in self.workspace_client.apps.list()]
        
    async def kill(self, resource_id: str):
        self.workspace_client.apps.delete(name=resource_id)
        return True
```

**Cluster Handler (`handlers/cluster_handler.py`)**
```python
class ClusterResourceHandler(BaseResourceHandler):
    async def kill(self, resource_id: str):
        # For clusters, "kill" might mean terminate rather than permanently delete
        self.workspace_client.clusters.permanent_delete(cluster_id=resource_id)
        return True
```

**Job Handler (`handlers/job_handler.py`)**
```python
class JobResourceHandler(BaseResourceHandler):
    async def kill(self, resource_id: str):
        self.workspace_client.jobs.delete(job_id=resource_id)
        return True
```

### The Sentinel Loop Integration
With this architecture, scaling to support new Databricks features (like new Dashboard types or Lakeflow) simply requires adding a new Handler class, without modifying the Sentinel's core execution loop.

```python
# Inside the Sentinel State Machine
handlers = {
    "app": AppResourceHandler(client),
    "job": JobResourceHandler(client),
    "notebook": NotebookResourceHandler(client)
}

# Execution Phase
for violation in violations:
    if violation.action == "KILL":
        handler = handlers.get(violation.resource_type)
        if handler:
            await handler.kill(violation.resource_id)
            log_fact("killed", violation)
```

---

## 6. Implementation Steps
1. **Rego Setup**: Add an OPA evaluation strategy (either spinning up an OPA sidecar/service, or using a Python Rego library). Define the first `.rego` policies.
2. **Database Models**: Create `AllowlistModel` and Alembic/SQLite migrations.
3. **API & UI**: Build the Admin Dashboard view and REST APIs for CRUD operations on the allowlist.
4. **Resource Handlers**: Implement the `BaseResourceHandler` and the initial concrete handlers (Apps, Jobs, Clusters).
5. **Sentinel Implementation**: Implement the Sentinel state machine to use the Handlers for discovery, evaluate via OPA, and execute kills via Handlers.
6. **Agent Tools**: Implement `evaluate_policy` for the agent to query OPA dynamically.

---

## 7. Policy Matrix (To Be Converted to Rego)

The Sentinel enforces the following policies. Each policy has a unique `policy_id`, a `scope` indicating which workspace types it applies to, and a `severity` that determines notification urgency and action.

### Severity Levels

| Severity | Icon | Meaning | Default Action |
|----------|------|---------|----------------|
| `CRITICAL` | 🔴 | Immediate security or compliance risk | Kill immediately in `active_enforcement`; page on-call in `audit_only` |
| `HIGH` | 🟠 | Significant cost or governance violation | Kill in `active_enforcement`; alert owner + governance team |
| `MEDIUM` | 🟡 | Policy drift or emerging litter | Warn owner; kill only in `active_enforcement` after grace period |
| `LOW` | 🔵 | Hygiene / informational | Flag in report; no automatic kill |

### Policy Table

| `policy_id` | Name | Severity | Scope | Discovery Method | Kill Action | Threshold |
|-------------|------|----------|-------|-----------------|-------------|-----------|
| `asset_allowlist` | Restrict specific assets | 🔴 CRITICAL | `["enterprise", "prod"]` | List Apps, Genie Spaces, Dashboards, Jobs, Notebooks; compare against Allow List | `delete`, `pause`, `stop` | Any unapproved resource |
| `notebooks_in_prod` | No Notebooks in Production | 🔴 CRITICAL | `prod` workspaces | `workspace.list` filtered by object type `NOTEBOOK` in `/Shared` or `/Repos` | `workspace.delete(path)` | Immediate |
| `tag_compliance` | Required Tags | 🟠 HIGH | All | List clusters, warehouses, jobs; check for `cost-center` and `owner` tags | Terminate untag resource; `stop_cluster`, `stop_warehouse` | Immediate |
| `abandoned_workspace` | Abandoned Workspace | 🟠 HIGH | All | Query `system.access.audit` for last login/query > 30 days | Flag for archival; disable compute | 30 days inactivity |
| `orphan_volumes` | Unattached Storage Volumes | 🟡 MEDIUM | All | List external volumes, check `system.storage.files` for last access | Delete volume | 60 days unaccessed |
| `stale_jobs` | Unscheduled Stale Jobs | 🟡 MEDIUM | All | List jobs where `schedule=null` and `last_run > 45 days ago` | `jobs.pause` or `jobs.delete` | 45 days idle |
| `dangling_sps` | Dangling Service Principals | 🔴 CRITICAL | All | Query SP last login from audit logs > 90 days | `token_management.revoke`; suspend SP | 90 days inactive |
| `asset_allowlist` | Restrict specific assets | 🔴 CRITICAL | `["enterprise", "prod"]` | List Apps, Genie Spaces, Dashboards, Jobs, Notebooks; compare against Allow List | `delete`, `pause`, `stop` | Any unapproved resource |
| `enterprise_storage_cap` | Enterprise Hub Storage Cap | 🟠 HIGH | workspace name contains `enterprise` | Sum DBFS & personal volume usage per user | Delete oldest files; alert user | > 50 GB per user |
| `idle_clusters` | Idle Cluster Termination | 🟠 HIGH | All | List clusters with `state=RUNNING` and `last_activity_time > 2h` | `clusters.delete` | 2 hours idle |
| `mlflow_bloat` | Undocumented MLflow Experiments | 🔵 LOW | Domain workspaces | List experiments; filter for `last_run > 30 days`, no linked registered model | Archive experiment | 30 days stale |
| `temp_tables` | Untracked Temporary Tables | 🟡 MEDIUM | All | SQL: `SHOW TABLES IN schema` filtered by `_temp` or `_test` suffix | `DROP TABLE` | > 7 days old |
| `over_provisioned_warehouses` | Over-provisioned SQL Warehouses | 🟡 MEDIUM | All | List warehouses with `auto_stop_mins=null` or > 120, and queue depth = 0 | `warehouses.stop` or set `auto_stop_mins=30` | Utilization < 5% |
## 8. Sentinel Execution Context & Architecture

### Enforcement Scopes

Policies behave differently based on workspace type:

| Workspace Type | Identified By | Posture | Notes |
|----------------|--------------|---------|-------|
| **Enterprise Hub** | Name contains `enterprise` | Strict | Least permissions. No apps/genie/dashboards unless allow-listed. Hard storage caps enforced. |
| **Domain Workspace** | Follows `ws-{domain}-{env}` format | Moderate | More user autonomy. Cost controls apply. Notebooks allowed in `dev`/`test`. |
| **Production Workspace** | Environment tag = `prod` | Strict | No notebooks. All jobs must be scheduled. All resources must be tagged. |
| **Development Workspace** | Environment tag = `dev` or `test` | Relaxed | Most policies are `audit_only` by default. Stale job thresholds are doubled. |

### Actions (Discover → Kill → Notify)

#### 1. Discover
- Call the appropriate Databricks SDK APIs (via `DatabricksProvider`) to enumerate resources in the target workspace.
- Evaluate each resource against the applicable policies in the Policy Catalog.
- Build a **violations list** with: `resource_id`, `resource_name`, `resource_type`, `policy_id`, `severity`, `owner`, `proposed_action`.

#### 2. Kill (only in `active_enforcement` mode)
- For each violation in the violations list, execute the prescribed Kill Action from the Policy Table.
- Log each termination as a `fact` in the state machine for audit trail.
- Skip any resource on the **Allow List** (see Framework below).
- Record outcome: `KILLED`, `SKIPPED_ALLOWLIST`, `FAILED`.

#### 3. Notify
- After all actions are complete, compile the **Sentinel Report** (see Output below).
- Send the report to all addresses in the `notify` parameter.
- If `notify` is empty, use `find_object_owner` to route notifications to each resource's owner directly.
- For `CRITICAL` violations, always CC the governance/platform team regardless of `notify` setting.

### Notification & Severity Routing

| Severity | Recipient | Channel | SLA |
|----------|-----------|---------|-----|
| `CRITICAL` | Resource Owner + Platform Governance Team | Email (HTML) | Immediately |
| `HIGH` | Resource Owner + Manager | Email (HTML) | Within 1 hour |
| `MEDIUM` | Resource Owner | Email (HTML) | Same day |
| `LOW` | Resource Owner | Email (summary digest) | Weekly rollup |

**Email Content Requirements**:
- Subject: `[Enforcement Sentinel] {severity} Policy Violation in {workspace_name}`
- Body: Include the HTML table (see Output section), a plain-language summary, and a link to the request in the Self-Service Hub.
- For kill actions: clearly state what was terminated, when, and who authorized the run.

### Output: Sentinel Report (HTML Table)

After the Discover (and optional Kill) phase, always return a structured report to the user in the chat. The report must contain:

1. **Summary Header**: Workspace name, scan timestamp, mode (`audit_only` / `active_enforcement`), total violations found, total actions taken.
2. **Violations Table** (HTML):

```html
<table>
  <thead>
    <tr>
      <th>Severity</th>
      <th>Policy</th>
      <th>Resource Name</th>
      <th>Resource Type</th>
      <th>Owner</th>
      <th>Proposed / Taken Action</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    <!-- One row per violation -->
    <tr>
      <td>🔴 CRITICAL</td>
      <td>No Notebooks in Production</td>
      <td>/Users/jane.doe/analysis.ipynb</td>
      <td>Notebook</td>
      <td>jane.doe@company.com</td>
      <td>DELETE notebook</td>
      <td>KILLED ✅ / PROPOSED 🔍</td>
    </tr>
  </tbody>
</table>
```

3. **Footer**: Confirmation of who was notified and a note if any resources were on the Allow List and skipped.

### The Enterprise Allow List Framework

To manage exceptions to the `asset_allowlist` policy, a centralized Allow List must be maintained. This prevents over-eager enforcement from killing approved resources.

**Suggested Implementation**:
- Store the Allow List as a table in Unity Catalog: `enterprise_hub.governance.enforcement_allowlist`
- Schema: `resource_id (STRING)`, `resource_type (STRING)`, `approved_by (STRING)`, `approved_at (TIMESTAMP)`, `expiry_at (TIMESTAMP)`, `justification (STRING)`
- Before any kill action in the `enterprise_hub`, query this table: `SELECT * FROM enterprise_hub.governance.enforcement_allowlist WHERE resource_id = '{id}' AND expiry_at > NOW()`
- If a matching record exists, skip the kill and log `SKIPPED_ALLOWLIST`.
- Expired entries are treated as non-existent; the resource becomes eligible for enforcement.

**Adding Resources to the Allow List**: This should itself be a Self-Service request workflow (a future `allowlist_exception` workflow type), requiring Platform Admin approval.

### Adding New Policies: Framework

This section defines the contract for extending the Sentinel with new policies. Because the Sentinel dynamically loads policies using a glob pattern, adding a new policy requires zero Python code changes!

Each new policy requires:
1. **A new `.rego` file** in the `backend/policies/` directory (e.g., `my_new_rule.rego`).
2. **A package name** matching the filename (e.g., `package databricks.governance.my_new_rule`).
3. **The standard contract** defining `action`, `is_violation`, and `reason` within the Rego file.
4. **A row in the Policy Catalog table** (above) with a matching `policy_id`.
5. **A discovery function** in the appropriate Resource Handler to fetch the resources.

Policies are implemented as independent, composable units. The discovery and enforcement phases iterate over all active policies, applying only those matching the current workspace scope natively in Rego.

### Required Providers and Methods

#### DatabricksProvider (`app/providers/databricks/client.py`)

**Existing Methods (Ready to Use)**:
- `execute_sql(query)` — Query `system.*` audit tables for usage, billing, and access history.
- `find_object_owner(object_type, object_name)` — Resolve owner for jobs, notebooks, clusters, dashboards, genie spaces.

**Required Methods to Implement**:

| Group | Method Signature | Purpose |
|-------|-----------------|---------|
| Workspace | `list_workspaces()` | List all workspaces at account level |
| Compute | `list_clusters(workspace_id)` | List all clusters |
| Compute | `terminate_cluster(workspace_id, cluster_id)` | Terminate a running cluster |
| Compute | `list_sql_warehouses(workspace_id)` | List SQL warehouses |
| Compute | `stop_sql_warehouse(workspace_id, warehouse_id)` | Stop a SQL warehouse |
| Jobs | `list_jobs(workspace_id)` | List all jobs |
| Jobs | `pause_job(workspace_id, job_id)` | Pause a scheduled job |
| Jobs | `delete_job(workspace_id, job_id)` | Permanently delete a job |
| Notebooks | `find_notebooks(workspace_id, path)` | Recursively list notebooks in a path |
| Notebooks | `delete_notebook(workspace_id, path)` | Delete a notebook |
| Apps | `list_apps(workspace_id)` | List Databricks Apps |
| Apps | `delete_app(workspace_id, app_id)` | Delete a Databricks App |
| Genie | `list_genie_spaces(workspace_id)` | List Genie Spaces |
| Genie | `delete_genie_space(workspace_id, space_id)` | Delete a Genie Space |
| Storage | `list_volumes(workspace_id)` | List external volumes |
| Identity | `revoke_service_principal_token(sp_id)` | Revoke a Service Principal's tokens |

#### NotificationProvider (`app/providers/notifications/client.py`)

**Existing Methods (Ready to Use)**:
- `send_email(to, subject, body, is_html=True)` — Send the formatted HTML Sentinel Report to all recipients.

### Architecture Note: Governance Pipeline vs. Provisioning Workflow

The Enforcement Sentinel is **not** a standard Provisioning Workflow. It is a **Governance Pipeline** — a distinct workflow class with the following characteristics:

| Dimension | Provisioning Workflow (e.g., `workspace_provision`) | Governance Pipeline (`enforcement_sentinel`) |
|-----------|------------------------------------------------------|----------------------------------------------|
| **Trigger** | User intent (conversational request) | Schedule or admin trigger |
| **Approvals** | Human-in-the-loop (manager, platform admin) | Zero — parameters set at invocation |
| **Primary Action** | Create / provision resources | Discover / destroy / quarantine resources |
| **Agent Role** | Gather parameters, call `execute_workflow` | Gather scope + mode, call `execute_workflow` |
| **State Machine Role** | Sequential provisioning steps | Fan-out discovery → conditional enforcement → notify |
| **Data Output** | Request status update | HTML Sentinel Report (tabular violations list) |
| **Idempotency** | Critical (prevent duplicate provisioning) | Critical (prevent double-killing the same resource) |

Other potential **Governance Pipeline** workflows to build in the future:
- `cost_report_sentinel` — Weekly cost attribution report by team/domain.
- `access_certification_sentinel` — Quarterly review of user-to-resource access grants.
- `data_lineage_sentinel` — Surface tables with no downstream consumers for potential archival.