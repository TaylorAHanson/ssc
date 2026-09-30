# AGENTS.md

Guidance for AI coding agents (and humans) working in this repo. This is the
canonical agent guide; `.cursor/rules/project-rules.mdc` points here. Nested
`AGENTS.md` files add area-specific detail — the nearest one to the file you're
editing wins.

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
- **Step output for approvers → `report_markdown`.** A workflow tool that returns
  `report_markdown` (+ `report_title`) has it shown on every later approval card
  (`step_report` fact → `GET /approvals` → `StepReportPanel`); no author config.
  Report text quoting a repo, a user or a model is untrusted: build it with
  `safe_text` / `safe_code` (`backend/app/services/app_code_review/report.py`).
- **Every user-visible change gets a release note** in `RELEASE_NOTES.md` — see
  [Release notes](#release-notes) below.
- Only commit when explicitly asked.

## Release notes

`RELEASE_NOTES.md` (repo root) is the single source of truth for the in-app
**Release notes** page (`/release-notes`, linked from the account menu). The app
parses it, so keep the format exact:

- One `## <major.minor.patch> — <YYYY-MM-DD>` heading per release, **newest
  first**. The top entry is shown as the current version.
- Bullets go under `### Added`, `### Changed`, `### Fixed` (or a feature-area
  subheading). Plain markdown only.

When you make a user-visible change (UI, behaviour, new capability, bug fix):

- **Bump per semver:** major = breaking change or major redesign; minor = new
  user-facing feature; patch = fixes and small tweaks. If the top entry is still
  unreleased work from the same batch of changes, add to it (raising its level if
  needed) instead of stacking another version.
- **Keep `package.json` `version` in sync** with the top entry.
- **Write for end users:** concise but complete sentences that say what changed
  and why it matters — no terse fragments, internal jargon, file paths, or the
  hardcoded brand name. Internal-only changes (refactors, tests, tooling) don't
  need an entry.

## Testing

- Backend: `cd backend && source venv/bin/activate && pytest` (details +
  workflow eval harness in `backend/AGENTS.md`).
- Frontend: `npm run build` (typecheck via `tsc`) and `npm run lint` (see
  `src/AGENTS.md`).
