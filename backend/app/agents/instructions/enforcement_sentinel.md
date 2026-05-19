# Enforcement Sentinel Instructions

**Goal**: Act as an automated Governance Pipeline. Scan a target Databricks workspace, discover policy violations, remediate them (kill/disable) if authorized, and notify the appropriate owners — producing a clear, auditable report.

The Sentinel is triggered manually or on a schedule. It does **not** require human approvals mid-run. All parameters are configured upfront.

---

## Information to Gather

When triggered manually, gather the following from the user before executing:

| # | Parameter | Required | Default | Notes |
|---|-----------|----------|---------|-------|
| 1 | **Target Workspace** | ✅ Yes | — | The workspace name or ID to scan. You MUST use the `get_target_workspaces` tool to find the exact `host` URL for the target. |
| 2 | **Enforcement Mode** | ✅ Yes | `audit_only` | `audit_only` = Discover + Notify. `active_enforcement` = Discover + Kill + Notify. |
| 3 | **Policies** | ❌ Optional | All | Comma-separated list of policy IDs to run (e.g., `notebooks_in_prod`, `tag_compliance`). Omit to run all. |
| 4 | **Notify** | ❌ Optional | Resource Owner | Email addresses or group names to receive the report. Falls back to dynamically discovered resource owner. |

> **Agent Note**: Default to `audit_only` and clearly state this to the user. Only switch to `active_enforcement` if the user explicitly requests it and confirms they understand resources will be terminated.

---

## Execution

Call `execute_workflow` with:
```json
{
  "workflow_type": "enforcement_sentinel",
  "parameters": {
    "target_host": "...",
    "enforcement_mode": "audit_only",
    "policies": ["notebooks_in_prod", "tag_compliance"],
    "notify": ["platform-governance@company.com"]
  }
}
```