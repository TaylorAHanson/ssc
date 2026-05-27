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

import glob
import logging
import os
from datetime import datetime, timezone

from statemachine import State

from app.core.config import settings
from app.db.allowlist import AllowlistModel
from app.providers.databricks.handlers import (
    AppResourceHandler,
    ClusterResourceHandler,
    JobResourceHandler,
    SqlWarehouseResourceHandler,
    DashboardResourceHandler,
    GenieSpaceResourceHandler,
    ServicePrincipalResourceHandler,
    NotebookResourceHandler,
    VolumeResourceHandler,
    DatasetResourceHandler
)
from app.providers.opa.client import OpaProvider
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.enforcement_sentinel.remediation import (
    NON_REMEDIATION_ACTIONS,
    normalize_severity,
    resolve_enforcement_step,
    warn_prefix,
)
from app.state_machines.facts import add_fact

logger = logging.getLogger(__name__)

@workflow(request_types=RequestType.ENFORCEMENT_SENTINEL, feature_flag="governance")
class EnforcementSentinelStateMachine(BaseRequestStateMachine):
    """
    State machine for running policy enforcement scans and remediations.
    This bypasses human approvals and acts as an automated pipeline.
    """
    
    # State UI Configurations
    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "discovering": "discover_completed",
        "enforcing": "enforce_completed",
        "notifying": "notify_completed",
        "rejected": "request_rejected"
    }

    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "discovering": ["discover_completed"],
        "enforcing": ["enforce_completed"],
        "notifying": ["notify_completed"],
        "rejected": ["request_rejected"]
    }
    
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
        environment = self.request.state_context.get("environment", "prod")
        
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

        # 2. Discover resources using Handlers
        from app.providers.databricks.client import DatabricksProvider
        try:
            provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL, 
                client_id=settings.DATABRICKS_CLIENT_ID, 
                client_secret=settings.DATABRICKS_CLIENT_SECRET
            )
            workspace_client = provider.client
        except Exception as e:
            logger.error(f"Failed to initialize DatabricksProvider: {e}")
            ctx = dict(self.request.state_context or {})
            ctx["violations"] = []
            self.request.state_context = ctx
            add_fact(self.db, self.request.id, "discover_completed", {"error": str(e), "violation_count": 0})
            self.finish_discovering()
            return
            
        # If a specific dataset_id was requested, only run the DatasetResourceHandler
        dataset_id = self.request.state_context.get("dataset_id")
        
        if dataset_id:
            handler_classes = [DatasetResourceHandler]
        else:
            handler_classes = [
                AppResourceHandler,
                ClusterResourceHandler,
                JobResourceHandler,
                SqlWarehouseResourceHandler,
                DashboardResourceHandler,
                GenieSpaceResourceHandler,
                ServicePrincipalResourceHandler,
                NotebookResourceHandler,
                VolumeResourceHandler,
                DatasetResourceHandler
            ]
        
        discovered_resources = []
        for handler_class in handler_classes:
            handler = handler_class(workspace_client)
            # If we're only looking for a specific dataset, we could pass it to the handler
            # but for now we'll just filter the results
            resources = await handler.discover()
            if dataset_id and handler_class == DatasetResourceHandler:
                resources = [r for r in resources if r.get("dataset_id") == dataset_id or r.get("id") == dataset_id]
            discovered_resources.extend(resources)

        from app.db.data_asset import DataAssetModel
        for resource in discovered_resources:
            if resource.get("type") == "data_product" and "dataset_id" in resource:
                dataset_id = resource.get("dataset_id")
                asset = self.db.query(DataAssetModel).filter(DataAssetModel.id == dataset_id).first()
                if not asset:
                    asset = DataAssetModel(
                        id=dataset_id,
                        catalog="Multiple datasets",
                        schema="",
                        table_name=dataset_id,
                        type="DATA_PRODUCT",
                        description=f"Data contract for {dataset_id}",
                        domain="unknown",
                        contract_url=f"/governance/certification?dataset={dataset_id}"
                    )
                    self.db.add(asset)
                    self.db.flush()
                
                dq = dict(asset.data_quality or {})
                # Calculate aggregate failed rules for UI display
                total_failed = sum([a.get("failed_rule_count", 0) for a in resource.get("assets", []) if a.get("failed_rule_count", -1) >= 0])
                # If any asset failed to fetch (-1), mark as -1
                if any(a.get("failed_rule_count", 0) < 0 for a in resource.get("assets", [])):
                    total_failed = -1
                dq["failed_rule_count"] = total_failed
                asset.data_quality = dq
                
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(asset, "data_quality")
                
                self.db.add(asset)
        
        try:
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to commit DataAsset quality updates: {e}")
            self.db.rollback()

        # 3. Evaluate with OPA
        opa_provider = OpaProvider(settings.opa_provider_config())
        violations = []
        # Per-(resource, policy) checklist. Captures every evaluation —
        # PASS or VIOLATION — alongside a slim snapshot of the resource
        # being checked (description, tags, etc.) so governance reviewers
        # can audit what was verified, not only what failed.
        checks = []

        # In a real implementation, we would iterate over all policies in the policies directory.
        # For now, we will simulate loading multiple policies or use the specific one requested.
        
        # Dynamically load all rego policies from the policies directory
        policy_files = glob.glob(os.path.join("policies", "*.rego"))
        all_policy_names = {
            os.path.basename(p).replace(".rego", "") for p in policy_files
        }

        # If the user requested specific policies, filter them
        requested_policies = self.request.state_context.get("policies", [])
        if requested_policies:
            policy_files = [p for p in policy_files if any(req in p for req in requested_policies)]
            allowed_policy_names = {
                os.path.basename(p).replace(".rego", "") for p in policy_files
            }
        else:
            allowed_policy_names = all_policy_names

        for resource in discovered_resources:
            input_data = {
                "workspace": {"name": workspace_name, "type": workspace_type, "environment": environment},
                "resource": resource,
                "request_time": datetime.now(timezone.utc).isoformat(),
                "allowlist_records": allowlist_records
            }

            # Single OPA call per resource that returns every policy result.
            # Spawning the OPA binary once and reading all .rego files once is
            # ~10× faster than evaluating each policy individually (see the
            # `evaluate_namespace` docstring for the underlying reasoning).
            namespace_results = await opa_provider.evaluate_namespace(input_data)

            for policy_name, result in namespace_results.items():
                if policy_name not in allowed_policy_names:
                    continue

                # Skip policies that had no applicable rules for this resource
                # (e.g. compute_and_jobs vs a data_product). Recording these
                # as vacuous PASS checks bloats state_context and produces
                # misleading "Verified — no rules mapped" rows in the UI.
                rule_results_raw = result.get("rule_results", []) or []
                if not rule_results_raw and not result.get("is_violation"):
                    continue

                is_violation = result.get("is_violation")
                action = result.get("action", "KILL")

                # Always emit a check-evaluated log so we have a per-(resource, policy)
                # trace whether the check passed or failed — not just failures. This
                # makes it possible to confirm "the check ran and passed" vs. "the
                # check was skipped/missed" (asked for in the TDQ/BDQ working session).
                # Violations still get an additional WARNING log below so high-severity
                # findings remain easy to grep / alert on.
                logger.info(
                    f"POLICY_CHECK_EVALUATED: "
                    f"Policy={policy_name} | "
                    f"Result={'VIOLATION' if is_violation else 'PASS'} | "
                    f"Action={action} | "
                    f"ResourceType={resource.get('type')} | "
                    f"ResourceID={resource.get('id')} | "
                    f"Severity={result.get('severity', 'N/A')}"
                )

                # Build a slim resource snapshot. We deliberately copy only the
                # fields a governance reviewer would want to see ("what was
                # actually verified") and skip nested arrays / large payloads
                # to keep state_context size bounded for runs with many
                # resources × policies.
                resource_snapshot = {
                    "name": (
                        resource.get("name")
                        or resource.get("title")
                        or resource.get("table_name")
                        or resource.get("id")
                    ),
                    "description": resource.get("description"),
                    "tags": resource.get("tags"),
                    "owner": resource.get("owner"),
                    "catalog": resource.get("catalog"),
                    "schema": resource.get("schema"),
                    "policies": resource.get("policies"),
                }
                # Drop None values so the UI doesn't have to filter empty keys.
                resource_snapshot = {
                    k: v for k, v in resource_snapshot.items() if v not in (None, "", [])
                }

                # `rule_results` is emitted directly by each rego policy
                # (see policies/*.rego). Each entry is a per-rule pass/fail
                # record with messages, scoped to the rules that were
                # actually applicable to this (resource, workspace) tuple.
                # Sort for stable UI rendering (failed rules first, then alpha).
                rule_results_sorted = sorted(
                    rule_results_raw,
                    key=lambda r: (bool(r.get("passed")), r.get("id", "")),
                )

                check_record = {
                    "resource_id": resource.get("id"),
                    "resource_type": resource.get("type"),
                    "resource": resource_snapshot,
                    "policy": policy_name,
                    "result": "VIOLATION" if is_violation else "PASS",
                    "action": action,
                    "reason": result.get(
                        "reason",
                        "" if not is_violation else "Unknown violation",
                    ),
                    "violation_reasons": result.get("violation_reasons", []),
                    "rule_results": rule_results_sorted,
                    "severity": result.get("severity", "N/A"),
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
                checks.append(check_record)

                # Update local DataAsset cache with the latest violations if evaluating data certification
                if policy_name == "data_certification" and resource.get("type") == "data_product":
                    from app.db.data_asset import DataAssetModel
                    from sqlalchemy.orm.attributes import flag_modified
                    dataset_id = resource.get("dataset_id", resource.get("id"))
                    asset = self.db.query(DataAssetModel).filter(DataAssetModel.id == dataset_id).first()
                    if not asset:
                        asset = DataAssetModel(
                            id=dataset_id,
                            catalog="Multiple datasets",
                            schema="",
                            table_name=dataset_id,
                            type="DATA_PRODUCT",
                            description=f"Data contract for {dataset_id}",
                            domain="unknown",
                            contract_url=f"/governance/certification?dataset={dataset_id}"
                        )
                        self.db.add(asset)
                        self.db.flush() # flush to get an ID if needed
                    
                    asset.certification_violations = result.get("violation_reasons", [])
                    flag_modified(asset, "certification_violations")
                    self.db.add(asset)
                
                # We record it if it's an actual violation, OR if the action is a proactive enforcement step like CERTIFY or UNCERTIFY
                if is_violation or action in ["CERTIFY", "UNCERTIFY"]:
                    violation_record = {
                        "resource_id": resource.get("id"),
                        "resource_type": resource.get("type"),
                        "policy": policy_name,
                        "action": action,
                        "reason": result.get("reason", "Action triggered" if not is_violation else "Unknown violation"),
                        "violation_reasons": result.get("violation_reasons", []),
                        "severity": result.get("severity", "HIGH"),
                        "input_context": input_data,
                    }
                    violations.append(violation_record)
                    
                    if is_violation:
                        logger.warning(
                            f"POLICY_VIOLATION_DETECTED: "
                            f"Policy={policy_name} | "
                            f"Action={action} | "
                            f"ResourceType={violation_record['resource_type']} | "
                            f"ResourceID={violation_record['resource_id']} | "
                            f"Reason={violation_record['reason']} | "
                            f"Severity={violation_record['severity']} | "
                            f"Violations={violation_record['violation_reasons']}"
                        )
        
        # Save violations + full checklist to state context and record fact.
        # SQLAlchemy needs a fresh dict assignment or flag_modified to detect
        # changes to JSON columns.
        pass_count = sum(1 for c in checks if c["result"] == "PASS")
        violation_count = sum(1 for c in checks if c["result"] == "VIOLATION")

        ctx = dict(self.request.state_context or {})
        ctx["violations"] = violations
        ctx["checks"] = checks
        self.request.state_context = ctx

        add_fact(self.db, self.request.id, "discover_completed", {
            "violation_count": violation_count,
            "pass_count": pass_count,
            "total_resources_scanned": len(discovered_resources),
            "policies_evaluated": len(policy_files),
            # Actual count of (resource, policy) evaluations performed,
            # rather than the resources × policies estimate.
            "total_checks": len(checks),
        })
        
        try:
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to commit discover results: {e}")
            self.db.rollback()
            
        self.finish_discovering()

    async def on_enter_enforcing_async(self):
        """Execute destructive actions if active_enforcement mode."""
        if self.has_fact("enforce_completed"):
            return
            
        mode = self.request.state_context.get("enforcement_mode", "audit_only")
        violations = self.request.state_context.get("violations", [])
        
        from app.providers.databricks.client import DatabricksProvider
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL, 
            client_id=settings.DATABRICKS_CLIENT_ID, 
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        workspace_client = provider.client
        
        from app.db.enforcement_audit import EnforcementAuditModel
        import uuid
        from app.state_machines.enforcement_sentinel.remediation import determine_intended_step

        handlers = {
            "app": AppResourceHandler(workspace_client),
            "cluster": ClusterResourceHandler(workspace_client),
            "job": JobResourceHandler(workspace_client),
            "sql_warehouse": SqlWarehouseResourceHandler(workspace_client),
            "dashboard": DashboardResourceHandler(workspace_client),
            "genie_space": GenieSpaceResourceHandler(workspace_client),
            "service_principal": ServicePrincipalResourceHandler(workspace_client),
            "notebook": NotebookResourceHandler(workspace_client),
            "storage": VolumeResourceHandler(workspace_client),
            "table": DatasetResourceHandler(workspace_client),
            "data_product": DatasetResourceHandler(workspace_client),
        }

        for violation in violations:
            action = violation.get("action", "KILL")
            severity = violation.get("severity", "HIGH")
            handler = handlers.get(violation["resource_type"])

            if action in NON_REMEDIATION_ACTIONS:
                logger.info(
                    "Enforcement skip (non-remediation action): policy=%s resource=%s action=%s",
                    violation.get("policy"),
                    violation.get("resource_id"),
                    action,
                )
                # Continue execution to log the skip
                
            step = resolve_enforcement_step(mode, severity, action)
            intended = determine_intended_step(severity, action)
            executed_action = step

            if step == "skip" or step == "audit_skipped":
                logger.debug(
                    "Enforcement %s: policy=%s resource=%s action=%s severity=%s",
                    step,
                    violation.get("policy"),
                    violation.get("resource_id"),
                    action,
                    normalize_severity(severity),
                )
            elif step == "warn":
                if not handler:
                    logger.warning(
                        "No handler for resource_type=%s; cannot warn for policy=%s",
                        violation.get("resource_type"),
                        violation.get("policy"),
                    )
                    executed_action = "error_no_handler"
                else:
                    body = violation.get("reason", "")
                    if action != "WARN":
                        body = f"{warn_prefix(severity, action)} {body}".strip()
                    sev = normalize_severity(severity)
                    if action == "KILL" and sev == "MEDIUM":
                        logger.info(
                            "Destructive action %s demoted to warn (MEDIUM severity) for %s",
                            action,
                            violation.get("resource_id"),
                        )
                    elif action == "KILL" and sev == "LOW":
                        logger.info(
                            "Destructive action %s demoted to warn (LOW severity) for %s",
                            action,
                            violation.get("resource_id"),
                        )
                    await handler.warn(violation["resource_id"], body)
            elif step == "kill":
                if not handler:
                    logger.warning(
                        "No handler for resource_type=%s; cannot kill for policy=%s",
                        violation.get("resource_type"),
                        violation.get("policy"),
                    )
                    executed_action = "error_no_handler"
                else:
                    logger.info(
                        "Executing KILL for resource=%s policy=%s severity=%s",
                        violation.get("resource_id"),
                        violation.get("policy"),
                        normalize_severity(severity),
                    )
                    await handler.kill(violation["resource_id"])
                    
            elif step == "certify":
                if not handler:
                    logger.warning(
                        "No handler for resource_type=%s; cannot certify for policy=%s",
                        violation.get("resource_type"),
                        violation.get("policy"),
                    )
                    executed_action = "error_no_handler"
                else:
                    if hasattr(handler, "certify"):
                        await handler.certify(violation["resource_id"])
                    else:
                        executed_action = "error_no_handler_method"
                        
            elif step == "uncertify":
                if not handler:
                    logger.warning(
                        "No handler for resource_type=%s; cannot uncertify for policy=%s",
                        violation.get("resource_type"),
                        violation.get("policy"),
                    )
                    executed_action = "error_no_handler"
                else:
                    if hasattr(handler, "uncertify"):
                        await handler.uncertify(violation["resource_id"])
                    else:
                        executed_action = "error_no_handler_method"
                        
            # Log to DB
            try:
                audit_record = EnforcementAuditModel(
                    id=str(uuid.uuid4()),
                    request_id=self.request.id,
                    resource_id=violation["resource_id"],
                    resource_type=violation.get("resource_type", "unknown"),
                    policy_name=violation.get("policy", "unknown"),
                    severity=normalize_severity(severity),
                    intended_action=intended,
                    executed_action=executed_action,
                    reason=violation.get("reason", "")
                )
                self.db.add(audit_record)
            except Exception as e:
                logger.error(f"Failed to log enforcement audit: {e}")
                
        # Commit all audit logs
        try:
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to commit enforcement audit logs: {e}")
            self.db.rollback()

        add_fact(self.db, self.request.id, "enforce_completed", {})
        self.finish_enforcing()

    async def on_enter_notifying_async(self):
        """Dispatch summary report to the notify target."""
        if self.has_fact("notify_completed"):
            return
            
        # Send email via NotificationProvider
        add_fact(self.db, self.request.id, "notify_completed", {})
        self.finish_notifying()

