# Marketplace Certification Instructions

**Goal**: Certify a dataset for the internal data marketplace.

## Information to Gather
1.  **Dataset Name**: The full name of the table or view to certify.
    *   *Validation*: Must exist in valid catalog/schema.
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
    "dataset_name": "...",
    "certification_level": "...",
    "data_steward": "...",
    "description": "..."
  }
}
```
