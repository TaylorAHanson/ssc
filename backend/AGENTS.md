# backend/AGENTS.md

FastAPI + LangGraph backend: the unified agent, its tools/providers, the durable
executor + poller, and the governance/settings APIs. **Read `docs/ARCHITECTURE.md`
first** — it is the source of truth for how the pieces fit together.

## Setup, run, test

- **Venv:** `backend/venv`. Activate it before running anything Python —
  dependencies are **not** globally installed. `./dev.sh` creates the venv and
  `pip install -r backend/requirements.txt` for you.
- **Run:** `./dev.sh` from repo root (serves on `:8000`). `./dev.sh --debug` runs
  under `debugpy` (attach via the "Attach to Backend" VS Code config).
- **Tests:** `cd backend && source venv/bin/activate && pytest`. Run a subset with
  `pytest tests/test_sentinel_multiworkspace.py -q`. Config is `backend/pytest.ini`;
  shared fixtures in `tests/conftest.py`.
- **Workflow eval harness:** `app/workflows/harness.py` — use it to exercise agent
  workflows end-to-end rather than hand-driving the graph.
- Scratch/debug scripts must run inside the venv too. Prefer attaching the
  debugger over sprinkling debug output; use the logging library, never `print`.

## Directory map (`backend/app/`)

| Dir | Responsibility |
|---|---|
| `api/` | FastAPI routers (`api/v1/*`): settings, governance, requests, data_contracts, system. |
| `agents/` | Unified agent: `runner.py`, `prompts.py`. |
| `workflows/` | Workflows-as-data, LangGraph graph, `tools.py`, eval `harness.py`. |
| `tools/` | Tool implementations (`self_service/`, `governance/`, `authoring/`, `external/`); `tools/mcp.py` defines the `@tool` decorator. |
| `providers/` | External integrations (Databricks handlers, notifications, LMWS, MCP). |
| `core/` | `config.py`, `default_config.py`, `settings_store.py`, `workspaces.py`. |
| `db/` | SQLAlchemy models, `session.py`, startup `migrate.py`. |
| `workers/` | `poller.py` — durable execution / scheduled tasks. |
| `model_serving/` | LLM clients (agent LLM, classifier, AI Gateway routing). |
| `state_machines/`, `middleware/`, `services/`, `jobs/`, `content/` | Supporting logic. |

## Conventions that matter

- **Tools are defined with `@tool`** (`app.tools.mcp`) and must declare a
  `side_effect_class` — one of `read` (default), `app_write`, `data_grant`,
  `infra`, `membership`, `notify`, `destructive`. This drives the ToolExecutor's
  approval gates, idempotency, and audit via the `data.agent.tools` OPA package.
  Classify honestly; `read` is the only non-mutating class. Everything routes
  through the ToolExecutor chokepoint — don't call side-effecting SDKs around it.
- **Unity Catalog reads run On-Behalf-Of the user** via `uc_client_for(_obo_token)`
  in `core/workspaces.py`, always pinned to the **home** workspace (UC is
  metastore-global). Never target a remote `target_host` for UC listings, and
  don't silently fall back to the app SP on deployed targets — a permission error
  should reflect the *user's* grants. Declare `_obo_token` on such tools.
- **Config** lives in `core/config.py` (with in-code defaults in
  `default_config.py`). Runtime-editable settings go in `core/settings_store.py`
  under the correct group (`EDITABLE_FIELDS`); deploy-time/secret values stay in
  `READONLY_FIELDS` / `databricks.yml`. Follow the no-code principle: make new
  knobs configurable, don't hardcode. Some field types: `cron`, `catalog`,
  `string_list`, `collection`, `select`, `color`.
- **DB migrations:** there is **no Alembic**. Schema evolution uses the
  lightweight, idempotent helpers in `db/migrate.py` (`run_startup_migrations`),
  which add columns / rename tables *before* `create_all` at startup.
- **DB sessions:** use `get_lakebase_session()` for workers/background tasks and
  tools — not `next(get_db())` (that leaks). `session.py` sets TCP keepalives to
  survive idle-connection drops.
- **Blocking I/O** inside FastAPI `BackgroundTasks` / the event loop must be
  offloaded with `asyncio.to_thread` (see the contract-sync path).
- **Always check `backend.log`** after a change that reloads FastAPI to confirm it
  started cleanly.
