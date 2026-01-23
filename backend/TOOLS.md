# EDAS Hub Tools Inventory

This document outlines the tools required to support the self-service capabilities of the EDAS Hub. Tools are business operations that abstract infrastructure and system details by using providers.

---

## 1. User Onboarding & Verification Tools (Priority Focus)
These tools are essential for the initial user experience, ensuring users have completed required training and understand their current access landscape.

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `CheckTrainingStatusTool` | Integrates with internal HR/Training systems to verify mandatory coursework (e.g., Databricks 101, Data Privacy). | **Critical (Onboarding)** | Onboarding |
| `SearchUserEntitlementsTool` | Provides a comprehensive view of a user's current access across all Workspaces, Catalogs, and Schemas. | **Critical (Onboarding)** | Discovery |
| `ListAvailableWorkspacesTool` | Lists all existing Databricks workspaces available for join requests, including metadata like business unit and environment. | **High (Onboarding)** | Discovery |
| `ListAvailableDatasetsTool` | Lists high-level certified catalogs and schemas that are available for access requests. | **High (Onboarding)** | Discovery |
| `CheckRequestHistoryTool` | Checks if the user already has a pending or duplicate request for the same resource to prevent "spamming" approvers. | **High** | Governance |

---

## 2. Information & Discovery Tools
These tools are used by the AI agent to validate user input and help users find specific resources.

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `CheckExistsTool` | Checks if a specific catalog, schema, or table exists in Unity Catalog. Supports fuzzy matching for suggestions. | **Critical (MVP)** | Validation |
| `SearchCatalogTool` | Searches Unity Catalog metadata for datasets matching user's business description or keywords. | **High** | Discovery |
| `SuggestReusableAssetTool` | Matches user needs with existing templates or certified datasets from the community library. | **Deferred** | Innovation |

---

## 3. Access Management Tools (Existing Resources)
Tools for managing permissions on existing infrastructure.

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `GrantAccessTool` | Grants specific permissions (READ, WRITE, etc.) to a principal on a UC object. | **Critical (MVP)** | Data Access |
| `RevokeAccessTool` | Removes permissions from a principal on a resource. | **High** | Governance |
| `GetAccessRequestStatusTool` | Tracks the status of an access grant operation, especially if it involves manual approval steps. | **High** | Operations |

---

## 4. Provisioning Tools (New Resources)
Tools for creating new infrastructure components (PaaS and DaaS).

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `CreateWorkspaceTool` | Provisions a new Databricks workspace via Terraform. Handles VPC, IAM, and base config. | **Critical (MVP)** | Provisioning |
| `CreateCatalogTool` | Creates a new top-level catalog in Unity Catalog with specific ownership. | **Critical (MVP)** | Provisioning |
| `CreateSchemaTool` | Creates a schema within an existing catalog. | **Critical (MVP)** | Provisioning |
| `CreateTableTool` | Creates a table definition (DDL) within a schema. | **High** | Provisioning |
| `CreateServicePrincipalTool` | Creates a new Service Principal in the Databricks account for automation. | **Critical (MVP)** | SP Provisioning |
| `CreateAPIKeyTool` | Generates an API key/token for a service principal via Databricks. | **Critical (MVP)** | SP Provisioning |

---

## 5. DevOps & Lifecycle Tools
Tools for managing developer workflows and the decommissioning of resources.

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `ScaffoldGitHubRepoTool` | Creates a new GitHub repository from a standard Qualcomm template. | **High** | GitHub Repo |
| `SetupGitIntegrationTool` | Links a Databricks workspace/repo to a GitHub repository. | **High** | GitHub Repo |
| `DeleteWorkspaceTool` | Decommissions a workspace and cleans up associated cloud resources. | **Deferred** | Governance |
| `UpdateWorkspaceConfigTool` | Modifies existing workspace settings (e.g., increasing cluster limits, adding tags). | **Nice to Have** | Operations |
| `RotateAPIKeyTool` | Rotates secrets for an existing service principal. | **Nice to Have** | Security |

---

## 6. Communication & Workflow Tools
Supporting tools for the overall process flow and notifications.

| Tool Name | Description | Priority | Target |
|-----------|-------------|----------|--------|
| `SendNotificationTool` | Sends Email/Slack/Teams alerts for approvals, failures, or completions. | **Critical (MVP)** | All |
| `NotifyApproversTool` | Specifically routes approval requests to managers/data owners based on metadata. | **Critical (MVP)** | Approvals |
| `GetWorkspaceStatusTool` | Polls the status of a provisioning operation or health of an existing workspace. | **High** | All |

---

## Summary of Priority for Onboarding Phase
To support the current focus on user onboarding and basic access, the following tools are prioritized:
1.  **Verification**: `CheckTrainingStatusTool`, `SearchUserEntitlementsTool`
2.  **Discovery**: `ListAvailableWorkspacesTool`, `ListAvailableDatasetsTool`
3.  **Basic Access**: `GrantAccessTool`, `CheckExistsTool`
4.  **Workflow**: `SendNotificationTool`, `NotifyApproversTool`

---

## Onboarding Workflow & Data Integration
This section describes how the onboarding tools integrate with existing systems and data.

### Training Status Integration
The `CheckTrainingStatusTool` should validate against the mandatory paths defined in `app/content/training.json`.
*   **Fundamental Requirement**: All users must have completed "Databricks Fundamentals" (1 hr).
*   **Role-Based Requirements**:
    *   **Data Engineers**: Must complete "Unity Catalog" and "Data Ingestion" for WRITE access.
    *   **Data Scientists**: Must complete "ML Model Development" for ML platform access.
    *   **Admins**: Must complete "Platform Administrator Learning Plan".

### Discovery & Entitlements
*   **Workspace Discovery**: `ListAvailableWorkspacesTool` should pull from the Terraform provider's state or a central workspace registry.
*   **Dataset Discovery**: `ListAvailableDatasetsTool` should leverage Unity Catalog's `INFORMATION_SCHEMA` to find "Certified" or "Public" catalogs.
*   **Entitlements**: `SearchUserEntitlementsTool` must aggregate permissions from:
    1.  Unity Catalog (Grants)
    2.  Databricks Account (Group Memberships)
    3.  Qualcomm IDP (Active Directory Groups)

### "Basic Access" Definition
Basic access for a new user typically includes:
1.  **Read-only access** to the `common` or `public` catalogs.
2.  **User-level access** to a "Sandbox" or "Community" workspace.
3.  **Entitlement to join** a specific project workspace based on their cost center/department.
