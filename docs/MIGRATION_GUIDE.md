# ATLAS App Migration Guide

## Overview
Migrate ATLAS app from Sandbox workspace to Stable workspace.

---

## Pre-Migration Checklist

### Source Workspace Info (Sandbox)
- Workspace URL: `fe-sandbox-rohan-ahire-serverless-ws.cloud.databricks.com`
- App Name: `edas-hub-dev`

### Target Workspace Info (Stable)
- Workspace URL: `__NEW_WORKSPACE_URL__`
- App Name: `edas-hub-dev` (or new name)

---

## Step 1: Configure Databricks CLI Profiles

```bash
# Add new workspace profile
databricks configure --profile stable

# Enter:
# - Host: https://__NEW_WORKSPACE_URL__
# - Token: <your PAT for new workspace>

# Verify both profiles work
databricks workspace list / --profile serverless  # old
databricks workspace list / --profile stable      # new
```

---

## Step 2: Create Secret Scopes and Secrets

### 2.1 Create Secret Scope
```bash
databricks secrets create-scope atlas-hub --profile stable
```

### 2.2 Copy Secrets from Old Workspace
You'll need to re-enter secret values (can't export them):

```bash
# GitHub PAT (for git operations)
databricks secrets put-secret atlas-hub github-pat --profile stable
# Enter your GitHub PAT when prompted

# GitHub App Private Key (optional, blocked by IP allowlist)
databricks secrets put-secret atlas-hub github-app-private-key --profile stable --file ~/Downloads/atlas-infra-bot.2026-01-31.private-key.pem

# Lakebase Password
databricks secrets put-secret atlas-hub lakebase-password --profile stable
# Enter the Lakebase password when prompted
```

### 2.3 Verify Secrets
```bash
databricks secrets list-secrets atlas-hub --profile stable
```

Expected output:
```
github-pat
github-app-private-key
lakebase-password
```

---

## Step 3: Grant Secret ACLs

```bash
# Grant to all users (or specific users)
databricks secrets put-acl atlas-hub users READ --profile stable
```

---

## Step 4: Deploy the App

### 4.1 Update deploy.sh or CI/CD

If using the deploy script, update the profile:
```bash
# In deploy.sh, change profile to 'stable' or set env var
export DATABRICKS_CONFIG_PROFILE=stable
```

### 4.2 Manual Deployment
```bash
cd /Users/rohan.ahire/Documents/projects/fe-agentic-self-service

# Deploy backend code to workspace
databricks workspace import-dir ./backend /Workspace/Shared/apps/edas-hub-dev --overwrite --profile stable

# Deploy or update the app
databricks apps deploy edas-hub-dev --source-code-path /Workspace/Shared/apps/edas-hub-dev --profile stable
```

### 4.3 If App Doesn't Exist, Create It
```bash
databricks apps create edas-hub-dev \
  --description "EDAS Hub - Development Environment" \
  --profile stable
```

---

## Step 5: Grant App Service Principal Permissions

### 5.1 Get App Service Principal ID
```bash
databricks apps get edas-hub-dev --profile stable | grep service_principal_client_id
```

### 5.2 Grant Secret Scope Access to App SP
```bash
# Replace <APP_SP_ID> with the actual ID from above
databricks secrets put-acl atlas-hub <APP_SP_ID> READ --profile stable
```

---

## Step 6: Verify Lakebase Connectivity

The Lakebase instance is shared across workspaces (if same account). Verify:

1. The `DATABASE_HOST` in `app.yaml` is correct
2. The app can connect using `edas_app` role

If Lakebase is workspace-specific, you'll need to:
- Create new Lakebase instance
- Run schema migrations
- Migrate data

---

## Step 7: Update GitHub Actions (CI/CD)

### 7.1 Update Secrets in GitHub Repository

Go to: `https://github.com/databricks-field-eng/fe-agentic-self-service/settings/secrets/actions`

Update these secrets:
- `DATABRICKS_HOST` → New workspace URL
- `DATABRICKS_TOKEN` → New PAT for stable workspace

Or create environment-specific secrets for staging/prod.

### 7.2 Update Workflow Files (if needed)

Check `.github/workflows/deploy-develop.yml` for any hardcoded workspace URLs.

---

## Step 8: Test the Deployment

### 8.1 Check App Status
```bash
databricks apps get edas-hub-dev --profile stable
```

### 8.2 Check App Logs
```bash
# Via Databricks UI: Apps → edas-hub-dev → Logs
```

### 8.3 Test Endpoints
```bash
# Get app URL
APP_URL=$(databricks apps get edas-hub-dev --profile stable | jq -r '.url')

# Test health
curl $APP_URL/api/v1/health

# Test branding
curl $APP_URL/api/v1/branding
```

---

## Step 9: Test GitOps Integration

1. Open the app UI
2. Request a new schema via chat
3. Verify logs show:
   - `Fetched GitHub PAT from secrets/atlas-hub/github-pat`
   - Successful git clone (no IP allowlist error)
   - PR created in terraform repo

---

## Rollback Plan

If migration fails:
1. Keep old workspace running until new one is verified
2. Switch DNS/URLs back to old workspace
3. Debug issues in new workspace

---

## Quick Reference - All Commands

```bash
# Profile setup
databricks configure --profile stable

# Secret scope
databricks secrets create-scope atlas-hub --profile stable
databricks secrets put-secret atlas-hub github-pat --profile stable
databricks secrets put-secret atlas-hub lakebase-password --profile stable
databricks secrets put-acl atlas-hub users READ --profile stable

# App deployment
databricks workspace import-dir ./backend /Workspace/Shared/apps/edas-hub-dev --overwrite --profile stable
databricks apps create edas-hub-dev --description "ATLAS Hub" --profile stable
databricks apps deploy edas-hub-dev --source-code-path /Workspace/Shared/apps/edas-hub-dev --profile stable

# App SP permissions
APP_SP=$(databricks apps get edas-hub-dev --profile stable | jq -r '.service_principal_client_id')
databricks secrets put-acl atlas-hub $APP_SP READ --profile stable

# Verification
databricks apps get edas-hub-dev --profile stable
databricks secrets list-secrets atlas-hub --profile stable
databricks secrets list-acls atlas-hub --profile stable
```

---

## Notes

- Lakebase: If same account, the Lakebase instance should be accessible from both workspaces
- GitHub: Stable workspace should have IPs already allowlisted in EMU
- Secrets: Cannot be exported, must be re-entered manually
