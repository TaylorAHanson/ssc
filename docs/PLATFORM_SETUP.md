# EDAS Hub - Platform Architecture & Setup Guide

## Developer Contribution Journey

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              DEVELOPER WORKSTATION                                       │
│                                                                                          │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Local Clone: ~/projects/fe-agentic-self-service/                               │   │
│   │                                                                                  │   │
│   │   ├── src/                    (React frontend source)                           │   │
│   │   ├── backend/                (FastAPI backend source)                          │   │
│   │   │   ├── app/                                                                  │   │
│   │   │   │   ├── tools/          ◄── Add new agent tools here                     │   │
│   │   │   │   ├── agents/                                                           │   │
│   │   │   │   └── ...                                                               │   │
│   │   │   └── static/             ◄── Built frontend copied here before deploy     │   │
│   │   ├── databricks.yml          (Bundle config)                                   │   │
│   │   └── deploy.sh               (Local deploy script)                             │   │
│   └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                          │
│   Developer Actions:                                                                     │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐             │
│   │ ./dev.sh    │    │ ./deploy.sh │    │ git push    │    │ git push    │             │
│   │ (local dev) │    │ dev         │    │ develop     │    │ main        │             │
│   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘             │
│          │                  │                  │                  │                     │
└──────────┼──────────────────┼──────────────────┼──────────────────┼─────────────────────┘
           │                  │                  │                  │
           ▼                  │                  │                  │
   ┌───────────────┐          │                  │                  │
   │ localhost:    │          │                  │                  │
   │ 5173 (FE)     │          │                  │                  │
   │ 8000 (API)    │          │                  │                  │
   └───────────────┘          │                  │                  │
                              │                  │                  │
           ┌──────────────────┘                  │                  │
           │                                     │                  │
           │              ┌──────────────────────┘                  │
           │              │                                         │
           │              │              ┌──────────────────────────┘
           │              │              │
           ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                      GITHUB                                              │
│                                                                                          │
│   ┌───────────────────────────────────────────────────────────────────────────────┐     │
│   │  Repository: databricks-field-eng/fe-agentic-self-service                     │     │
│   │                                                                                │     │
│   │  feature/* ──► PR ──► develop ──────────────────────► PR ──► main            │     │
│   │                          │                                      │              │     │
│   │                          │                                      │              │     │
│   │                          ▼                                      ▼              │     │
│   │              ┌─────────────────────┐              ┌─────────────────────┐     │     │
│   │              │ GitHub Actions      │              │ GitHub Actions      │     │     │
│   │              │ deploy-develop.yml  │              │ deploy-main.yml     │     │     │
│   │              │                     │              │                     │     │     │
│   │              │ 1. npm ci           │              │ 1. npm ci           │     │     │
│   │              │ 2. npm run build    │              │ 2. npm run build    │     │     │
│   │              │ 3. cp dist→static   │              │ 3. cp dist→static   │     │     │
│   │              │ 4. generate app.yaml│              │ 4. generate app.yaml│     │     │
│   │              │ 5. databricks CLI   │              │ 5. databricks CLI   │     │     │
│   │              └──────────┬──────────┘              └──────────┬──────────┘     │     │
│   │                         │                                    │                │     │
│   │   Env: development      │              Env: production       │                │     │
│   │   - CLIENT_ID (secret)  │              - CLIENT_ID (secret)  │                │     │
│   │   - CLIENT_SECRET       │              - CLIENT_SECRET       │                │     │
│   │   - HOST (variable)     │              - HOST (variable)     │                │     │
│   │   - WAREHOUSE_ID        │              - WAREHOUSE_ID        │                │     │
│   │   - MODEL_ENDPOINT      │              - MODEL_ENDPOINT      │                │     │
│   └─────────────────────────┼──────────────────────────────────────┼──────────────┘     │
│                             │                                      │                    │
└─────────────────────────────┼──────────────────────────────────────┼────────────────────┘
                              │ OAuth M2M                            │ OAuth M2M
                              │ (Service Principal)                  │ (Service Principal)
                              ▼                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              DATABRICKS WORKSPACE                                        │
│                                                                                          │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Workspace Files (where code is uploaded)                                       │   │
│   │                                                                                  │   │
│   │  /Workspace/                                                                    │   │
│   │  ├── Shared/                                                                    │   │
│   │  │   └── apps/                                                                  │   │
│   │  │       ├── edas-hub-dev/              ◄── CI/CD: develop branch              │   │
│   │  │       │   ├── app/                                                           │   │
│   │  │       │   │   ├── main.py                                                    │   │
│   │  │       │   │   ├── tools/                                                     │   │
│   │  │       │   │   └── ...                                                        │   │
│   │  │       │   ├── static/                (built React app)                       │   │
│   │  │       │   ├── app.yaml               (generated by CI/CD)                    │   │
│   │  │       │   └── requirements.txt                                               │   │
│   │  │       │                                                                      │   │
│   │  │       └── edas-hub/                  ◄── CI/CD: main branch                 │   │
│   │  │           └── (same structure)                                               │   │
│   │  │                                                                              │   │
│   │  └── Users/                                                                     │   │
│   │      └── rohan.ahire@company.com/                                              │   │
│   │          └── .bundle/                                                           │   │
│   │              └── edas-hub/                                                      │   │
│   │                  └── dev/               ◄── Local: ./deploy.sh dev             │   │
│   │                      └── (same structure)                                       │   │
│   └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                          │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Databricks Apps (running instances)                                            │   │
│   │                                                                                  │   │
│   │  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐     │   │
│   │  │ edas-hub-dev-rohan  │  │ edas-hub-dev        │  │ edas-hub            │     │   │
│   │  │ (Personal Dev)      │  │ (Integration)       │  │ (Production)        │     │   │
│   │  │                     │  │                     │  │                     │     │   │
│   │  │ Source: Users/      │  │ Source: Shared/     │  │ Source: Shared/     │     │   │
│   │  │ rohan/.bundle/...   │  │ apps/edas-hub-dev   │  │ apps/edas-hub       │     │   │
│   │  │                     │  │                     │  │                     │     │   │
│   │  │ URL: https://       │  │ URL: https://       │  │ URL: https://       │     │   │
│   │  │ edas-hub-dev-rohan- │  │ edas-hub-dev-       │  │ edas-hub-           │     │   │
│   │  │ xxx.apps.databricks │  │ xxx.apps.databricks │  │ xxx.apps.databricks │     │   │
│   │  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘     │   │
│   └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Deployment Path Summary

| Method | Trigger | Workspace Path | App Name |
|--------|---------|----------------|----------|
| `./deploy.sh dev` | Manual (local) | `/Workspace/Users/{you}/.bundle/edas-hub/dev/` | `edas-hub-dev-{username}` |
| Push to `develop` | CI/CD (GitHub Actions) | `/Workspace/Shared/apps/edas-hub-dev/` | `edas-hub-dev` |
| Push to `main` | CI/CD (GitHub Actions) | `/Workspace/Shared/apps/edas-hub/` | `edas-hub` |

---

## Physical Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                    GITHUB                                            │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │  Repository: databricks-field-eng/fe-agentic-self-service                   │    │
│  │                                                                              │    │
│  │  Branches:                                                                   │    │
│  │    main ────────► GitHub Actions ────► Deploy to Production                 │    │
│  │    develop ─────► GitHub Actions ────► Deploy to Development                │    │
│  │                                                                              │    │
│  │  Environments (Settings → Environments):                                     │    │
│  │    ├── development (secrets + variables)                                    │    │
│  │    └── production  (secrets + variables)                                    │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         │ OAuth M2M (Service Principal)
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              DATABRICKS WORKSPACE                                    │
│                                                                                      │
│  ┌──────────────────────┐    ┌──────────────────────┐    ┌────────────────────┐    │
│  │   Databricks App     │    │   Model Serving      │    │   Unity Catalog    │    │
│  │   (edas-hub-dev)     │    │   Endpoint           │    │                    │    │
│  │                      │    │                      │    │   ┌────────────┐   │    │
│  │  ┌────────────────┐  │    │  Claude Sonnet 4.5   │    │   │ Catalogs   │   │    │
│  │  │ FastAPI Backend│◄─┼────┼─► (Foundation Model) │    │   │ Schemas    │   │    │
│  │  │                │  │    │                      │    │   │ Tables     │   │    │
│  │  │  - Agent API   │  │    └──────────────────────┘    │   └────────────┘   │    │
│  │  │  - Tools       │──┼────────────────────────────────┼──►  (SDK queries)  │    │
│  │  │  - Workflows   │  │                                │                    │    │
│  │  └────────────────┘  │    ┌──────────────────────┐    └────────────────────┘    │
│  │         │            │    │   SQL Warehouse      │                              │
│  │  ┌────────────────┐  │    │   (for SQL queries)  │                              │
│  │  │ React Frontend │  │    │                      │◄─────── (does_catalog_exist) │
│  │  │ (static files) │  │    └──────────────────────┘                              │
│  │  └────────────────┘  │                                                          │
│  └──────────────────────┘    ┌──────────────────────┐                              │
│                              │   Lakebase (Future)  │                              │
│  App Service Principal ──────┤   or SQLite (Dev)    │                              │
│  (auto-created by App)       │   - requests table   │                              │
│                              │   - approvals table  │                              │
│                              │   - events table     │                              │
│                              └──────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Authentication Flow

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│  GitHub Actions │         │  Databricks CLI │         │  Databricks     │
│                 │         │                 │         │  Workspace      │
│  DATABRICKS_    │────────►│  OAuth M2M      │────────►│                 │
│  CLIENT_ID      │         │  Authentication │         │  - Upload code  │
│  CLIENT_SECRET  │         │                 │         │  - Deploy app   │
└─────────────────┘         └─────────────────┘         └─────────────────┘

┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│  Databricks App │         │  Databricks SDK │         │  Model Serving  │
│  (Runtime)      │         │                 │         │  Unity Catalog  │
│                 │────────►│  OAuth          │────────►│                 │
│  App Service    │         │  (Automatic)    │         │  (API calls)    │
│  Principal      │         │                 │         │                 │
└─────────────────┘         └─────────────────┘         └─────────────────┘
```

---

## What We Configured Today

### 1. GitHub Repository Secrets & Variables

| Environment | Type | Name | Value | Purpose |
|-------------|------|------|-------|---------|
| development | Variable | `DATABRICKS_HOST` | `https://your-dev-workspace.cloud.databricks.com` | Workspace URL |
| development | Secret | `DATABRICKS_CLIENT_ID` | `<sp-client-id>` | Service Principal for CI/CD |
| development | Secret | `DATABRICKS_CLIENT_SECRET` | `<sp-secret>` | Service Principal secret |
| development | Variable | `DATABRICKS_WAREHOUSE_ID` | `<warehouse-id>` | SQL Warehouse for queries |
| development | Variable | `MODEL_SERVING_AGENT_LLM_ENDPOINT` | `databricks-claude-sonnet-4-5` | LLM endpoint name |
| production | ... | (same structure) | ... | ... |

### 2. Databricks Workspace Resources

| Resource | Name | Purpose | Provisioning |
|----------|------|---------|--------------|
| **Service Principal** | `edas-hub-cicd-sp` | GitHub Actions deployment | Manual via Account Console |
| **Databricks App** | `edas-hub-dev` | Development app | Auto-created by CI/CD |
| **Databricks App** | `edas-hub` | Production app | Auto-created by CI/CD |
| **SQL Warehouse** | (existing) | Execute SQL queries | Pre-existing |
| **Model Serving Endpoint** | `databricks-claude-sonnet-4-5` | Foundation Model API | Pre-existing |

### 3. Code Fixes Applied

| Issue | Fix | File |
|-------|-----|------|
| Frontend using localhost:8000 | Added `VITE_API_BASE_URL=/api/v1` to build | `.github/workflows/deploy-*.yml` |
| Model serving auth failing | Added OAuth via Databricks SDK | `backend/app/model_serving/client.py` |
| URL missing https:// protocol | Auto-prepend https:// if missing | `backend/app/model_serving/client.py` |
| Wrong model endpoint name | Made configurable via GitHub variable | `.github/workflows/deploy-*.yml` |

---

## Per-Environment Provisioning Checklist

### Prerequisites (One-time Setup)

- [ ] GitHub repository created
- [ ] Databricks workspace(s) available
- [ ] Account-level admin access for Service Principal creation

### For Each Environment (dev, prod)

#### 1. Create Service Principal (Account Console)

```bash
# Via Databricks Account Console:
# 1. Go to User Management → Service Principals
# 2. Create new SP: "edas-hub-{env}-cicd"
# 3. Generate OAuth secret
# 4. Note Client ID and Secret
```

#### 2. Grant Service Principal Permissions (Workspace)

```sql
-- In Databricks SQL or via Terraform

-- Grant workspace access
-- (Add SP to workspace via Admin Console)

-- Grant Unity Catalog permissions (for tools)
GRANT USE CATALOG ON CATALOG * TO `edas-hub-{env}-cicd`;
GRANT USE SCHEMA ON SCHEMA *.* TO `edas-hub-{env}-cicd`;
GRANT SELECT ON TABLE *.*.* TO `edas-hub-{env}-cicd`;

-- Grant Model Serving access
-- (Via Serving UI: Endpoint → Permissions → Add SP with "Can Query")

-- Grant SQL Warehouse access
-- (Via SQL Warehouses UI: Warehouse → Permissions → Add SP with "Can Use")

-- Grant Apps permission (if needed)
-- (SP needs ability to create/deploy apps)
```

#### 3. Configure GitHub Environment

1. Go to **Repository → Settings → Environments**
2. Create environment (e.g., `development`)
3. Add secrets:
   - `DATABRICKS_CLIENT_ID` = SP Client ID
   - `DATABRICKS_CLIENT_SECRET` = SP Secret
4. Add variables:
   - `DATABRICKS_HOST` = Workspace URL
   - `DATABRICKS_WAREHOUSE_ID` = SQL Warehouse ID
   - `MODEL_SERVING_AGENT_LLM_ENDPOINT` = Model endpoint name

#### 4. Verify Model Serving Endpoint

1. Go to **Serving** in Databricks workspace
2. Confirm the Foundation Model endpoint exists (e.g., `databricks-claude-sonnet-4-5`)
3. Click on endpoint → **Permissions** → Add Service Principal with "Can Query"

#### 5. Deploy & Test

```bash
# Trigger deployment by pushing to branch
git push origin develop  # → deploys to development
git push origin main     # → deploys to production
```

---

## Environment Configuration Matrix

| Setting | Development | Production |
|---------|-------------|------------|
| App Name | `edas-hub-dev` | `edas-hub` |
| CPU | 1 | 2 |
| Memory | 2048 MB | 4096 MB |
| Branch | `develop` | `main` |
| GitHub Environment | `development` | `production` |

---

## Local Developer Setup (Bundle Deploy)

For developers to deploy personal instances:

### 1. Prerequisites

```bash
# Install Databricks CLI
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh

# Authenticate
databricks auth login --host https://your-workspace.cloud.databricks.com
```

### 2. Set Environment Variables

```bash
export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
export DATABRICKS_WAREHOUSE_ID=your-warehouse-id
```

### 3. Deploy Personal Instance

```bash
./deploy.sh dev
# Creates: edas-hub-dev-{your-username}
```

---

## Troubleshooting

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| "localhost:8000" error in browser | `VITE_API_BASE_URL` not set during build | Ensure workflow has `VITE_API_BASE_URL: /api/v1` |
| "internal configuration error" | Model serving auth failed | Check OAuth setup, SP permissions |
| "Request URL missing protocol" | `{{workspace_url}}` doesn't include https | Code auto-prepends https:// now |
| "Endpoint not found" 404 | Wrong endpoint name | Check `MODEL_SERVING_AGENT_LLM_ENDPOINT` variable |
| "Authentication failed" 401 | SP lacks permissions | Grant SP access to Model Serving endpoint |

### Viewing Logs

1. Go to **Compute → Apps → {app-name}**
2. Click **Logs** tab
3. Search for "Error" or specific keywords

---

## File Reference

| File | Purpose |
|------|---------|
| `.github/workflows/deploy-develop.yml` | CI/CD for development |
| `.github/workflows/deploy-main.yml` | CI/CD for production |
| `databricks.yml` | Asset Bundle config for local deploys |
| `deploy.sh` | Helper script for bundle deploy |
| `backend/app/model_serving/client.py` | OAuth + Model Serving client |
| `backend/app/tools/__init__.py` | Agent tool registry |
