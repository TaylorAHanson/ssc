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

## Required Secrets & Variables

Configure these for **each environment** (Settings → Environments → [env]):

### Required (for deployment to work)

| Name | Type | Description | Example |
|------|------|-------------|---------|
| `DATABRICKS_HOST` | Variable | Workspace URL | `https://your-workspace.cloud.databricks.com` |
| `DATABRICKS_CLIENT_ID` | **Secret** | Service Principal Client ID | `00000000-0000-0000-0000-000000000000` |
| `DATABRICKS_CLIENT_SECRET` | **Secret** | Service Principal Secret | `dose...` |
| `DATABRICKS_WAREHOUSE_ID` | Variable | SQL Warehouse ID | `abc123def456` |

### Optional (for full app functionality)

| Name | Type | Description | Default |
|------|------|-------------|---------|
| `MODEL_SERVING_AGENT_LLM_ENDPOINT` | Variable | LLM endpoint name | `databricks-gemini-3-flash` |
| `MODEL_SERVING_CLASSIFIER_ENDPOINT` | Variable | Classifier endpoint name | (empty) |
| `GITHUB_ORG` | Variable | GitHub organization name | (empty) |
| `APP_GITHUB_TOKEN` | **Secret** | GitHub PAT for app operations | (empty) |

> **Note:** The app uses OAuth automatically inside Databricks Apps, so `DATABRICKS_TOKEN` and `MODEL_SERVING_API_KEY` are **not needed**.

### Getting the Values

#### DATABRICKS_HOST
Your Databricks workspace URL:
```
https://your-workspace.cloud.databricks.com
```

#### DATABRICKS_CLIENT_ID & DATABRICKS_CLIENT_SECRET
Create a Service Principal for CI/CD:

1. Go to **Account Console** → **User management** → **Service principals**
2. Click **Add service principal**
3. Name it: `github-actions-deployer`
4. Generate an OAuth secret:
   - Click on the service principal
   - Go to **Secrets** tab
   - Click **Generate secret**
   - Copy the **Client ID** and **Secret** (secret is only shown once!)

5. Grant workspace access:
   - Go to your Workspace → **Settings** → **Identity and access**
   - Add the service principal with **Admin** role (needed to deploy apps)

#### DATABRICKS_WAREHOUSE_ID
Find in Databricks UI:
1. Go to **SQL** → **SQL Warehouses**
2. Click on your warehouse
3. Copy the **ID** from the connection details

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
- [ ] Create Service Principal for CI/CD deployments
- [ ] Grant Service Principal admin access to workspace
- [ ] Create `development` environment in GitHub
- [ ] Create `production` environment in GitHub
- [ ] Add secrets to `development` environment
- [ ] Add secrets to `production` environment
- [ ] Set up branch protection for `develop`
- [ ] Set up branch protection for `main`
- [ ] Test deployment with a PR to develop

## Troubleshooting

### Deployment fails with "Authentication failed"
Ensure:
1. `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` are set correctly
2. The Service Principal has workspace access
3. The OAuth secret hasn't expired

### Deployment fails with "Permission denied"
The Service Principal needs:
1. Workspace Admin role (to create/deploy apps)
2. CAN MANAGE permissions on the target workspace path

### Deployment fails with "App not found"
The workflow will auto-create the app. Ensure the Service Principal has permissions to create apps.

### Build fails
Check Node.js version (should be 20) and Python version (should be 3.11).

## Security Best Practices

1. **Use separate Service Principals** for development and production environments
2. **Rotate secrets regularly** - OAuth secrets should be rotated periodically
3. **Use environment protection rules** - Require approval for production deployments
4. **Limit Service Principal permissions** - Only grant what's needed
