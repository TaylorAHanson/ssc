# Enforcement Sentinel Instructions

**Goal**: Act as an automated Governance Pipeline. Scan a target Databricks workspace, discover policy violations, apply the safe/reversible remediations, and notify the appropriate owners — producing a clear, auditable report.

The Sentinel is triggered manually or on a schedule. It does **not** require human approvals mid-run. All parameters are configured upfront.

**Enforcement is non-destructive by design.** Every run automatically applies only safe, reversible actions — **certify**, **uncertify**, and **warn** the owner. Destructive actions (kill, drop, suspend, …) are **never** automated: they are downgraded to an owner warning and left for a human to perform manually via "Review & Act" on the Enforcement Sentinel page. There is no "mode" to choose and no dry-run.

Notifications are severity-tiered: **HIGH**-severity violations email the governance group immediately (deduped so a steady-state HIGH doesn't re-fire every scan), while everything else is rolled into an anchored once-per-day governance digest.

---

## Information to Gather

When triggered manually, gather the following from the user before executing:

| # | Parameter | Required | Default | Notes |
|---|-----------|----------|---------|-------|
| 1 | **Target Workspace** | ✅ Yes | — | The workspace name or ID to scan. You MUST use the `get_target_workspaces` tool to find the exact `host` URL for the target. |
| 2 | **Policies** | ❌ Optional | All | Comma-separated list of policy IDs to run (e.g., `notebooks_in_prod`, `tag_compliance`). Omit to run all. |
| 3 | **Notify** | ❌ Optional | Resource Owner | Email addresses or group names to receive the report. Falls back to dynamically discovered resource owner. |

---

## Execution

Call `execute_workflow` with:
```json
{
  "workflow_type": "enforcement_sentinel",
  "parameters": {
    "target_host": "...",
    "policies": ["notebooks_in_prod", "tag_compliance"],
    "notify": ["platform-governance@company.com"]
  }
}
```
