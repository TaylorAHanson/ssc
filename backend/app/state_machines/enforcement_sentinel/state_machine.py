"""
Enforcement Sentinel state machine.
Automated Governance Pipeline for discovering and remediating policy violations.

To execute this Governance Pipeline, the State Machine relies on the following providers. 

### DatabricksProvider (`app/providers/databricks/client.py`)
This provider handles all interaction with the Databricks environments. 
**Existing Methods:**
*   `execute_sql`: Can be used to query system tables for billing, usage, or job state if configured.
*   `find_object_owner`: Existing logic to determine the owner of jobs, notebooks, clusters, or workspaces for notification routing.

**Required Methods to Implement:**
The Enforcement Sentinel requires the rapid addition of Several Workspace, Compute, and Job APIs to the `DatabricksProvider`:
*   *Workspace Management*: `list_workspaces()` (hub level), `delete_workspace(workspace_id)`
*   *Compute Management*: `list_clusters(workspace_id)`, `terminate_cluster(workspace_id, cluster_id)`, `list_sql_warehouses(workspace_id)`, `stop_sql_warehouse(workspace_id, warehouse_id)`
*   *Job/Notebook Management*: `list_jobs(workspace_id)`, `pause_job(workspace_id, job_id)`, `delete_job(workspace_id, job_id)`, `find_notebooks(workspace_id, path)`, `delete_notebook(workspace_id, path)`
*   *App/Genie Management*: `list_apps(workspace_id)`, `delete_app(workspace_id, app_id)`, `list_genie_spaces(workspace_id)`, `delete_genie_space(workspace_id, space_id)`
*   *Storage/Identity*: `list_volumes(workspace_id)`, `revoke_service_principal_token(sp_id)`, `list_genie_spaces(workspace_id)`, `delete_genie_space(workspace_id, space_id)`

### NotificationProvider (`app/providers/notifications/client.py`)
This provider handles alerting resource owners and the governance team.
**Existing Methods:**
*   `send_email(to, subject, body, is_html=True)`: **Ready to use.** The Sentinel will compile the HTML table of violated policies and localized remediation actions, then invoke this method to alert the specified recipients in the `notify` parameter, or the dynamically discovered owner of the resources.
"""

from statemachine import State
from app.state_machines.base import BaseRequestStateMachine


class EnforcementSentinelStateMachine(BaseRequestStateMachine):
    """
    State machine for running policy enforcement scans and remediations.
    This bypasses human approvals and acts as an automated pipeline.
    """
    
    pending = State("pending", initial=True)
    discovering = State("discovering")
    enforcing = State("enforcing")
    notifying = State("notifying")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    # Basic Transitions
    submit = pending.to(discovering, cond="has_request_submitted")
    
    # In a full implementation, these transitions would be governed by facts 
    # (e.g. "has_discover_completed", "has_enforce_completed")
    # For now, we define the basic forward flow.
    finish_discovering = discovering.to(enforcing)
    finish_enforcing = enforcing.to(notifying)
    finish_notifying = notifying.to(completed)

    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        discovering.to(rejected, cond="has_request_rejected")
    )
    
    # We will implement the actual logic in these async hooks later
    async def on_enter_discovering_async(self):
        """Execute async API calls to discover resources and check policies."""
        pass

    async def on_enter_enforcing_async(self):
        """Execute destructive actions if active_enforcement mode and dry_run=False."""
        pass

    async def on_enter_notifying_async(self):
        """Dispatch summary report to the notify target."""
        pass
