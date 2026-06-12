from typing import Dict, Any, List, Optional, Literal
import asyncio
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
from databricks.sdk import WorkspaceClient

class SearchEntitlementsInput(BaseModel):
    entitlement_types: List[Literal["data", "workspace", "compute", "all"]] = Field(
        ..., 
        description="Types of entitlements to search for. usage: ['data'] to search only data entitlements."
    )
    filter_string: Optional[str] = Field(
        None, 
        description="Optional string to filter resource names by (case-insensitive)."
    )
    use_obo: bool = Field(
        True, 
        description="Whether to use On-Behalf-Of (OBO) authentication to search as the user. Default is true."
    )

def _get_provider() -> DatabricksProvider:
    return DatabricksProvider(
        host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
        token=settings.DATABRICKS_TOKEN,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET,
        config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
    )

def _get_effective_permission(client: WorkspaceClient, object_type: str, object_id: str, current_user: Any, user_groups: List[str]) -> str:
    """
    Try to determine effective permission for the user on an object.
    Returns: 'MANAGE', 'WRITE', 'READ', or 'Unknown'.
    """
    if not current_user:
        return "Read (Implicit)"
        
    try:
        perms = client.permissions.get(object_type.lower(), object_id)
        user_principal = current_user.user_name
        
        highest_perm = "READ"
        
        for acl in perms.access_control_list:
            # Check direct user match
            if acl.user_name == user_principal:
                for p in acl.all_permissions:
                    if p.permission_level == "CAN_MANAGE":
                        return "MANAGE (Explicit)" 
                    if p.permission_level in ["CAN_EDIT", "CAN_MANAGE_RUN", "CAN_RESTART"]:
                        highest_perm = "WRITE"
            
            # Check group matches
            # Note: This requires accurate user_groups list.
            # If acl.group_name is in user_groups
            if acl.group_name and acl.group_name in user_groups:
                for p in acl.all_permissions:
                    if p.permission_level == "CAN_MANAGE":
                        # We don't return immediately, as user deny might override? 
                        # Databricks usually additive.
                        highest_perm = "MANAGE" # Upgrade to manage
                    elif p.permission_level in ["CAN_EDIT", "CAN_MANAGE_RUN", "CAN_RESTART"]:
                        if highest_perm != "MANAGE":
                            highest_perm = "WRITE"
        
        if highest_perm == "MANAGE":
            return "MANAGE (Explicit - via Group)"
        return f"{highest_perm} (Explicit)"
        
    except Exception:
        return "Read/Write (Implicit)"

def _search_data_entitlements(client: WorkspaceClient, filter_string: Optional[str], using_obo: bool, current_user: Any = None, user_groups: List[str] = []) -> List[Dict[str, Any]]:
    """Search for data (Unity Catalog) entitlements.

    Synchronous on purpose: the body is a blocking Databricks SDK loop. The
    caller dispatches it via ``asyncio.to_thread`` so it never blocks the loop.
    """
    results = []
    try:
        catalogs = client.catalogs.list()
        for cat in catalogs:
            if filter_string and filter_string.lower() not in cat.name.lower():
                continue
            
            permission_level = "Use/Read (Implicit)"
            
            results.append({
                "type": "catalog",
                "name": cat.name,
                "owner": cat.owner,
                "permission": permission_level
            })
    except Exception as e:
        raise e
        
    return results

def _search_workspace_entitlements(client: WorkspaceClient, filter_string: Optional[str], using_obo: bool, current_user: Any = None, user_groups: List[str] = []) -> List[Dict[str, Any]]:
    """Search for workspace entitlements with recursion (blocking; run in a thread)."""
    results = []
    try:
        # BFS Queue: (path, depth)
        queue = [('/', 0)]
        max_depth = 5 # Prevent infinite loops
        max_items = 100 # Safety limit for non-filtered searches
        # If filtered, we might search deeper or more items?
        if filter_string:
            max_items = 500
        
        items_found = 0
        
        while queue and items_found < max_items:
            path, depth = queue.pop(0)
            if depth > max_depth:
                continue
            
            try:
                items = client.workspace.list(path)
                for item in items:
                    # Add subdirectories to queue
                    if item.object_type and item.object_type.value == 'DIRECTORY':
                        queue.append((item.path, depth + 1))
                        
                    # Check filter
                    if filter_string and filter_string.lower() not in item.path.lower():
                        continue
                    
                    permission_level = "Read (Implicit)"
                    
                    if filter_string:
                        permission_level = _get_effective_permission(
                            client, 
                            item.object_type.value, 
                            item.object_id, 
                            current_user,
                            user_groups
                        )
    
                    results.append({
                        "type": item.object_type.value if hasattr(item.object_type, 'value') else "unknown",
                        "path": item.path,
                        "id": item.object_id,
                        "permission": permission_level
                    })
                    items_found += 1
                    if items_found >= max_items:
                        break
                        
            except Exception as e:
                print(f"DEBUG: Failed to list workspace path {path}: {e}")
                pass # Skip folders we can't read
                
    except Exception as e:
            print(f"DEBUG: Error in workspace search: {e}")
            pass 
    return results

def _search_compute_entitlements(client: WorkspaceClient, filter_string: Optional[str], using_obo: bool, current_user: Any = None, user_groups: List[str] = []) -> List[Dict[str, Any]]:
    """Search for compute entitlements (blocking; run in a thread)."""
    results = []
    try:
        clusters = client.clusters.list()
        for cluster in clusters:
            if filter_string and filter_string.lower() not in cluster.cluster_name.lower():
                continue
            
            permission_level = "Can Attach To (Implicit)"
            
            if filter_string:
                permission_level = _get_effective_permission(
                    client, 
                    "cluster", 
                    cluster.cluster_id, 
                    current_user,
                    user_groups
                )

            results.append({
                "type": "cluster",
                "name": cluster.cluster_name,
                "id": cluster.cluster_id,
                "state": cluster.state.value if hasattr(cluster.state, 'value') else str(cluster.state),
                "permission": permission_level
            })
    except Exception as e:
        print(f"DEBUG: Error in compute search: {e}")
        pass
    return results

def _prepare_client(use_obo: bool, obo_token: Optional[str]) -> tuple:
    """Build the (possibly OBO) client and resolve identity. Blocking SDK work."""
    provider = _get_provider()
    client = provider.client
    using_obo = False

    if use_obo and obo_token:
        print(f"DEBUG: Using OBO token for search (len={len(obo_token)})")
        client = provider.get_workspace_client(token=obo_token)
        print(f"DEBUG: Initialized OBO client for host {client.config.host}")
        using_obo = True
    elif use_obo:
        print("DEBUG: OBO requested but no token found in kwargs. Falling back to Service Principal.")

    current_user = None
    user_groups: List[str] = []
    try:
        current_user = client.current_user.me()
        print(f"DEBUG: Search executing as user: {current_user.user_name} (ID: {current_user.id})")
        if hasattr(current_user, 'groups') and current_user.groups:
            user_groups = [g.display_name for g in current_user.groups]
    except Exception as e:
        print(f"DEBUG: Could not determine current user identity: {e}")

    return client, using_obo, current_user, user_groups


@tool(
    name="search_user_entitlements",
    description="Searches for user entitlements across Data (Unity Catalog), Workspace (Notebooks, Folders), and Compute resources. Features: 1) Recursively searches workspace folders up to 5 levels deep. 2) Analyzes EFFECTIVE permissions, resolving both direct access and group inheritance (e.g., 'MANAGE' via 'Admin Group'). Use this to answer 'what do I have access to?' or 'can I access X?'.",
    args_schema=SearchEntitlementsInput
)
async def search_user_entitlements(entitlement_types: List[str], filter_string: Optional[str] = None, use_obo: bool = True, **kwargs) -> Dict[str, Any]:
    """Execute the entitlement search."""
    try:
        # Client setup + identity resolution are blocking SDK calls; offload them.
        client, using_obo, current_user, user_groups = await asyncio.to_thread(
            _prepare_client, use_obo, kwargs.get("_obo_token")
        )

        # Normalize entitlement types
        if "all" in entitlement_types:
            types_to_search = {"data", "workspace", "compute"}
        else:
            types_to_search = set(entitlement_types)
        
        tasks = []
        
        if "data" in types_to_search:
            tasks.append(asyncio.to_thread(_search_data_entitlements, client, filter_string, using_obo, current_user, user_groups))
        
        if "workspace" in types_to_search:
            tasks.append(asyncio.to_thread(_search_workspace_entitlements, client, filter_string, using_obo, current_user, user_groups))
            
        if "compute" in types_to_search:
            tasks.append(asyncio.to_thread(_search_compute_entitlements, client, filter_string, using_obo, current_user, user_groups))
            
        # Run searches in parallel (each in its own worker thread).
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Aggregate results
        aggregated_results = {
            "data": [],
            "workspace": [],
            "compute": [],
            "errors": []
        }
        
        result_idx = 0
        if "data" in types_to_search:
            res = results[result_idx]
            if isinstance(res, Exception):
                aggregated_results["errors"].append(f"Data search failed: {str(res)}")
            else:
                aggregated_results["data"] = res
            result_idx += 1
            
        if "workspace" in types_to_search:
            res = results[result_idx]
            if isinstance(res, Exception):
                aggregated_results["errors"].append(f"Workspace search failed: {str(res)}")
            else:
                aggregated_results["workspace"] = res
            result_idx += 1
            
        if "compute" in types_to_search:
            res = results[result_idx]
            if isinstance(res, Exception):
                aggregated_results["errors"].append(f"Compute search failed: {str(res)}")
            else:
                aggregated_results["compute"] = res
            result_idx += 1

        return {
            "count": len(aggregated_results["data"]) + len(aggregated_results["workspace"]) + len(aggregated_results["compute"]),
            "results": aggregated_results,
            "mode": "OBO" if using_obo else "ServicePrincipal"
        }

    except Exception as e:
        raise RetryableError(f"Entitlement search failed: {str(e)}")
