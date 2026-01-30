
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.tools.self_service.search_user_entitlements import SearchUserEntitlementsTool
from app.providers.databricks import DatabricksProvider
from databricks.sdk import WorkspaceClient

@pytest.fixture
def mock_provider():
    with patch("app.tools.self_service.search_user_entitlements.DatabricksProvider") as MockProvider:
        provider_instance = MockProvider.return_value
        provider_instance.client = MagicMock(spec=WorkspaceClient)
        provider_instance.get_workspace_client = MagicMock(return_value=MagicMock(spec=WorkspaceClient))
        yield provider_instance

@pytest.fixture
def tool(mock_provider):
    tool = SearchUserEntitlementsTool()
    tool._provider = mock_provider
    return tool

@pytest.mark.asyncio
async def test_search_entitlements_obo(tool, mock_provider):
    """Test that OBO token is used when provided."""
    obo_token = "test-obo-token"
    
    # Mock return of get_workspace_client
    mock_obo_client = MagicMock(spec=WorkspaceClient)
    mock_provider.get_workspace_client.return_value = mock_obo_client
    
    # Mock me()
    mock_obo_client.current_user.me.return_value = MagicMock(user_name="me")
    
    # Mock catalog list
    mock_obo_client.catalogs.list.return_value = []
    
    # Execute with OBO
    await tool.execute(
        entitlement_types=["data"], 
        use_obo=True, 
        _obo_token=obo_token
    )
    
    # Verify get_workspace_client was called with token
    mock_provider.get_workspace_client.assert_called_with(token=obo_token)
    
    # Verify me() was called (it might be called multiple times)
    mock_obo_client.current_user.me.assert_called()
    # Verify search used OBO client
    mock_obo_client.catalogs.list.assert_called_once()

@pytest.mark.asyncio
async def test_search_entitlements_fallback(tool, mock_provider):
    """Test fallback to default client when OBO token is missing."""
    # Execute with OBO requested but no token
    await tool.execute(
        entitlement_types=["data"], 
        use_obo=True
        # No _obo_token passed
    )
    
    # Verify get_workspace_client was NOT called
    mock_provider.get_workspace_client.assert_not_called()
    # Verify search used default client
    mock_provider.client.catalogs.list.assert_called_once()

@pytest.mark.asyncio
async def test_search_entitlements_parallel(tool, mock_provider):
    """Test parallel execution of searches."""
    mock_client = mock_provider.client
    
    # Execute all types
    await tool.execute(
        entitlement_types=["data", "workspace", "compute"], 
        use_obo=False
    )
    
    # Verify all search methods were called
    mock_client.catalogs.list.assert_called_once()
    mock_client.workspace.list.assert_called_once_with('/')
    mock_client.clusters.list.assert_called_once()

@pytest.mark.asyncio
async def test_search_entitlements_filtering_with_permissions_manage(tool, mock_provider):
    """Test filtering with permission check resulting in MANAGE."""
    mock_client = mock_provider.client
    
    # Mock me
    mock_client.current_user.me.return_value = MagicMock(user_name="me")
    
    # Mock data with UPPERCASE type (simulating real SDK)
    item = MagicMock()
    item.object_type.value = "NOTEBOOK" 
    item.path = "/test/notebook"
    item.object_id = "123"
    
    mock_client.workspace.list.return_value = [item]
    
    # Mock permissions
    mock_perms = MagicMock()
    acl_entry = MagicMock()
    acl_entry.user_name = "me"
    perm_item = MagicMock()
    perm_item.permission_level = "CAN_MANAGE"
    acl_entry.all_permissions = [perm_item]
    mock_perms.access_control_list = [acl_entry]
    
    # Enforce lowercase check in mock
    def permissions_get_side_effect(obj_type, obj_id):
        if obj_type != "notebook":
             raise Exception(f"Invalid object type casing: {obj_type}")
        return mock_perms
        
    mock_client.permissions.get.side_effect = permissions_get_side_effect
    
    # Execute with filter
    result = await tool.execute(
        entitlement_types=["workspace"], 
        use_obo=False,
        filter_string="notebook"
    )
    
    # Verify
    assert len(result["results"]["workspace"]) == 1
    res = result["results"]["workspace"][0]
    # This assertion is expected to fail until we fix the casing issue
    assert res["permission"] == "MANAGE (Explicit)"
    mock_client.permissions.get.assert_called_with("notebook", "123") # We expect code to fix it to lowercase

@pytest.mark.asyncio
async def test_search_entitlements_filtering_with_permissions_read(tool, mock_provider):
    """Test filtering with permission check resulting in READ/Implicit (failed check)."""
    mock_client = mock_provider.client
    # Mock me
    mock_client.current_user.me.return_value = MagicMock(user_name="me")
    
    # Mock data
    item = MagicMock()
    item.object_type.value = "notebook"
    item.path = "/test/notebook"
    item.object_id = "456"
    
    mock_client.workspace.list.return_value = [item]
    
    # Mock permissions to raise error (e.g. 403)
    mock_client.permissions.get.side_effect = Exception("Forbidden")
    
    # Execute with filter
    result = await tool.execute(
        entitlement_types=["workspace"], 
        use_obo=False,
        filter_string="notebook"
    )
    
    # Verify
    assert len(result["results"]["workspace"]) == 1
    res = result["results"]["workspace"][0]
    assert "Read/Write (Implicit)" in res["permission"]

@pytest.mark.asyncio
async def test_search_entitlements_recursion(tool, mock_provider):
    """Test recursive workspace search."""
    mock_client = mock_provider.client
    
    # Mock me
    mock_client.current_user.me.return_value = MagicMock(user_name="me")
    
    # Mock directory structure: / -> [subfolder], /subfolder -> [file]
    dir_item = MagicMock()
    dir_item.object_type.value = "DIRECTORY"
    dir_item.path = "/subfolder"
    dir_item.object_id = "dir1"
    
    file_item = MagicMock()
    file_item.object_type.value = "NOTEBOOK"
    file_item.path = "/subfolder/file"
    file_item.object_id = "file1"
    
    def list_side_effect(path):
        if path == "/":
            return [dir_item]
        elif path == "/subfolder":
            return [file_item]
        return []
        
    mock_client.workspace.list.side_effect = list_side_effect
    
    # Execute
    result = await tool.execute(
        entitlement_types=["workspace"], 
        use_obo=False
    )
    
    # Verify both found (recursion worked)
    assert len(result["results"]["workspace"]) == 2
    paths = [item["path"] for item in result["results"]["workspace"]]
    assert "/subfolder" in paths
    assert "/subfolder/file" in paths

@pytest.mark.asyncio
async def test_search_entitlements_group_permission(tool, mock_provider):
    """Test permission via group."""
    mock_client = mock_provider.client
    
    # Mock me with groups
    user = MagicMock(user_name="me")
    group = MagicMock()
    group.display_name = "admins" 
    user.groups = [group]
    mock_client.current_user.me.return_value = user
    
    # Mock item
    item = MagicMock()
    item.object_type.value = "NOTEBOOK" # Upper case here too
    item.path = "/test/notebook"
    item.object_id = "123"
    mock_client.workspace.list.return_value = [item]
    
    # Mock permissions (only group has manage)
    mock_perms = MagicMock()
    acl_entry = MagicMock()
    acl_entry.group_name = "admins"
    perm_item = MagicMock()
    perm_item.permission_level = "CAN_MANAGE"
    acl_entry.all_permissions = [perm_item]
    mock_perms.access_control_list = [acl_entry]
    
    # Allow uppercase for this test just to verify group logic separate from casing first? 
    # Or force lowercase. Let's force lowercase to be consistent.
    def permissions_get_side_effect(obj_type, obj_id):
        if obj_type != "notebook":
             raise Exception("Invalid object type")
        return mock_perms
    mock_client.permissions.get.side_effect = permissions_get_side_effect
    
    # Execute
    result = await tool.execute(
        entitlement_types=["workspace"], 
        use_obo=False,
        filter_string="notebook"
    )
    
    # Verify
    assert "MANAGE (Explicit - via Group)" == result["results"]["workspace"][0]["permission"]
