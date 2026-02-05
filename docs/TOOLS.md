# Tools Inventory

This document outlines the tools available to the AI agent, categorized by functional mode. Tools are primarily for **information gathering and discovery**, with the exception of the `execute_workflow` tool which executes actions.

---

## 1. Core Action Tool
| Tool Name | Description | Status | UI Intent / Trigger |
|-----------|-------------|--------|---------------------|
| `execute_workflow` | Primary mechanism to invoke provider functions (e.g., Create Workspace). | [x] Implemented | "Shall I proceed with this request?" |

---

## 2. Self-Service (Standard Mode)
| Tool Name | Description | Status | UI Hint / Intent |
|-----------|-------------|--------|---------------------|
| `does_catalog_exist` | Checks if a specific catalog, schema, or table exists. | [x] Implemented | Validation logic |
| `get_catalog_list` | Lists all catalogs available to the user. | [x] Implemented | "Discover Enterprise Data" |
| `get_schema_list` | Lists schemas within a catalog. | [x] Implemented | "Discover Enterprise Data" |
| `get_table_list` | Lists tables within a schema. | [x] Implemented | "Discover Enterprise Data" |
| `search_requests` | Searches for current or past user requests. | [x] Implemented | "Check request status" |
| `search_approvals` | Searches for pending approvals for the user. | [x] Implemented | Menu: Pending Approvals |
| `search_user_entitlements` | Views a user's access across workspaces and data. | [x] Implemented | "Search user permissions" |
| `check_training_status` | Verifies mandatory coursework completion. | [x] Implemented | "Learn a new skill" |
| `list_github_templates` | Lists available GitHub repository templates. | [x] Implemented | "Request GitHub repo" |
| `check_github_repo` | Checks if a GitHub repository exists. | [x] Implemented | "Request GitHub repo" |
| `search_events` | Finds community events or office hours. | [x] Implemented | Menu: Event Calendar |

---

## 3. FinOps (Finance Admin)
| Tool Name | Description | Status | UI Hint / Intent |
|-----------|-------------|--------|---------------------|
| `get_cost_summary` | Provides a summary of spend by department or workspace. | [x] Implemented | "Department Billing" |
| `get_resource_efficiency_metrics` | Analyzes cluster utilization and overprovisioning. | [x] Implemented | "Idle Clusters" |
| `get_forecasted_spend` | Predicts future spend based on historics. | [x] Implemented | "Usage Forecast" |
| `get_forecasted_spend` | Predicts future spend. | [x] Implemented | "Forecast Next Month" |
| `get_cost_summary` | Summary of spend by cost center. | [x] Implemented | "Department Billing" |

---

## 4. Governance (Security Admin)
| Tool Name | Description | Status | UI Hint / Intent |
|-----------|-------------|--------|---------------------|
| `check_object_permissions` | Audits access to specific catalogs or schemas. | [x] Implemented | "Access Report" |
| `audit_user_access` | Audits all access grants for a specific principal. | [x] Implemented | "Audit permissions" |
| `search_audit_logs` | Searches Unity Catalog audit logs. | [x] Implemented | "Usage Audit" |
| `check_overprovisioned_users` | Identifies users with excessive privileges. | [x] Implemented | "Overprovisioned users" |
| `list_workspaces` | Lists all available Databricks workspaces. | [x] Implemented | "Workspace Access" |
| `find_owner` | Locates the owner of a specific data asset. | [x] Implemented | "Assign Owner" |
| `search_audit_logs` | Queries login events and admin actions. | [x] Implemented | "Count Failed Logins", "Unique Users" |

---

## 5. Data Quality
TODO
