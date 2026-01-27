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

## 4. Suggestions for Other Agents (Governance, FinOps)
Additional tools recommended for a large-scale enterprise environment.

| Tool Name | Description | Category | Target |
|-----------|-------------|----------|--------|
| `CheckBudgetStatusTool` | Checks if a cost center or workspace is projected to exceed its monthly budget. | FinOps | Cost Control |
| `ScanSecurityPostureTool` | Scans workspace for security violations (e.g., public clusters, unencrypted storage, over-privileged users). | Security | Compliance |
| `VerifyDataSLAComplianceTool` | Checks if critical tables have been updated within their expected SLA timeframe. | Observability | Data Quality |
| `AnalyzeClusterUsageTool` | Identifies underutilized or over-provisioned clusters and suggests instance type optimizations. | FinOps | Optimization |
| `FindStaleResourcesTool` | Scans for notebooks, tables, or jobs that haven't been accessed in X days to suggest archiving. | Governance | Cleanup |
| `SearchComplianceViolationsTool` | Scans metadata and table samples for potential PII violations in non-secure zones. | Security | Compliance |
