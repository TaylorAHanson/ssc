# CI/CD Setup Guide

This document explains how to configure GitHub Actions for automated deployments.

## Branch Strategy (GitFlow)

```
main (protected)         ← Production deployments
  ↑
develop (protected)      ← Development deployments
  ↑
feature/*, hotfix/*, bugfix/*  ← Developer branches
```

### Branch Rules

| Branch | Protection | Deployment |
|--------|------------|------------|
| `main` | Requires PR, approvals | Production |
| `develop` | Requires PR | Development |
| `feature/*` | None | None (CI only) |
| `hotfix/*` | None | None (CI only) |
| `bugfix/*` | None | None (CI only) |

## GitHub Environments

Create two environments in GitHub (Settings → Environments):

### 1. `development`
Used for develop branch deployments.

### 2. `production`
Used for main branch deployments.
- Optional: Add required reviewers for approval before deployment

## Required Secrets

Configure these secrets for **each environment** (Settings → Environments → [env] → Secrets):

| Secret | Description | Example |
|--------|-------------|---------|
| `DATABRICKS_HOST` | Workspace URL | `https://your-workspace.cloud.databricks.com` |
| `DATABRICKS_TOKEN` | PAT or OAuth token | `dapi...` |
| `DATABRICKS_USER` | Your Databricks email | `user@company.com` |
| `DATABRICKS_WAREHOUSE_ID` | SQL Warehouse ID | `abc123def456` |
| `DATABASE_HOST` | Lakebase host | `instance-xxx.database.cloud.databricks.com` |
| `DATABASE_USER` | Service principal ID | `37734a7f-33d9-4355-...` |
| `DATABASE_INSTANCE_NAME` | Lakebase instance name | `edas-backend` |

### Getting the Values

#### DATABRICKS_TOKEN
```bash
# Generate a PAT in Databricks UI: User Settings → Developer → Access Tokens
# Or use service principal OAuth (recommended for production)
```

#### DATABRICKS_WAREHOUSE_ID
```bash
# Find in Databricks UI: SQL → SQL Warehouses → [warehouse] → Connection details
```

#### DATABASE_USER (Service Principal ID)
```bash
# This is the app's service principal client ID
# Get it from: databricks apps get <app-name> --output json | jq -r '.service_principal_client_id'
```

## Workflow Files

| File | Trigger | Description |
|------|---------|-------------|
| `.github/workflows/ci.yml` | PRs, feature branches | Lint, type-check, build, test |
| `.github/workflows/deploy-develop.yml` | Push to develop | Deploy to dev environment |
| `.github/workflows/deploy-main.yml` | Push to main | Deploy to production |

## Setting Up Branch Protection

### For `develop` branch:
1. Go to Settings → Branches → Add rule
2. Branch name pattern: `develop`
3. Enable:
   - ✅ Require a pull request before merging
   - ✅ Require status checks to pass before merging
   - ✅ Require branches to be up to date before merging
4. Select required status checks:
   - `Frontend Lint & Build`
   - `Backend Lint & Test`

### For `main` branch:
1. Same as above, plus:
   - ✅ Require approvals (set to 1 or more)
   - ✅ Dismiss stale pull request approvals when new commits are pushed

## Workflow Diagram

```
Developer pushes to feature/xyz
         │
         ▼
    ┌─────────┐
    │   CI    │  ← Lint, build, test
    └────┬────┘
         │
         ▼
Developer creates PR to develop
         │
         ▼
    ┌─────────┐
    │   CI    │  ← Runs again on PR
    └────┬────┘
         │
    PR approved & merged
         │
         ▼
    ┌─────────────────┐
    │ Deploy to Dev   │  ← Automatic on merge
    └────────┬────────┘
         │
         ▼
    edas-hub-dev deployed
         │
         │  (after testing in dev)
         │
Developer creates PR: develop → main
         │
         ▼
    ┌─────────┐
    │   CI    │
    └────┬────┘
         │
    PR approved & merged
         │
         ▼
    ┌─────────────────┐
    │ Deploy to Prod  │  ← May require approval
    └────────┬────────┘
         │
         ▼
    edas-hub deployed (production)
```

## First-Time Setup Checklist

- [ ] Create GitHub repository
- [ ] Create `development` environment in GitHub
- [ ] Create `production` environment in GitHub
- [ ] Add secrets to `development` environment
- [ ] Add secrets to `production` environment
- [ ] Set up branch protection for `develop`
- [ ] Set up branch protection for `main`
- [ ] Create Databricks Apps in each workspace (or let workflow create them)
- [ ] Create Lakebase instances in each workspace
- [ ] Create service principal roles in each Lakebase instance
- [ ] Grant CAN USE on Lakebase to each app's service principal

## Troubleshooting

### Deployment fails with "App not found"
The workflow will auto-create the app. Ensure the token has permissions to create apps.

### OAuth token generation fails
Ensure the app's service principal:
1. Has CAN USE on the Lakebase instance
2. Has a Postgres role created via `databricks_create_role()`

### Build fails
Check Node.js version (should be 20) and Python version (should be 3.11).
