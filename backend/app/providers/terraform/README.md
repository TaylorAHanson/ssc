# Terraform Provider (GitOps Pattern)

## Overview
This provider implements a **GitOps** pattern for infrastructure provisioning. Unlike the standard direct-execution pattern (where the worker runs `terraform apply`), this provider acts as a bridge to an external Git repository. An external CI/CD system (e.g., GitHub Actions, GitLab CI) is responsible for the actual `terraform apply` execution.

This approach aligns with the system's **Async Processing** and **State Machine** architecture by decoupling the "request" for infrastructure from the "execution" of it, treating the Git commit as the actuation event. It also keeps terraform out of the backend codebase.

## Integration with Architecture

### 1. State Machine Layer
The **State Machines** (e.g., `WorkspaceProvisionStateMachine`) remain the orchestrators. They call the `TerraformProvider` directly.
*   **Action**: Instead of blocking on a 20-minute `apply`, the State Machine calls `provider.apply()`, which now returns quickly after pushing a commit.
*   **State Transition**: The State Machine transitions to a `provisioning` state (or `git_push_complete`) and waits.

### 2. Provider Layer
The `TerraformProvider` abstracts the Git interaction.
*   **`apply(config)`**: 
    1.  Clones/Pulls the configuration repo.
    2.  Reads the target YAML files (e.g., `resources/<workspace>/catalogs.yaml`).
    3.  Modifies them based on the request.
    4.  Commits and Pushes to specific branch.
    5.  Returns the **Commit SHA** as the "Resource ID".

### 3. Feedback Loop (Workers/API)
The system needs to know when the external `terraform apply` succeeds.
*   **Mechanism**: **Webhook / Callback** (Recommended).
*   **Flow**:
    1.  External CI runs `terraform apply`.
    2.  On success/failure, CI invokes the backend API: `POST /api/v1/callbacks/terraform/{request_id}`.
    3.  **Polling Worker**: Alternatively, if webhooks aren't possible, the standard Polling Worker can check the CI build status using the `GithubProvider`.

## Workflow (Example: Create Catalog)

1.  **State Machine** requests `create_catalog`.
2.  **Provider** checks out `main` (or feature branch).
3.  **Provider** reads `resources/<workspace_id>/catalogs.yml` (or creates it if it doesn't exist).
4.  **Provider** adds the new catalog definition.
5.  **Provider** commits: "Ops: Create catalog 'sales_dev' (Req: 123)".
6.  **Provider** pushes to remote.
7.  **External CI** detects change -> Runs Terraform.
8.  **External CI** reports status back to Backend.
9.  **State Machine** advances to `active` or `failed`.

## Design Decisions & TODOs

### 1. Atomicity & Concurrency
*Problem*: Multiple State Machines modifying `catalogs.yml` concurrently.
*   **Architecture Alignment**: The `Workers` layer uses **Database Locking** (Line 630 in ARCHITECTURE.md) to process requests serialized per-request-ID, but we need serialization *per-resource-file* in Git.
*   **Solution**: 
    *   **Optimistic Locking**: Provider checks `git rev-parse HEAD` before push. If changed, rebase and retry (handled by `tenacity` retry logic in `client.py`).

### 2. Schema Strategy
*Problem*: Monolithic `catalogs.yml` vs split files.
*   **Recommendation**: **Split Files** (`resources/<workspace_id>/*.yaml`).
    *   Reduces git conflicts (Atomicity).
    *    aligns with **State Machine** encapsulation (one SM operates on one Workspace usually).
    *   "Supply Chain" view can be aggregated by the CI system or a separate tool.

### 3. Information Feedback
*Problem*: How to get outputs (e.g., `workspace_url`) back?
*   **Solution**: 
    *   **State File**: CI parses `terraform.tfstate` or `terraform output` and sends the JSON payload to the Callback API.
    *   **Backend Storage**: CI writes `outputs.json` to the git repo. Provider pulls and reads it (slower). -> **Prefer Callback**.
