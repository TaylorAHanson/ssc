# EDAS Hub - Quick Reference Card

## Deployment Commands

```bash
# Local development (runs frontend + backend locally)
./dev.sh

# Deploy personal instance to Databricks
./deploy.sh dev

# CI/CD auto-deploys on push:
# - develop branch → edas-hub-dev
# - main branch → edas-hub
```

---

## GitHub Secrets & Variables (Per Environment)

### Secrets (sensitive)
| Name | Example |
|------|---------|
| `DATABRICKS_CLIENT_ID` | `00000000-0000-0000-0000-000000000000` |
| `DATABRICKS_CLIENT_SECRET` | `dose...` |

### Variables
| Name | Example |
|------|---------|
| `DATABRICKS_HOST` | `https://workspace.cloud.databricks.com` |
| `DATABRICKS_WAREHOUSE_ID` | `abc123def456` |
| `MODEL_SERVING_AGENT_LLM_ENDPOINT` | `databricks-claude-sonnet-4-5` |

---

## Service Principal Permissions

```
✓ Workspace Member (add via Admin Console)
✓ USE CATALOG on Unity Catalog
✓ Can Query on Model Serving Endpoint
✓ Can Use on SQL Warehouse
✓ Can Manage on Databricks Apps (or workspace admin)
```

---

## Architecture Summary

```
GitHub Actions ──► Databricks CLI (OAuth M2M) ──► Workspace
                                                      │
                                                      ▼
                                               ┌─────────────┐
                                               │ Databricks  │
                                               │ App         │
                                               │             │
User Browser ────────────────────────────────► │ React + API │
                                               │             │
                                               └──────┬──────┘
                                                      │
                           ┌──────────────────────────┼──────────────────────────┐
                           ▼                          ▼                          ▼
                    ┌─────────────┐           ┌─────────────┐           ┌─────────────┐
                    │ Model       │           │ Unity       │           │ SQL         │
                    │ Serving     │           │ Catalog     │           │ Warehouse   │
                    │ (LLM)       │           │ (metadata)  │           │ (queries)   │
                    └─────────────┘           └─────────────┘           └─────────────┘
```

---

## Agent Tools

| Tool | What it queries |
|------|-----------------|
| `get_catalog_list` | Unity Catalog → list catalogs |
| `get_schema_list` | Unity Catalog → list schemas |
| `get_table_list` | Unity Catalog → list tables |
| `does_catalog_exist` | SQL Warehouse → SHOW CATALOGS |
| `execute_workflow` | App DB → create request |

---

## Key Files

| File | Purpose |
|------|---------|
| `.github/workflows/deploy-*.yml` | CI/CD pipelines |
| `databricks.yml` | Bundle config for local deploy |
| `backend/app/tools/__init__.py` | Register agent tools |
| `backend/app/model_serving/client.py` | LLM client with OAuth |
| `backend/app/core/config.py` | All app settings |
