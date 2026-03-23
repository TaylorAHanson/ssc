# Governance pipeline: Enforcement Sentinel, OPA, and allowlists

This document describes how automated governance works in this codebase: the **Enforcement Sentinel** workflow, **Open Policy Agent (OPA)** with multiple **Rego** modules, **resource handlers**, and the **allowlist** (database, API, UI, and `allowlist_exception` workflow). The allowlist is one piece of that system—not the whole story.

---

## Executive summary

- **Enforcement Sentinel** is an asynchronous governance pipeline: discover resources in a workspace, evaluate them against policy, optionally remediate, then notify. It does not use the conversational agent for mid-run approvals; the agent’s job is mainly to gather parameters and start the workflow.
- **Policy** lives in **Rego** (`.rego` files under `backend/policies/`). The backend loads those files (glob), and for each discovered resource runs **one OPA evaluation per policy module** (separate query per package, e.g. `data.databricks.governance.notebooks_in_prod`).
- **Allowlist** is the **Lakebase `AllowlistModel`** plus admin API/UI and the **`allowlist_exception`** request workflow. It stores **approved / pending / rejected** exceptions keyed by workspace, resource id, and resource type.
- **Allowlist data in OPA**: The Sentinel builds a single `input` JSON per resource and passes it to **every** policy evaluation. That payload **includes** `allowlist_records` for the workspace. **Only the `asset_allowlist` Rego module reads `input.allowlist_records` today.** Other modules (e.g. `notebooks_in_prod`, `tag_compliance`) implement their own rules and **ignore** the allowlist unless you add that logic to them or add aggregation rules in Python.
- **Precedence across policies**: OPA does **not** merge multiple `.rego` files into one winner. Each file returns its own `is_violation` / `action` / `reason` for that query. The application **collects** results (e.g. one violation row per `(resource, policy_id)`). Global rules like “allowlist exempts this resource from every policy” require **explicit design**: either Rego in each policy, a dedicated orchestrator package, or post-processing in Python.

---

## 1. How the pieces fit together

| Piece | Role |
|--------|------|
| **Rego modules** (`backend/policies/*.rego`) | Declarative rules per concern (tags, prod notebooks, asset allowlist matrix, etc.). |
| **OPA** (`OpaProvider`) | Evaluates `input` against one package at a time (`data.databricks.governance.<module>`). |
| **Sentinel state machine** | Loads policy paths, discovers resources (via handlers / APIs), loops **resource × policy**, aggregates violations, runs enforcement handlers, notifies. |
| **AllowlistModel + API + Admin UI** | CRUD for governance exceptions; source of `allowlist_records` in `input`. |
| **`allowlist_exception` workflow** | Creates **pending** rows immediately (reprieve), then **approved** / **rejected** after platform admin action. |
| **Resource handlers** | Map generic actions (e.g. `KILL`) to Databricks SDK calls per resource type. |

---

## 2. OPA evaluation model (multiple policies, one resource)

This section explains **what runs in what order**, **what Rego sees as `input`**, and **why the allowlist is present on every call even though only one policy reads it**.

### 2.1 The nested loop (conceptually)

Think of evaluation as a **grid**, not a single decision:

| Resource / Policy file | `asset_allowlist.rego` | `notebooks_in_prod.rego` | `tag_compliance.rego` | … (each `*.rego` in scope) |
|-------------------------|------------------------|---------------------------|------------------------|------------------------------|
| **Resource A** | OPA call → result₁ | OPA call → result₂ | OPA call → result₃ | … |
| **Resource B** | OPA call → … | OPA call → … | OPA call → … | … |

For **each cell** (one resource, one policy file):

1. The Sentinel builds **one JSON document** — the **`input` document** (see §2.3). It is **identical** for every policy in that row: same `workspace`, same `resource`, same `request_time`, same `allowlist_records`.
2. It calls OPA with:
   - **Policy**: that single `.rego` file (and its package `databricks.governance.<name>`).
   - **Query**: `data.databricks.governance.<name>` (e.g. `data.databricks.governance.asset_allowlist`).
3. OPA returns that package’s **exported** values the query asks for — in practice the Sentinel cares about **`is_violation`**, **`action`**, **`reason`** (and defaults if undefined).
4. If `is_violation` is true, Python appends a **violation row** that includes **which policy** fired (e.g. `policy: "tag_compliance"`).

So: **N resources × M policies ⇒ up to N×M OPA evaluations.** There is **no** single Rego “merge” step across files unless you add one.

### 2.2 What is *not* happening

- **Not** one OPA run that “applies all rules at once” from every file in one package (unless you refactor into one bundle and one query).
- **Not** automatic precedence (e.g. “allowlist beats tag compliance”) unless implemented in **Rego** (shared helper / orchestrator) or **Python** (post-filter after aggregation).

### 2.3 Canonical `input` document (what every Rego module receives)

In Rego, this object is available as **`input`** (e.g. `input.workspace.type`, `input.resource.id`).

**Always present in the Sentinel loop today**

| Field | Purpose |
|--------|---------|
| `workspace` | `{ "name": string, "type": string }` — `type` is how policies classify posture (`enterprise`, `domain`, `prod`, `dev`, …). Discovery code must set it consistently. |
| `resource` | At minimum `{ "id": string, "type": string }`. Extra keys are optional and policy-specific (see below). |
| `request_time` | ISO-8601 string (or comparable ordering vs `expires_at`) for allowlist expiry checks. |
| `allowlist_records` | Array of rows from `AllowlistModel` for this workspace (subset of DB columns). **Present on every policy call** for a stable API; **only `asset_allowlist.rego` reads it** unless you extend other modules. |

**Optional fields on `resource` (examples — add as discovery matures)**

Policies such as `notebooks_in_prod`, `tag_compliance`, `idle_clusters`, etc. expect richer `input.resource` when those rules are evaluated, for example:

| Example key | Used by (conceptually) |
|----------------|-------------------------|
| `path` | Notebook path under workspace |
| `tags` | Map for `tag_compliance` |
| `state`, `idle_hours` | `idle_clusters` |
| `has_schedule`, `days_since_last_run` | `stale_jobs` |

If a field is missing, a policy that references it may not fire or may use Rego defaults — that is per module.

**Full example `input` JSON** (illustrative: one resource with enough fields that several modules *could* use; not every policy requires every field):

```json
{
  "workspace": {
    "name": "ws-finance-prod",
    "type": "prod"
  },
  "resource": {
    "id": "/Workspace/Users/jane.doe/analysis",
    "type": "notebook",
    "path": "/Workspace/Users/jane.doe/analysis",
    "tags": {
      "cost-center": "FIN-001",
      "owner": "jane.doe@company.com"
    },
    "state": "RUNNING",
    "idle_hours": 3,
    "has_schedule": false,
    "days_since_last_run": 50
  },
  "request_time": "2026-03-18T12:00:00Z",
  "allowlist_records": [
    {
      "resource_id": "fin-forecast-app",
      "status": "approved",
      "expires_at": "2027-03-18T12:00:00Z",
      "justification": "Finance critical app"
    },
    {
      "resource_id": "/Workspace/Users/jane.doe/analysis",
      "status": "pending",
      "expires_at": null,
      "justification": "Notebook exception in review"
    }
  ]
}
```

**Wire format:** The Python code passes the object above as the OPA **input document** for local `opa eval -i` (Rego reads it as `input.*`). A remote OPA REST call wraps the same object as `{"input": <document>}` — semantically it is still the same `input` tree inside Rego.

**How this example behaves (intuition only)**

- **`asset_allowlist.rego`** — Reads `input.allowlist_records`. For a **notebook** in **`prod`**, it may flag a violation or `SKIPPED_ALLOWLIST` / `PENDING_EXCEPTION` depending on matrix + rows for **this** `resource.id`.
- **`notebooks_in_prod.rego`** — Uses `workspace` + `resource` (e.g. `type`, `path`). It does **not** read `allowlist_records` today, so it can still report a violation for the same notebook even if another row exists for a different `resource_id`.
- **`tag_compliance.rego`** — Uses `resource.tags`. Same story: no allowlist unless you add it.

### 2.4 Allowlist in the shared `input` (why it feels redundant)

- **Injected for all policies:** `allowlist_records` is on **every** OPA call so the payload is **one stable contract** for the Sentinel, tests, and `evaluate_policy`.
- **Consumed in Rego by:** **`asset_allowlist.rego` only** today (`input.allowlist_records`).
- **Implication:** An approved allowlist row for resource X **does not** silence **`notebooks_in_prod`**, **`tag_compliance`**, or other modules unless you extend those policies or add Python/orchestrator logic (§2.5).

### 2.5 If you want “allowlist wins globally”

Pick one approach and implement it explicitly:

1. **Per-policy Rego** — Each module (or a shared imported module) checks `allowlist_records` and clears `is_violation` when a matching approved exception applies to that resource.
2. **Python aggregation** — After collecting violations, filter out rows where allowlist + your rules say “exempt.”
3. **Orchestrator Rego** — One package and one query produce a final `decision` for the resource (advanced).

---

## 3. Standard Rego contract (all modules)

Each `*.rego` file under `databricks.governance.<name>` should expose a consistent surface for the Sentinel and tools:

- **`is_violation`** — Boolean: this resource violates this module’s rules.
- **`action`** — String the executor interprets (e.g. `KILL`, `PAUSE`, `ALLOW`, `SKIPPED_ALLOWLIST`, `PENDING_EXCEPTION`, or domain-specific values). Handlers must map these to SDK calls over time.
- **`reason`** — Human-readable explanation for logs and reports.

Defaults typically set `is_violation := false` and `action := "ALLOW"` when the module does not apply.

---

## 4. Allowlist-specific policy: `asset_allowlist.rego`

This module encodes the **restricted environment × restricted asset type** matrix (e.g. enterprise/prod vs app, genie, dashboard, job, notebook). It is the **only** bundled policy that:

- Treats matching resources as violations **unless** an allowlist row exists.
- Uses **`pending`** rows for a temporary reprieve during approval.
- Uses **`approved`** + optional **`expires_at`** for `SKIPPED_ALLOWLIST`.

See `backend/policies/asset_allowlist.rego` for the authoritative rules. The implementation plan does not duplicate the full file here.

---

## 5. Allowlist data: `AllowlistModel` (Lakebase)

The database holds exception state. Rows are loaded for the target workspace and passed as `allowlist_records` in `input`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | String (PK) | UUID for the row. |
| `resource_id` | String | Normalized Databricks id or path. |
| `resource_type` | String | e.g. `app`, `notebook`, `dashboard`, `genie_space`. |
| `workspace` | String | Workspace id or name. |
| `justification` | String | Business reason. |
| `status` | String | `pending`, `approved`, `rejected`. |
| `request_id` | String (optional) | Links to `requests.id` for audit. |
| `approved_by` | String (optional) | Admin identity when approved. |
| `expires_at` | DateTime (optional) | Null means no expiry. |

Migrations for local dev use `backend/migrate_db.py` (SQLite).

---

## 6. Sentinel integration (discovery → OPA → enforce → notify)

1. **Discovery** — List resources via Databricks APIs / resource handlers (implementation grows over time).
2. **Context** — Load `AllowlistModel` rows for the workspace → `allowlist_records`.
3. **Evaluation** — For each resource, for each selected `.rego` file, evaluate with the **same** `input` document (see **§2.3** for the canonical shape and a full JSON example).
4. **Aggregation** — Collect violations with `policy` set to the module name (e.g. `asset_allowlist`, `idle_clusters`).
5. **Enforcement** — In `active_enforcement`, execute actions via handlers; respect mode (`audit_only` vs kill).
6. **Notify** — Email / UI report (spec for HTML table lives below as product requirements for the workflow output, not agent chat).

---

## 7. Agent tools (dry-run and status)

- **`evaluate_policy`** — Builds the same style of `input` (including allowlist rows) and evaluates the relevant Rego module(s) so the agent can answer “can I deploy X?” without relying on prose instructions alone.
- **`check_allowlist_status`** — Reads `AllowlistModel` for status questions (“is my exception still pending?”).

---

## 8. `allowlist_exception` workflow (practical flow)

1. User needs an exception for a restricted asset; agent or UI starts **`allowlist_exception`**.
2. Workflow creates a **`pending`** `AllowlistModel` row immediately (Sentinel sees **`PENDING_EXCEPTION`** from `asset_allowlist` while approval is in flight).
3. Platform admin approves or rejects; row becomes **`approved`** or **`rejected`**.
4. Subsequent Sentinel runs: **`SKIPPED_ALLOWLIST`** for approved + valid expiry, or violation again if rejected / expired.

---

## 9. Resource handlers (execution layer)

Databricks remediation is type-specific (app delete vs job pause vs cluster terminate). **Resource handlers** encapsulate `discover`, `kill`, `warn` (and similar) per type so the Sentinel loop stays generic.

- Base: `backend/app/providers/databricks/handlers/base.py`
- Examples: app, cluster, job handlers under `handlers/`

The enforcement phase maps `violation.resource_type` and `violation.action` to the right handler as those actions grow beyond a single `KILL`.

---

## 10. Policy catalog (Rego modules and scope)

Each row maps to a file `backend/policies/<policy_id>.rego` (package `databricks.governance.<policy_id>`). The **Uses allowlist** column states whether that module reads `input.allowlist_records` **in the current codebase**.

### Severity levels

| Severity | Meaning | Typical handling |
|----------|---------|------------------|
| `CRITICAL` | Security / strong compliance | Kill in active enforcement; urgent notify |
| `HIGH` | Cost / governance | Kill or stop; notify owner + governance |
| `MEDIUM` | Drift / hygiene | Warn; kill optionally after grace |
| `LOW` | Informational | Report; rare auto-remediation |

**Implementation:** Each Rego module exposes `default severity := "NONE"` and sets `severity` when `is_violation` is true (or per outcome for `asset_allowlist`). OPA results are copied into `violations[]` as `severity`. The Enforcement Sentinel uses `backend/app/state_machines/enforcement_sentinel/remediation.py` (`resolve_enforcement_step`) so that in **active enforcement**, `handler.kill` runs only for `action == "KILL"` with `HIGH` or `CRITICAL`; `MEDIUM`/`LOW` demote destructive actions to `handler.warn`; explicit `WARN` actions always notify; non-remediation outcomes (`SKIPPED_ALLOWLIST`, `PENDING_EXCEPTION`) are skipped.

### Policy table

| `policy_id` | Name | Severity | Scope | Uses allowlist in Rego | Notes |
|-------------|------|----------|-------|-------------------------|--------|
| `asset_allowlist` | Restricted assets in enterprise/prod | CRITICAL | `enterprise`, `prod` × listed asset types | **Yes** | Pending/approved exceptions; matrix in Rego |
| `notebooks_in_prod` | No notebooks in Shared/Repos in prod | CRITICAL | `prod` | No | Path + workspace rules only |
| `tag_compliance` | Required tags on compute/jobs | HIGH | All | No | Tag keys on resource in `input` |
| `abandoned_workspace` | Inactive workspace | HIGH | All | No | Needs workspace-shaped `input.resource` |
| `orphan_volumes` | Stale volumes | MEDIUM | All | No | |
| `stale_jobs` | Unscheduled idle jobs | MEDIUM | All | No | Relaxed threshold in dev/test in Rego |
| `dangling_sps` | Inactive service principals | CRITICAL | All | No | |
| `enterprise_storage_cap` | Per-user storage cap | HIGH | `enterprise` | No | |
| `idle_clusters` | Idle running clusters | HIGH | All | No | |
| `mlflow_bloat` | Stale unlinked experiments | LOW | Domain | No | |
| `temp_tables` | Old `_temp` / `_test` tables | MEDIUM | All | No | |
| `over_provisioned_warehouses` | Underutilized warehouses | MEDIUM | All | No | |
| `temporary_admins` | Expired temporary admin | HIGH | All | No | |

---

## 11. Adding a new Rego policy

1. Add `backend/policies/<name>.rego` with `package databricks.governance.<name>`.
2. Implement `is_violation`, `action`, `reason`, and `severity` (defaults: `default severity := "NONE"`).
3. Optionally read `input.allowlist_records` if this policy should respect exceptions (not automatic).
4. Add a row to the policy table above.
5. Ensure discovery supplies enough fields on `input.resource` and `input.workspace` for the rules to fire.
6. Extend resource handlers if remediation needs new SDK calls.

No Python change is required **only** for loading: the Sentinel globs `*.rego`. Handler and discovery work still land in code when the policy applies to new resource types or actions.

---

## 12. Implementation checklist (reference)

1. OPA integration (`OpaProvider`) and Rego files under `backend/policies/`.
2. `AllowlistModel`, migrations, CRUD API, Admin UI.
3. Sentinel state machine: dynamic policy loop, violations aggregation, enforcement + notify hooks.
4. Resource handlers for types you remediate in production.
5. Agent tools: `evaluate_policy`, `check_allowlist_status`.
6. `allowlist_exception` state machine and instructions.

---

## 13. Sentinel execution context (reports, email, scopes)

### Workspace posture (informative)

| Workspace type | Identified by | Notes |
|----------------|---------------|--------|
| Enterprise hub | Name contains `enterprise` | Strict; allowlist-driven exceptions for listed assets |
| Domain | e.g. `ws-{domain}-{env}` | Moderate |
| Production | Env / tag `prod` | Stricter tagging / notebook rules in catalog |
| Development | `dev` / `test` | Relaxed thresholds where policies encode it |

### Discover → enforce → notify

- **Discover** — Enumerate resources; attach owner where possible for reporting.
- **Enforce** — In `active_enforcement`, run handlers; log facts for audit.
- **Notify** — Build HTML/email report for the workflow run (summary + per-violation rows). This is **workflow output**, not something the conversational agent must stream as its primary return value.

### Allowlist and Unity Catalog (optional pattern)

Some orgs mirror exceptions in Unity Catalog for analytics. The **system of record** for this app is **`AllowlistModel`** in Lakebase; UC can be a derived store if needed.

---

## 14. Providers and governance pipeline vs provisioning

### Databricks / notification capabilities

The Sentinel depends on `DatabricksProvider` and `NotificationProvider` for list/delete/stop APIs and email. See `backend/app/agents/instructions/enforcement_sentinel.md` (minimal) and provider modules for method lists.

### Governance pipeline vs provisioning workflow

| Dimension | Provisioning (e.g. workspace) | Governance (`enforcement_sentinel`) |
|-----------|-------------------------------|-------------------------------------|
| Trigger | User-driven request | Schedule or operator |
| Approvals | Often required | Parameters fixed at start |
| Outcome | Creates resources | Scans, evaluates policy, remediates, reports |
| Agent | Gathers params, starts workflow | Gathers scope/mode, starts workflow |

---

## Related docs

- `docs/GOVERNANCE.md` — Three-layer governance (agent, state machines, reactive enforcers).
- `backend/policies/asset_allowlist.rego` — Allowlist matrix policy source.
- `backend/app/state_machines/enforcement_sentinel/state_machine.py` — Dynamic policy loop implementation.
