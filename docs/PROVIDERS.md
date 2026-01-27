# ATLAS Provider Functions

This document outlines the provider functions that perform actions and state changes in the infrastructure. These functions are invoked by the agent via the `ExecuteWorkflow` tool.

---

## 1. TerraformProvider
Manages Infrastructure-as-Code (IaC) for workspaces, unity catalog objects, and access control.

| Function Name | Description | Target |
|---------------|-------------|--------|
| `CreateWorkspace` | Provisions a new Databricks workspace via Terraform. Handles VPC, IAM, and base config. | Provisioning |
| `DeleteWorkspace` | Decommissions a workspace and cleans up associated cloud resources. | Governance |
| `UpdateWorkspaceConfig` | Modifies existing workspace settings (e.g., increasing cluster limits, adding tags). | Operations |
| `CreateServicePrincipal` | Creates a new Service Principal in the Databricks account for automation. | SP Provisioning |
| `CreateCatalog` | Creates a new top-level catalog in Unity Catalog with specific ownership. | Provisioning |
| `CreateSchema` | Creates a schema within an existing catalog. | Provisioning |
| `CreateTable` | Creates a table definition (DDL) within a schema. | Provisioning |
| `GrantAccess` | Grants specific permissions (READ, WRITE, etc.) to a principal on a UC object. | Data Access |
| `RevokeAccess` | Removes permissions from a principal on a resource. | Governance |
| `CreateAPIKey` | Generates an API key/token for a service principal. | SP Provisioning |
| `RotateAPIKey` | Rotates secrets for an existing service principal. | Security |

## 2. MicrosoftProvider
Manages Azure/Entra ID resources and licensing.

| Function Name | Description | Target |
|---------------|-------------|--------|
| *None currently exposed* | | |

## 3. NotificationProvider
Handles communication via Email, Teams, and Slack.

| Function Name | Description | Target |
|---------------|-------------|--------|
| `SendNotification` | Sends Email/Slack/Teams alerts for approvals, failures, or completions. | All |
| `NotifyApprovers` | Specifically routes approval requests to managers/data owners. | Approvals |

## 4. DatabricksProvider
Executes SQL queries to metadata checks. Primarily used by Read-Only tools.

| Function Name | Description | Target |
|---------------|-------------|--------|
| `ExecuteSQL` | Executes a SQL query against a warehouse (used internally by tools, rarely by workflows). | Validation |

## 5. TrainingProvider
Integrates with Learning Management Systems (LMS).

| Function Name | Description | Target |
|---------------|-------------|--------|
| *None currently exposed* | (Read-only verification handled by `CheckTrainingStatusTool`) | |

## 6. IdpProvider
Manages Identity Provider users and groups.

| Function Name | Description | Target |
|---------------|-------------|--------|
| `CreateGroup` | Creates a new group in the IDP. | Identity |
| `AddToGroup` | Adds a user to a specific group. | Identity |

## 7. GithubProvider
Manages source control repositories and CI/CD integration.

| Function Name | Description | Target |
|---------------|-------------|--------|
| `ScaffoldGitHubRepo` | Creates a new GitHub repository from a standard Qualcomm template. | GitHub Repo |
| `SetupGitIntegration` | Links a Databricks workspace/repo to a GitHub repository. | GitHub Repo |
