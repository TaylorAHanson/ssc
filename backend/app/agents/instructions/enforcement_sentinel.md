# Enforcement Sentinel Instructions

**Goal**: Act as an automated Governance Pipeline. Scan a target Databricks workspace, discover policy violations, remediate them (kill/disable) if authorized, and notify the appropriate owners — producing a clear, auditable report.

The Sentinel is triggered manually or on a schedule. It does **not** require human approvals mid-run. All parameters are configured upfront.

---

## Information to Gather

When triggered manually, gather the following from the user before executing:

| # | Parameter | Required | Default | Notes |
|---|-----------|----------|---------|-------|
| 1 | **Workspace** | ✅ Yes | — | The workspace name or ID to scan. Use the `list_workspaces` tool to help the user identify the target. |
| 2 | **Enforcement Mode** | ✅ Yes | `audit_only` | `audit_only` = Discover + Notify. `active_enforcement` = Discover + Kill + Notify. |
| 3 | **Policies** | ❌ Optional | All | Comma-separated list of policy IDs to run (e.g., `notebooks_in_prod`, `tag_compliance`). Omit to run all. |
| 4 | **Notify** | ❌ Optional | Resource Owner | Email addresses or group names to receive the report. Falls back to dynamically discovered resource owner. |

> **Agent Note**: Default to `audit_only` and clearly state this to the user. Only switch to `active_enforcement` if the user explicitly requests it and confirms they understand resources will be terminated.

---

## Policy Catalog

The Sentinel enforces the following policies. Each policy has a unique `policy_id`, a `scope` indicating which workspace types it applies to, and a `severity` that determines notification urgency and action.

### Severity Levels

| Severity | Icon | Meaning | Default Action |
|----------|------|---------|----------------|
| `CRITICAL` | 🔴 | Immediate security or compliance risk | Kill immediately in `active_enforcement`; page on-call in `audit_only` |
| `HIGH` | 🟠 | Significant cost or governance violation | Kill in `active_enforcement`; alert owner + governance team |
| `MEDIUM` | 🟡 | Policy drift or emerging litter | Warn owner; kill only in `active_enforcement` after grace period |
| `LOW` | 🔵 | Hygiene / informational | Flag in report; no automatic kill |

---

### Policy Table

| `policy_id` | Name | Severity | Scope | Discovery Method | Kill Action | Threshold |
|-------------|------|----------|-------|-----------------|-------------|-----------|
| `notebooks_in_prod` | No Notebooks in Production | 🔴 CRITICAL | `prod` workspaces | `workspace.list` filtered by object type `NOTEBOOK` in `/Shared` or `/Repos` | `workspace.delete(path)` | Immediate |
| `tag_compliance` | Required Tags | 🟠 HIGH | All | List clusters, warehouses, jobs; check for `cost-center` and `owner` tags | Terminate untag resource; `stop_cluster`, `stop_warehouse` | Immediate |
| `abandoned_workspace` | Abandoned Workspace | 🟠 HIGH | All | Query `system.access.audit` for last login/query > 30 days | Flag for archival; disable compute | 30 days inactivity |
| `orphan_volumes` | Unattached Storage Volumes | 🟡 MEDIUM | All | List external volumes, check `system.storage.files` for last access | Delete volume | 60 days unaccessed |
| `stale_jobs` | Unscheduled Stale Jobs | 🟡 MEDIUM | All | List jobs where `schedule=null` and `last_run > 45 days ago` | `jobs.pause` or `jobs.delete` | 45 days idle |
| `dangling_sps` | Dangling Service Principals | 🔴 CRITICAL | All | Query SP last login from audit logs > 90 days | `token_management.revoke`; suspend SP | 90 days inactive |
| `enterprise_app_allowlist` | Enterprise Hub App Restrictions | 🔴 CRITICAL | workspace name contains `enterprise` | List Databricks Apps, Genie Spaces, Dashboards; compare against Allow List | `apps.delete`, `genie.delete`, `lakeview.delete` | Any unapproved resource |
| `enterprise_storage_cap` | Enterprise Hub Storage Cap | 🟠 HIGH | workspace name contains `enterprise` | Sum DBFS & personal volume usage per user | Delete oldest files; alert user | > 50 GB per user |
| `idle_clusters` | Idle Cluster Termination | 🟠 HIGH | All | List clusters with `state=RUNNING` and `last_activity_time > 2h` | `clusters.delete` | 2 hours idle |
| `mlflow_bloat` | Undocumented MLflow Experiments | 🔵 LOW | Domain workspaces | List experiments; filter for `last_run > 30 days`, no linked registered model | Archive experiment | 30 days stale |
| `temp_tables` | Untracked Temporary Tables | 🟡 MEDIUM | All | SQL: `SHOW TABLES IN schema` filtered by `_temp` or `_test` suffix | `DROP TABLE` | > 7 days old |
| `over_provisioned_warehouses` | Over-provisioned SQL Warehouses | 🟡 MEDIUM | All | List warehouses with `auto_stop_mins=null` or > 120, and queue depth = 0 | `warehouses.stop` or set `auto_stop_mins=30` | Utilization < 5% |

---

## Enforcement Scopes

Policies behave differently based on workspace type:

| Workspace Type | Identified By | Posture | Notes |
|----------------|--------------|---------|-------|
| **Enterprise Hub** | Name contains `enterprise` | Strict | Least permissions. No apps/genie/dashboards unless allow-listed. Hard storage caps enforced. |
| **Domain Workspace** | Follows `ws-{domain}-{env}` format | Moderate | More user autonomy. Cost controls apply. Notebooks allowed in `dev`/`test`. |
| **Production Workspace** | Environment tag = `prod` | Strict | No notebooks. All jobs must be scheduled. All resources must be tagged. |
| **Development Workspace** | Environment tag = `dev` or `test` | Relaxed | Most policies are `audit_only` by default. Stale job thresholds are doubled. |

---

## Actions (Discover → Kill → Notify)

### 1. Discover
- Call the appropriate Databricks SDK APIs (via `DatabricksProvider`) to enumerate resources in the target workspace.
- Evaluate each resource against the applicable policies in the Policy Catalog.
- Build a **violations list** with: `resource_id`, `resource_name`, `resource_type`, `policy_id`, `severity`, `owner`, `proposed_action`.

### 2. Kill (only in `active_enforcement` mode)
- For each violation in the violations list, execute the prescribed Kill Action from the Policy Table.
- Log each termination as a `fact` in the state machine for audit trail.
- Skip any resource on the **Allow List** (see Framework below).
- Record outcome: `KILLED`, `SKIPPED_ALLOWLIST`, `FAILED`.

### 3. Notify
- After all actions are complete, compile the **Sentinel Report** (see Output below).
- Send the report to all addresses in the `notify` parameter.
- If `notify` is empty, use `find_object_owner` to route notifications to each resource's owner directly.
- For `CRITICAL` violations, always CC the governance/platform team regardless of `notify` setting.

---

## Notification & Severity Routing

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

---

## Output: Sentinel Report (HTML Table)

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

---

## The Enterprise Allow List Framework

To manage exceptions to the `enterprise_app_allowlist` policy, a centralized Allow List must be maintained. This prevents over-eager enforcement from killing approved resources.

**Suggested Implementation**:
- Store the Allow List as a table in Unity Catalog: `enterprise_hub.governance.enforcement_allowlist`
- Schema: `resource_id (STRING)`, `resource_type (STRING)`, `approved_by (STRING)`, `approved_at (TIMESTAMP)`, `expiry_at (TIMESTAMP)`, `justification (STRING)`
- Before any kill action in the `enterprise_hub`, query this table: `SELECT * FROM enterprise_hub.governance.enforcement_allowlist WHERE resource_id = '{id}' AND expiry_at > NOW()`
- If a matching record exists, skip the kill and log `SKIPPED_ALLOWLIST`.
- Expired entries are treated as non-existent; the resource becomes eligible for enforcement.

**Adding Resources to the Allow List**: This should itself be a Self-Service request workflow (a future `allowlist_exception` workflow type), requiring Platform Admin approval.

---

## Adding New Policies: Framework

This section defines the contract for extending the Sentinel with new policies.

Each new policy requires:
1. **A row in the Policy Catalog table** (above) with a unique `policy_id`.
2. **A discovery function** in the state machine's `on_enter_discovering_async` hook — a callable that returns a list of violations.
3. **A kill function** in `on_enter_enforcing_async` — a callable that accepts a violation object and executes the remediation.
4. **A severity assignment** — determines notification routing automatically.
5. **A scope filter** — the state machine checks workspace name/type before applying each policy.

Policies should be implemented as independent, composable units. The discovery and enforcement phases iterate over all active policies, applying only those matching the current workspace scope.

---

## Execution

Call `execute_workflow` with:
```json
{
  "workflow_type": "enforcement_sentinel",
  "parameters": {
    "workspace": "ws-finance-prod",
    "enforcement_mode": "audit_only",
    "policies": ["notebooks_in_prod", "tag_compliance"],
    "notify": ["platform-governance@company.com"]
  }
}
```

---

## Required Providers and Methods

### DatabricksProvider (`app/providers/databricks/client.py`)

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

### NotificationProvider (`app/providers/notifications/client.py`)

**Existing Methods (Ready to Use)**:
- `send_email(to, subject, body, is_html=True)` — Send the formatted HTML Sentinel Report to all recipients.

---

## Architecture Note: Governance Pipeline vs. Provisioning Workflow

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