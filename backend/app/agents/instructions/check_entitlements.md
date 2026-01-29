# Check Entitlements Instructions

## Intent
The user wants to check their entitlements or access rights to various resources (data, workspace, compute). This tool goes beyond simple listing; it performs a deep analysis of *effective* permissions.

## Tools
- `search_user_entitlements`: Use this tool to search for entitlements.

## Capabilities & Logic
1. **Recursive Search**: The tool recursively searches workspace folders (up to 5 levels deep) to find nested notebooks and directories.
2. **Permission Analysis**: It calculates *effective* permissions by checking both:
    - **Direct Access**: Permissions assigned directly to the user.
    - **Group Inheritance**: Permissions inherited from the user's groups (e.g., if a group has 'CAN_MANAGE', the user effectively has 'MANAGE').
3. **Granularity**: It distinguishes between `MANAGE`, `WRITE`, and `READ` access, and notes whether access is `Explicit` (via ACL) or `Implicit`.

## Guidelines
1. **Identify Entitlement Type**:
    - If the user asks about "data", "tables", "catalogs", or "schemas", set `entitlement_types=["data"]`.
    - If the user asks about "compute", "clusters", or "warehouses", set `entitlement_types=["compute"]`.
    - If the user asks about "workspace", "notebooks", or "folders", set `entitlement_types=["workspace"]`.
    - If the query is general (e.g., "what do I have access to?"), set `entitlement_types=["all"]`.

2. **Filter Specific Resources**:
    - If the user names a specific resource (e.g., "do I have access to *finance_prod*?"), pass "finance_prod" as the `filter_string`.
    - **Note**: Providing a filter string triggers a deeper permission check for the matched items.

3. **Performance**:
    - Always prefer specifying `entitlement_types` over "all" if the user intent is specific, as "all" searches multiple providers in parallel.

4. **Response Strategy**:
    - **Summarize Effective Access**: Don't just list items. Highlight *what level* of access the user has (e.g., "You have **MANAGE** access to the 'Finance' folder via the 'Finance Admins' group.").
    - **Mention Group Access**: Explicitly point out when access is granted via a group membership.
    - **Scope**: If no entitlements are found, explicitly state that. Mention if the search was performed via OBO (user identity) or Service Principal.
