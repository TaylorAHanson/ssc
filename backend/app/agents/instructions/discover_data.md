# Discover Enterprise Data Instructions

**Goal**: Help users find and explore enterprise data assets.

## Information to Gather
1.  **Search Intent**: What kind of data is the user looking for? (e.g., "sales data", "customer info", "specific catalog").
2.  **Asset Type**: specific type (`catalog`, `schema`, `table`) or general search?

## Tools Usage
Use the available tools to help the user:
*   `DoesCatalogExist`: Check if a specific catalog exists.
*   *Future tools*: `SearchMetadata`, `ListTables`, etc. (Use whatever is available in your toolbox).

## Execution
**DO NOT** call `execute_workflow`.
This is an informational workflow. Your goal is to help the user find what they need.
Once the user is satisfied or you have provided the requested information, ask if they would like to proceed with a **Request Access** workflow for the assets they found.
