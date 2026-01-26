# Terraform Provider (GitOps Pattern)

## Overview
This provider implements a **GitOps** pattern for infrastructure provisioning. Unlike a direct-execution model where the backend runs Terraform directly, this provider acts as an automation bridge to an external Infrastructure-as-Code (IaC) Git repository. 

The core principle is **Isolation through Branching**: every request gets its own branch, allowing for independent `terraform plan` execution and review before merging to `main` for a final `terraform apply`.

## Architecture Flow

```mermaid
sequenceDiagram
    participant Admin as Platform Admin (UI)
    participant SM as State Machine
    participant TP as Terraform Provider
    participant Git as Git Repo (Infra)
    participant CI as CI/CD (GitHub Actions)

    Note over SM: State: terraform_planning
    SM->>TP: plan(config)
    TP->>Git: Create branch 'request/{id}'
    TP->>Git: Commit YAML changes
    TP->>Git: Push branch
    Git->>CI: Trigger Plan Workflow
    CI->>CI: terraform plan
    CI->>SM: POST /api/callbacks/plan (Send summary)
    
    Note over SM: State: awaiting_approval
    SM-->>Admin: Show Plan in UI
    Admin->>SM: Approve Request
    
    Note over SM: State: terraform_applying
    SM->>TP: apply()
    TP->>Git: Merge 'request/{id}' to 'main'
    Git->>CI: Trigger Apply Workflow
    CI->>CI: terraform apply
    CI->>SM: POST /api/callbacks/apply (Send status/outputs)
    
    Note over SM: State: active/completed
```

## Key Components

### 1. State Machine Integration
State machines using this provider must handle the following sequence:

1.  **`terraform_planning`**: The SM calls `provisioner.plan()`. This is an async state. The SM waits for a callback from the CI system containing the plan result.
2.  **`awaiting_approval`**: Once the plan is received, the SM transitions here. This is a "Human-in-the-loop" state where a Platform Admin reviews the plan in the UI.
3.  **`terraform_applying`**: Upon approval, the SM calls `provisioner.apply()`. This state triggers the merge to `main`. The SM waits for a final callback confirming execution.

### 2. Provider Responsibilities
The `TerraformProvider` handles the low-level Git mechanics:
- **`plan(request_id, target_file, content)`**:
    - Switches to a new branch: `request/{request_id}`.
    - Updates YAML files with the desired state.
    - Pushes the branch to trigger remote CI.
- **`apply(request_id)`**:
    - Merges the request branch into the target branch (usually `main`).
    - Pushes the merge to trigger remote execution.

### 3. CI/CD Requirements
The external repository must host two primary workflows:

#### **Plan Workflow** (`plan.yml`)
- **Trigger**: `on: push: branches: ["request/*"]`
- **Commands**:
    ```bash
    terraform init
    terraform plan -no-color > plan_output.txt
    ```
- **Callback**: Send `plan_output.txt` to the Backend callback API.

#### **Apply Workflow** (`apply.yml`)
- **Trigger**: `on: push: branches: ["main"]`
- **Commands**:
    ```bash
    terraform init
    terraform apply -auto-approve
    ```
- **Callback**: Send success/failure and any `terraform output -json` to the Backend callback API.

## Implementation Details

### Branch Management
By using `request/{request_id}` branches, we ensure:
- **Zero Conflicts**: Multiple requests can be in the "Planning" phase simultaneously.
- **Isolation**: Each plan is specific to the changes in its branch.
- **Auditability**: Every infrastructure change is backed by a Git commit and a recorded plan.
- **Cleanup**: Once merged and applied, the request branch can be safely deleted.

### Environment & Directory Structure
The provider assumes a convention-based directory structure in the IaC repo:
```text
infrastructure-repo/
├── environments/
│   ├── dev/
│   ├── prod/
│   └── common.tf
├── resources/
│   ├── workspace-a/
│   │   ├── catalogs.yaml
│   │   └── unity_catalog.tf
│   └── workspace-b/
└── ...
```
The `TerraformProvider` will primarily interact with the YAML files in the `resources/` directory.

### The Callback API
The Backend provides a centralized callback endpoint:
`POST /api/v1/callbacks/terraform/{request_id}`

The payload structure:
```json
{
  "action": "plan" | "apply",
  "status": "success" | "failure",
  "summary": "Terraform plan details or apply logs",
  "outputs": {
    "workspace_url": "https://...",
    "metastore_id": "..."
  },
  "error": "Error details if status is failure"
}
```
This callback updates the **Facts** for the request, which in turn triggers the next state transition in the State Machine.
