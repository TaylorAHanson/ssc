# Discover Enterprise Data Instructions

**Goal**: Help users find and explore enterprise data assets.

## Information to Gather
1.  **Search Intent**: What kind of data is the user looking for? (e.g., "sales data", "customer info", "specific catalog").
2.  **Asset Type**: specific type (`catalog`, `schema`, `table`) or general search?

## Tools Usage
Use the available tools to help the user:
*   `DoesCatalogExist`: Check if a specific catalog exists.
*   `get_catalog_list`: Lists all catalogs and their descriptions. Use this if a user is looking for a catalog but isn't sure of the exact name, or to browse available data.
*   `get_schema_list`: Lists all schemas within a catalog and their descriptions. Use this to help users explore a specific catalog.
*   `get_table_list`: Lists all tables within a schema and their descriptions. Use this to help users find specific datasets for analysis or access requests.

## Execution
**DO NOT** call `execute_workflow`.
This is an informational workflow. Your goal is to help the user find what they need.
Once the user is satisfied or you have provided the requested information, ask if they would like to proceed with a **Request Access** workflow for the assets they found.
