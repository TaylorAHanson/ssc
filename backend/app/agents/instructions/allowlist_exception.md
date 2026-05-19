# Allowlist Exception Request Instructions

**Goal**: Help the user request an exception to a governance policy so that their resource (e.g., Databricks App, long-running cluster) is not deleted by the Enforcement Sentinel.

---

## Pre-Check (Dry-Run)
Before you collect information for the request, you should check if the resource is actually going to be blocked.
Use the `evaluate_policy` tool with the resource details (if they know the workspace, type, and ID).
If the tool says `allowed = true`, tell them they do NOT need an exception!

---

## Information to Gather

When filing the request, gather the following:

| # | Parameter | Required | Notes |
|---|-----------|----------|-------|
| 1 | **Target Workspace** | ✅ Yes | The workspace where the resource lives. You MUST use `get_target_workspaces` to find the exact `host` URL. |
| 2 | **Resource Type** | ✅ Yes | Enum: `app`, `notebook`, `job`, `cluster`, `dashboard`, `genie_space`. |
| 3 | **Resource ID** | ✅ Yes | The normalized name or ID of the resource (e.g., `fin-forecast-app`). Remind them this must match the actual name EXACTLY. |
| 4 | **Justification** | ✅ Yes | A detailed business justification for why they need this exception. |
| 5 | **Expires At** | ❌ No | When this exception should expire (if they only need it temporarily). Must be an ISO date string if provided. |

---

## Execution

Once all parameters are gathered, call `execute_workflow` with:
```json
{
  "workflow_type": "allowlist_exception",
  "parameters": {
    "target_host": "...",
    "resource_type": "app",
    "resource_id": "fin-forecast-app",
    "justification": "Required for finance reporting.",
    "expires_at": "2025-12-31T23:59:59Z"
  }
}
```

## Post-Execution
Tell the user that their request has been submitted to the Platform Admin for approval. Inform them that the system will temporarily ignore their app until the ticket is reviewed.
