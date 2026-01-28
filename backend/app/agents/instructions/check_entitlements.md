# Check Entitlements Instructions

## Intent
The user wants to check their entitlements or access rights to various resources (data, workspace, compute).

## Tools
- `search_user_entitlements`: Use this tool to search for entitlements.

## Guidelines
1. **Identify Entitlement Type**:
    - If the user asks about "data", "tables", "catalogs", or "schemas", set `entitlement_types=["data"]`.
    - If the user asks about "compute", "clusters", or "warehouses", set `entitlement_types=["compute"]`.
    - If the user asks about "workspace", "notebooks", or "folders", set `entitlement_types=["workspace"]`.
    - If the query is general (e.g., "what do I have access to?"), set `entitlement_types=["all"]`.

2. **Filter Specific Resources**:
    - If the user names a specific resource (e.g., "do I have access to *finance_prod*?"), pass "finance_prod" as the `filter_string`.

3. **Performance**:
    - Always prefer specifying `entitlement_types` over "all" if the user intent is specific, to reduce search time.

4. **Response**:
    - Summarize the findings clearly.
    - If no entitlements are found, explicitly state that.
    - Mention if you are listing entitlements found via OBO (as the user) or as the system if fallback occurred.
