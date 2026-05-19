# Marketplace Certification Instructions

**Goal**: Certify a dataset for the internal data marketplace.

## Information to Gather
1.  **Target Workspace**: Which workspace contains the dataset?
    *   *Action*: You MUST use `get_target_workspaces` to find the exact `host` URL for the requested workspace.
2.  **Dataset Name**: The full name of the table or view to certify (e.g., `catalog.schema.table`).
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST verify the table or view actually exists using `get_table_list` (passing the `target_host`).
2.  **Certification Level**: The target certification tier.
    *   *Options*: `bronze`, `silver`, `gold`.
3.  **Data Steward**: Email of the person responsible for this data.
    *   *Validation*: Must be a valid email.
4.  **Description**: detailed description for the marketplace listing.
    *   *Validation*: Must be distinct from technical metadata.

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "marketplace_certification",
  "parameters": {
    "target_host": "...",
    "dataset_name": "...",
    "certification_level": "...",
    "data_steward": "...",
    "description": "..."
  }
}
```
