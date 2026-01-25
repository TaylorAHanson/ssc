# REST API Access Instructions

**Goal**: Request access to Databricks REST API endpoints.

## Information to Gather
1.  **Service**: Which service/endpoint do you need access to?
    *   *Examples*: `Jobs API`, `Clusters API`, `SQL API`.
2.  **Access Type**: `Read-only` or `Manage` (Write) access?
3.  **Justification**: Why is this programmatic access needed?

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "rest_api_access",
  "parameters": {
    "service": "...",
    "access_type": "...",
    "justification": "..."
  }
}
```
