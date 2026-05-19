# Batch Data Access Instructions

**Goal**: Request high-volume batch data access (e.g., via Delta Sharing).

## Information to Gather
1.  **Target Workspace**: Which workspace contains the dataset?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2.  **Dataset**: The name of the dataset or share you need (e.g., `catalog.schema.table`).
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST verify the dataset actually exists using `get_schema_list` or `get_table_list` (passing the `target_host`).
2.  **Recipient Type**: Internal or External?
    *   *Context*: External sharing uses Delta Sharing. Internal might just be normal access.
3.  **Frequency**: How often will this data be accessed?
    *   *Examples*: `Daily`, `Ad-hoc`, `Streaming`.
4.  **Transfer Mechanism**: `Delta Sharing`, `S3 Extract`, or `Direct Access`.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "batch_data_access",
  "parameters": {
    "target_host": "...",
    "dataset": "...",
    "recipient_type": "...",
    "frequency": "...",
    "transfer_mechanism": "..."
  }
}
```
