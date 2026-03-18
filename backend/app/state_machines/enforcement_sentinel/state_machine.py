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
from app.state_machines.facts import add_fact
from app.providers.opa.client import OpaProvider
from app.providers.databricks.handlers import AppResourceHandler, ClusterResourceHandler, JobResourceHandler
from app.db.allowlist import AllowlistModel
from datetime import datetime
import os
import glob

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
    
    # Simple flow based on facts
    finish_discovering = discovering.to(enforcing, cond="has_discover_completed")
    finish_enforcing = enforcing.to(notifying, cond="has_enforce_completed")
    finish_notifying = notifying.to(completed, cond="has_notify_completed")

    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        discovering.to(rejected, cond="has_request_rejected")
    )
    
    @property
    def has_discover_completed(self) -> bool:
        return self.has_fact("discover_completed")

    @property
    def has_enforce_completed(self) -> bool:
        return self.has_fact("enforce_completed")

    @property
    def has_notify_completed(self) -> bool:
        return self.has_fact("notify_completed")

    def has_fact(self, fact_type: str) -> bool:
        from app.state_machines.facts import has_fact as check_fact
        return check_fact(self.db, self.request.id, fact_type)
    
    async def on_enter_discovering_async(self):
        """Execute async API calls to discover resources and check policies."""
        if self.has_fact("discover_completed"):
            return

        workspace_name = self.request.state_context.get("workspace", "ws-enterprise-prod")
        
        # Determine workspace type based on name for OPA context
        workspace_type = "enterprise" if "enterprise" in workspace_name else "domain"
        
        # 1. Fetch Allowlist Context from DB
        allowlist_records = []
        db_entries = self.db.query(AllowlistModel).filter(AllowlistModel.workspace == workspace_name).all()
        for entry in db_entries:
            allowlist_records.append({
                "resource_id": entry.resource_id,
                "status": entry.status,
                "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                "justification": entry.justification
            })

        # 2. Discover resources using Handlers (simplified mock for now since DatabricksProvider is missing workspace_client)
        # In real code: handler = AppResourceHandler(databricks_client)
        # apps = await handler.discover()
        apps = [{"id": "fin-forecast-app", "type": "app"}] # Mock data for example

        # 3. Evaluate with OPA
        opa_provider = OpaProvider()
        violations = []
        
        # In a real implementation, we would iterate over all policies in the policies directory.
        # For now, we will simulate loading multiple policies or use the specific one requested.
        
        # Let's dynamically load all rego policies from the policies directory
        import os
        import glob
        policy_files = glob.glob(os.path.join("policies", "*.rego"))
        
        # If the user requested specific policies, filter them
        requested_policies = self.request.state_context.get("policies", [])
        if requested_policies:
            policy_files = [p for p in policy_files if any(req in p for req in requested_policies)]
            
        for resource in apps:
            input_data = {
                "workspace": {"name": workspace_name, "type": workspace_type},
                "resource": {"id": resource["id"], "type": resource["type"]},
                "request_time": datetime.utcnow().isoformat(),
                "allowlist_records": allowlist_records
            }
            
            for policy_path in policy_files:
                # Extract policy name to construct the query path
                # e.g. policies/asset_allowlist.rego -> asset_allowlist
                policy_name = os.path.basename(policy_path).replace(".rego", "")
                query = f"data.databricks.governance.{policy_name}"
                
                result = await opa_provider.evaluate(
                    policy_path=policy_path,
                    query=query,
                    input_data=input_data
                )
                
                if result.get("is_violation"):
                    violations.append({
                        "resource_id": resource["id"],
                        "resource_type": resource["type"],
                        "policy": policy_name,
                        "action": result.get("action", "KILL"),
                        "reason": result.get("reason", "Unknown violation")
                    })
        
        # Save violations to state context and record fact
        self.request.state_context["violations"] = violations
        add_fact(self.db, self.request.id, "discover_completed", {"violation_count": len(violations)})
        self.finish_discovering()

    async def on_enter_enforcing_async(self):
        """Execute destructive actions if active_enforcement mode."""
        if self.has_fact("enforce_completed"):
            return
            
        mode = self.request.state_context.get("enforcement_mode", "audit_only")
        violations = self.request.state_context.get("violations", [])
        
        if mode == "active_enforcement":
            # Initialize handlers (Mocked for now)
            handlers = {
                "app": AppResourceHandler(None),
                "cluster": ClusterResourceHandler(None),
                "job": JobResourceHandler(None)
            }
            
            for violation in violations:
                if violation["action"] == "KILL":
                    handler = handlers.get(violation["resource_type"])
                    if handler:
                        # await handler.kill(violation["resource_id"])
                        # Simulate kill
                        pass
        
        add_fact(self.db, self.request.id, "enforce_completed", {})
        self.finish_enforcing()

    async def on_enter_notifying_async(self):
        """Dispatch summary report to the notify target."""
        if self.has_fact("notify_completed"):
            return
            
        # Send email via NotificationProvider
        add_fact(self.db, self.request.id, "notify_completed", {})
        self.finish_notifying()

