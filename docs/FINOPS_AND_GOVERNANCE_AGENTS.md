# FinOps & Governance Agents Design

This document outlines the design for the **FinOps** and **Governance** agents. These agents are strictly **informational**, focusing on retrieving data from Databricks system tables to answer user queries about costs, usage, permissions, and compliance.

## Service Principal Requirements

The Service Principal (SP) used by the backend requires specific permissions to access the system tables.

### Required Permissions
The SP must have `USE CATALOG`, `USE SCHEMA`, and `SELECT` permissions on the relevant system schemas.

```sql
-- Grant access to System Catalog
GRANT USE CATALOG ON CATALOG system TO `[SERVICE_PRINCIPAL_APP_ID]`;

-- FinOps Permissions
GRANT USE SCHEMA ON SCHEMA system.billing TO `[SERVICE_PRINCIPAL_APP_ID]`;
GRANT SELECT ON ALL TABLES IN SCHEMA system.billing TO `[SERVICE_PRINCIPAL_APP_ID]`;
GRANT USE SCHEMA ON SCHEMA system.compute TO `[SERVICE_PRINCIPAL_APP_ID]`;
GRANT SELECT ON ALL TABLES IN SCHEMA system.compute TO `[SERVICE_PRINCIPAL_APP_ID]`;

-- Governance Permissions
GRANT USE SCHEMA ON SCHEMA system.information_schema TO `[SERVICE_PRINCIPAL_APP_ID]`;
GRANT SELECT ON ALL TABLES IN SCHEMA system.information_schema TO `[SERVICE_PRINCIPAL_APP_ID]`;
GRANT USE SCHEMA ON SCHEMA system.access TO `[SERVICE_PRINCIPAL_APP_ID]`;
GRANT SELECT ON ALL TABLES IN SCHEMA system.access TO `[SERVICE_PRINCIPAL_APP_ID]`;
```

---

## 1. FinOps Agent

**Goal**: Provide visibility into Databricks spend, resource usage, and efficiency.

### Supported Use Cases (UI Prompts)
- "Which workspaces are the most expensive?"
- "Which users are out of compliance with the tagging policy?"
- "Show monthly cost trend by department"
- "Identify idle clusters that can be terminated"
- "What is my predicted spend for next month?"
- "Show me the cost breakdown by department"
- "Show me my spot instance savings report"

### Tools Design

#### `get_cost_summary`
**Description**: Retrieves aggregated cost data over a specified time range, optionally grouped by dimension.
**SQL Source**: `system.billing.usage`
**Parameters**:
- `start_date`, `end_date` (YYYY-MM-DD)
- `granularity`: `daily`, `monthly`, `total`
- `group_by`: `workspace_id`, `sku_name`, `usage_type`, `custom_tags.[key]` (e.g. `custom_tags.CostCenter`)

#### `get_resource_efficiency_metrics`
**Description**: Identifies potentially inefficient resources, such as idle clusters or low-utilization jobs.
**SQL Source**: `system.compute.clusters`, `system.billing.usage`
**Parameters**:
- `metric`: `idle_time`, `low_utilization`
- `threshold_hours`: Minimum hours to consider (e.g., >24h idle)

#### `check_tagging_compliance`
**Description**: Identifies resources (clusters, warehouses, jobs) that are missing required tags.
**SQL Source**: `system.compute.clusters`, `system.billing.usage` (distinct custom_tags keys)
**Parameters**:
- `required_tags`: List of tag keys that must be present (e.g., `["CostCenter", "Project"]`)

#### `get_forecasted_spend` (Simple Projection)
**Description**: Projects future spend based on historical average.
**Logic**: Calculates daily average over last 30 days * remaining days in month.

---

## 2. Governance Agent

**Goal**: Ensure security compliance, audit access controls, and validate resource configuration.

### Supported Use Cases (UI Prompts + Leader Feedback)
- "Which users are overprovisioned?" (Access to too many things / unused access)
- "Who has workspace admin?"
- "Audit recent permission changes in the last 7 days"
- "Show me an access report for my production data"
- "Audit administrative actions in my workspace"
- "I need to assign a new owner to a catalog" (Orphaned ownership)
- "I need to classify sensitive data" (Classification review)
- "Find empty catalogs"
- "Find unused data"

### Tools Design

#### `check_object_permissions`
**Description**: Lists all grants on a specific object.
**SQL Source**: `system.information_schema.table_privileges`, etc.
**Parameters**: `object_type`, `object_name`

#### `audit_user_access`
**Description**: Lists all effective permissions held by a user/group.
**SQL Source**: `system.information_schema` views (filtered by grantee)
**Parameters**: `principal_email`

#### `search_audit_logs`
**Description**: Searches audit logs for specific actions.
**SQL Source**: `system.access.audit`
**Parameters**: `action_name`, `actor_email`, `days_back`, `target_object`

#### `check_overprovisioned_users`
**Description**: Identifies users with high privilege counts or admin roles (Workspace Admin, Account Admin).
**SQL Source**: `system.access.audit` (admin actions), `system.information_schema` (broad grants like ALL PRIVILEGES)

#### `check_orphaned_assets`
**Description**: Finds assets owned by users who no longer exist or are inactive (requires joining with IDP data or list of active users, or simply checking if owner is in a "deleted" state if tracked).
**SQL Source**: `system.information_schema.tables`, `catalogs`, `schemas`

#### `check_asset_quality`
**Description**: Checks for "data hygiene" issues: missing comments, empty catalogs/schemas, or unused tables.
**SQL Source**: 
- `system.information_schema.tables` (comment IS NULL)
- `system.billing.usage` (join to find tables with 0 usage in X days)
**Parameters**:
- `check_type`: `missing_description`, `unused_assets`, `empty_containers`

#### `check_data_classification`
**Description**: Reports on columns/tables with specific classification tags (e.g., PII).
**SQL Source**: `system.information_schema.column_tags`

---

## Implementation Plan

1.  **Verify System Table Access**: Ensure the backend Service Principal has the permissions.
2.  **Create Directory Structure**:
    -   `backend/app/tools/finops/`
    -   `backend/app/tools/governance/`
3.  **Implement Base Classes**: Shared logic for Databricks connection.
4.  **Implement Tools**: One file per tool (or grouped logically).
5.  **Register Tools**: Add to agent config.
