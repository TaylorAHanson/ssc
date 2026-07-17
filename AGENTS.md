# AGENTS.md

Guidance for AI coding agents (and humans) working in this repo. This is the
canonical agent guide; `.cursor/rules/project-rules.mdc` points here. Nested
`AGENTS.md` files add area-specific detail — the nearest one to the file you're
editing wins.

> **Naming note:** this file (`AGENTS.md`, plural) is *developer tooling* — how to
> work on the codebase. It is **unrelated** to the application's runtime
> **`AGENT.md`** (singular) Agent Profiles, which are authored in the Command
> Center and loaded on-behalf-of the user at runtime (see `docs/ARCHITECTURE.md`
> §14). Don't conflate them.

## What this is

A **no-code, governed agentic platform** on Databricks Apps: admins author
workflows as data (prompt + allowed tools + policy + approval rules), and a single
unified agent orchestrates provider-backed tools under a guardrail stack, with
long-running work executed durably on LangGraph + Lakebase (Postgres).

**Read `docs/ARCHITECTURE.md` before making backend changes** — it explains the
ToolExecutor chokepoint, the guardrail stack, workflows-as-data, and the durable
execution model. Other useful docs: `DEVELOPER_QUICK_START.md`, `GOVERNANCE.md`,
`SERVICE_PRINCIPALS.md`, `PLATFORM_ADMINISTRATION.md`, `TERRAFORM_GITOPS.md`.

## Repo layout (monorepo)

| Path | What |
|---|---|
| `src/` | React + TypeScript SPA (Vite). See `src/AGENTS.md`. |
| `backend/` | FastAPI + LangGraph backend, the agent, poller, tools, providers. See `backend/AGENTS.md`. |
| `mcp_app/` | Small standalone MCP server, deployed as its own Databricks App. See `mcp_app/AGENTS.md`. |
| `docs/` | Architecture + operator/developer guides. |
| `databricks.yml` | Bundle config / per-target env vars for deployment. |
| `dev.sh` | One-command local dev (backend + frontend). |

## Running locally

- **`./dev.sh`** starts backend (`:8000`) and frontend (`:5173`). It creates
  `backend/.env` from `.env.example` if missing, runs a local preflight that
  resolves Databricks creds from your CLI login, sets up the venv, installs deps,
  and tails to `backend.log` / `frontend.log`.
- `./dev.sh --debug` starts the backend under `debugpy` (port 5678); attach with
  the VS Code "Attach to Backend" config (see `docs/DEVELOPER_QUICK_START.md`).
- You do **not** need to deploy to Databricks to run locally with full
  functionality.

## Golden rules (apply everywhere)

- **Use the logging library, never `print`.**
- **Never hardcode the app/brand name.** In-code defaults live in
  `backend/app/core/default_config.py`, exposed as `settings.BRAND_*`
  (`backend/app/core/config.py`); a Platform Admin can override them live.
- **Prefer no-code over hardcoding.** New configuration should be editable in
  Admin → Settings (`settings_store.py`) or set in `databricks.yml` — not baked
  into code. Config layers: in-code defaults → env (`.env` / `databricks.yml`) →
  DB overrides (Admin → Settings), applied at startup.
- **Check `backend.log` before finishing** any change that reloads FastAPI — a
  startup error there means the app isn't actually running.
- **Venv gotcha:** most Python libs are **not** globally installed. Activate
  `backend/venv` before running any `python`/`pytest`/scratch script.
- **Local DB** is SQLite at `backend/app_hub.db` (query it directly if useful);
  deployed uses Lakebase/Postgres.
- Only commit when explicitly asked.

## Testing

- Backend: `cd backend && source venv/bin/activate && pytest` (details +
  workflow eval harness in `backend/AGENTS.md`).
- Frontend: `npm run build` (typecheck via `tsc`) and `npm run lint` (see
  `src/AGENTS.md`).
