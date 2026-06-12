# Platform Administration Guide

This guide is for **Platform Admins** and **Governance Admins** who own and operate the platform day to day. It focuses on the thing you'll do most: **authoring, testing, shipping, and governing no-code workflows** — plus the operational tooling around them (versioning, environment promotion, live monitoring, and governance posture).

For deployment see the [Platform Setup Guide](./PLATFORM_SETUP.md); for the policy/governance model see [Governance](./GOVERNANCE.md); for the system internals see [Architecture](./ARCHITECTURE.md).

---

## 1. Mental Model: Workflows Are Data

A **workflow** is a *Workflow* — a DB-backed record with a JSON `graph_spec`, not code. The `graph_spec` is an ordered list of **stages** compiled into a durable, checkpointed graph:

- **gate** — a human/event approval the request pauses on (manager, platform admin, data owner, training completion, PR merge, or "children done").
- **step** — runs exactly one governed *tool* (optionally once per item via fan-out).

Because workflows are data, you author and change them **without a deploy or a code review** — in the visual editor or directly in chat with the agent. Every mutating step still runs through the governed `ToolExecutor` (capability scope + OPA + idempotency + audit), so "no-code" never means "no governance."

> The authoritative, **editable** reference for the spec format, gate types, the expression mini-language, and finicky-tool house rules lives in the **Context Catalog**: *Admin → Context Catalog → Platform Administration → "Authoring Workflows (Workflows) — Guide"*. Edit it to encode your org's conventions; the agent reads it when helping you author.

---

## 2. Two Ways to Author

### 2a. Visual editor — *Admin → Workflows*

The Workflows studio is the primary authoring surface:

- **Stage list + canvas + inspector**: add/reorder gates and steps; click a node in the graph or the list to edit it.
- **Gates**: pick the approver type and optionally a declarative **auto-approve condition** (e.g. auto-approve enterprise scope) built in the condition UI.
- **Steps**: pick a tool from the live tool list, set its arguments with the expression-aware editor (with a raw-JSON escape hatch), declare which prior gate approvals it requires, and optionally a `success_fact`.
- **Dry-run / Test**: project the draft against a sample context — see which gates auto-approve and the exact args each step would receive, **without running anything**.
- **Publish**: a blast-radius confirmation (gates, mutating steps, external-MCP steps, missing-request-type warnings) validates before it goes live.
- **Version history / rollback**, **clone**, and **export/import** are all in the Workflows toolbar (see §4–§5).

### 2b. In chat — the agent authors with you (admins only)

The same agent that runs workflows can help you design and edit them. These tools appear **only** for Platform/Governance Admins (everyone else never sees them):

| Tool | What it does | Side effect |
| :--- | :--- | :--- |
| `list_workflow_building_blocks` | Lists the real step tools, gate types, and expression operators | read |
| `get_workflow` | Fetches an existing workflow + its `graph_spec` | read |
| `validate_workflow_spec` | Structural validation of a candidate spec | read |
| `preview_workflow_spec` | Side-effect-free dry-run projection against a sample context | read |
| `save_workflow_draft` | Creates/updates a **draft** (does not affect live requests) | app_write (audited) |
| `publish_workflow` | Runs the full pre-publish gate, then publishes + snapshots a version | app_write (audited) |

Just ask, e.g. *"Create a workflow that grants read access to a table after manager approval, then notifies the requester,"* or *"Add a platform-admin review gate before the apply step in `service_principal`."* The agent will consult the guide, propose a spec, validate and preview it, and only publish on your explicit confirmation.

---

## 3. The Safe Authoring Loop

Whether in the editor or in chat, follow the same loop:

1. **Learn the building blocks** — `list_workflow_building_blocks` (or the editor's tool picker) shows every wireable tool with its side-effect class and whether it mutates. A step can only reference a real tool.
2. **Draft** — start from a similar workflow (`get_workflow`) and adapt.
3. **Validate** — structural checks: stage shapes, gate types, tool existence, well-formed expressions.
4. **Preview (dry-run)** — against a realistic sample context. Confirm which gates fire and the exact args each step receives. **No tools run, nothing is written.**
5. **Save draft** — persisted but **not live**; it won't affect any real request.
6. **Publish** — only after review. Publishing runs the **pre-publish gate** (structural validation **plus** compiling the spec and resolving every referenced tool), then makes it live for its `request_type` and writes an immutable **version snapshot** for rollback.

### Things that make a workflow correct

- **`request_type`** must be set for a published workflow to actually govern requests of that type. A draft can be saved without it, but publish will refuse until it's set.
- A **step after a gate** should list that gate's type in `approvals` (e.g. `["manager"]`) so OPA enforcement sees the approval is satisfied.
- Set **`success_fact`** on the meaningful provisioning step — it drives the request timeline and the live graph view.
- Reserved stage names (cannot be used): `pending`, `complete`, `completed`, `rejected`.

---

## 4. Versioning & Rollback

Every publish writes an **immutable snapshot** of the workflow body. From the Workflows studio:

- **Version history** lists prior published versions with who published them.
- **Rollback** restores a chosen version **as a new draft** — review and dry-run it, then re-publish. (Rollback never silently changes live behavior.)

---

## 5. Promoting Workflows Across Environments (dev → staging → prod)

Workflows are portable. Use **export/import bundles** (format `atlas.workflows/v1`, keyed by `key`, with no ids/status/version) to promote them:

1. In the source env, **export** the published workflows you want to promote (Workflows → Export).
2. In the target env, **import** the bundle (Workflows → Import). Imported workflows land as **drafts** by default.
3. In the target env, **dry-run** each imported draft against representative context, then **publish**.

This gives you a deliberate dev → staging → prod path: build and prove in lower envs, promote the artifact, re-validate, then go live.

### Locking authoring in production

Production should not be hand-edited. Set the `workflow_authoring_locked` variable to `true` for the `prod` target (it already defaults to `true` there in `databricks.yml`). When an environment is locked:

- Create / edit / publish / unpublish / delete / rollback are **disabled** — in the UI, the API, **and** the agent's `save_workflow_draft` / `publish_workflow` tools (they refuse with a clear message).
- The **only** way to change workflows is an all-or-nothing **bundle import**, which defaults to importing as *published* in a locked env.
- Reads, export, validate, and **dry-run** remain available, so you can still inspect and test what's live.

The Workflows studio shows a lock banner and renders read-only in a locked environment. This makes "build in lower envs → promote a vetted bundle" the enforced path to prod, not a convention.

---

## 6. Editing the Context Library (House Rules)

The Context Catalog is a curated, editable knowledge base the agent retrieves from. As an admin you'll use it two ways:

- **The authoring guide** (*Platform Administration → "Authoring Workflows (Workflows) — Guide"*) is seeded automatically and is **yours to edit**. Add tool-specific gotchas, naming conventions, and required-gate rules in its *"Finicky tools & house rules"* section. The agent reads this when authoring, so your guidance shapes what it builds. (Seeding is idempotent and revision-aware, so re-deploys won't clobber your edits.)
- **Domain docs** for the self-service experience (internal processes, standards, product knowledge) live in other domains and back the agent's `search_context_catalog` answers.

> Tip: when a tool is finicky (ordering matters, specific args required, must be gated), document it once in the guide. Both human authors and the agent will then get it right consistently.

---

## 7. Monitoring & Day-to-Day Operations

### Watch a request move through its workflow
Open any request → **Workflow** tab for the **live graph view**: the authored graph with each node annotated as done / current / pending / rejected, derived from the same fact log as the timeline. It polls until the request reaches a terminal state.

### Audit & traceability
- Every governed tool call appends an **audit fact** (tool, side-effect class, policy decision, result/error).
- The request **timeline** and live graph are reconstructed from the immutable fact log.
- When MLflow tracing is enabled, each agent turn is one **trace** (LLM + tool spans); human feedback can be attached to a turn's `trace_id`.

### Pre-publish regression gate (eval harness)
The workflow **eval harness** compiles every registered graph, proves gates pause/resume to `completed`, asserts all mutations route through the `ToolExecutor`, and compares each run to a committed **golden transcript** (ordered tool calls + gates + status). Run it before shipping graph changes:

```bash
cd backend
python -m app.workflows.harness            # hermetic run + golden compare
python -m app.workflows.harness --capture  # refresh goldens after an intended change
python -m app.workflows.harness --sandbox  # run against a real throwaway workspace (not for CI)
```

### Governance posture
Confirm enforcement is on in deployed environments — the startup logs show it:
```
GOVERNANCE: agent-tool OPA is in ENFORCE mode (mutating policy gates active).
```
SHADOW mode (the local default) logs policy decisions but does **not** block. Set `agent_tool_opa_enforce: "true"` for any deployed environment. See [Governance](./GOVERNANCE.md) for the policy model, OPA `.rego` editing, certification, and the reactive Sentinels.

---

## 8. Roles & Access

| Capability | Who |
| :--- | :--- |
| Author/validate/preview/save/publish workflows (editor **and** agent tools) | **Platform Admin**, **Governance Admin** |
| Edit OPA `.rego` governance policies | Governance Admin (file-based; see [Governance](./GOVERNANCE.md)) |
| Run/operate Enforcement Sentinels, data certification | Governance Admin |
| Use self-service workflows (request access, provisioning, etc.) | All users |

Role scoping is enforced at the chat endpoint: tools tagged for admins are filtered out of every non-admin's tool set, so non-admins can neither see nor call the authoring tools.

---

## 9. Quick Reference

| Thing | Where |
| :--- | :--- |
| Visual workflow editor | App → **Admin → Workflows** |
| Authoring guide (editable) | App → **Admin → Context Catalog → Platform Administration** |
| Live graph for a request | App → request detail → **Workflow** tab |
| Workflow definitions (code catalog seed) | `backend/app/workflows/graphs/specs.py` |
| Spec schema / expression language | `backend/app/workflows/spec_loader.py`, `backend/app/workflows/expr.py` |
| Governed tool executor | `backend/app/tools/tool_executor.py` |
| Agent authoring tools | `backend/app/tools/authoring/workflow_authoring.py` |
| OPA policies (Rego) | `backend/policies/` |
| Eval harness + goldens | `backend/app/workflows/harness.py`, `backend/app/workflows/golden_transcripts.json` |
| Governance posture / feature flags | `databricks.yml` variables, `configuration.yaml` |
| Lock authoring in an environment | `databricks.yml` var `workflow_authoring_locked` (→ `WORKFLOW_AUTHORING_LOCKED`) |
