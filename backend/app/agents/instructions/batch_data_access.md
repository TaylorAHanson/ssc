# Batch Data Access Instructions

**Goal**: Request high-volume batch data access (e.g., via Delta Sharing).

## Information to Gather
1.  **Dataset**: The name of the dataset or share you need.
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
    "dataset": "...",
    "recipient_type": "...",
    "frequency": "...",
    "transfer_mechanism": "..."
  }
}
```
