# ATLAS Tools Inventory

This document outlines the tools available to the AI agent. **Tools are exclusively for information gathering and discovery.** 

The only exception is the `ExecuteWorkflowTool`, which is the mechanism for the agent to take action by invoking provider functions (see `PROVIDERS.md`).

---

## 1. The Action Tool

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `ExecuteWorkflowTool` | The "Special Tool" used to execute a plan. It takes a list of provider function calls (e.g., `CreateWorkspace`, `GrantAccess`) and executes them transactionally. | **Critical** | Action Execution |

---

## 2. User Onboarding & Verification Tools
Tools for verifying user status and entitlements.

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `CheckTrainingStatusTool` | Integrates with internal HR/Training systems to verify mandatory coursework. | **Critical** | Onboarding |
| `SearchUserEntitlementsTool` | Provides a comprehensive view of a user's current access across all Workspaces, Catalogs, and Schemas. | **Critical** | Discovery |
| `ListAvailableWorkspacesTool` | Lists all existing Databricks workspaces available for join requests. | **High** | Discovery |
| `ListAvailableDatasetsTool` | Lists high-level certified catalogs and schemas available for access. | **High** | Discovery |
| `CheckRequestHistoryTool` | Checks for pending or duplicate requests to prevent spam. | **High** | Governance |

---

## 3. Information & Discovery Tools
Tools to validate input and find resources.

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `CheckExistsTool` | Checks if a specific catalog, schema, or table exists. Supports fuzzy matching. | **Critical** | Validation |
| `SearchCatalogTool` | Searches Unity Catalog metadata for datasets matching keywords. | **High** | Discovery |
| `SuggestReusableAssetTool` | Matches user needs with existing templates or certified datasets. | **Deferred** | Innovation |
| `GetAccessRequestStatusTool` | Checks the status of an ongoing access request. | **High** | Operations |
| `GetWorkspaceStatusTool` | Polls the status of a provisioning operation. | **High** | Operations |

---

## 4. Governance & Automated Oversight Tools
These tools allow the Governance Agent to proactively monitor the Lakehouse for hygiene, security, and cost efficiency.

### Resource & Cost Optimization
| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `DetectOverProvisioningTool` | Analyzes cluster and warehouse metrics to identify resources with consistently low utilization relative to their instance size. | **High** | Cost |
| `IdentifyStaleAssetsTool` | Identifies tables, models, or jobs that have not been queried or run in X days ("Unused Data"). | **Medium** | Cost/Hygiene |
| `FindEmptyContainersTool` | Identifies Catalogs or Schemas that contain zero child objects (tables, views, volumes) to reduce namespace clutter. | **Low** | Hygiene |

### Security & Compliance Audit
| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `AuditPrivilegedAccessTool` | Scans Unity Catalog for principals with `Account Admin`, `Metastore Admin`, or broad `ALL PRIVILEGES` grants. | **Critical** | Security |
| `IdentifyOrphanedAssetsTool` | Validates asset ownership against the IDP to find resources owned by deleted users or service principals. | **High** | Security |
| `ReviewDataClassificationTool` | Scans schema metadata to identify columns likely containing PII that lack proper sensitivity tags. | **High** | Compliance |

### Data Quality & Usage
| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `AnalyzeAssetUsageTool` | Queries system tables to generate usage heatmaps, identifying frequently vs. rarely accessed assets. | **Medium** | Observability |
| `ScanUndocumentedAssetsTool` | Lists data assets (tables, columns) that are missing `COMMENT` fields to enforce documentation standards. | **Low** | Quality |
