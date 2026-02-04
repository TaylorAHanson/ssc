import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.self_service.find_owner import find_owner
from app.core.exceptions import RetryableError

@pytest.fixture
def mock_provider():
    with patch("app.tools.self_service.find_owner.DatabricksProvider") as MockProvider:
        mock_instance = AsyncMock()
        # Mock the synchronous client property chain
        mock_instance.client = MagicMock()
        MockProvider.return_value = mock_instance
        yield mock_instance

@pytest.mark.asyncio
async def test_find_owner_catalog_found(mock_provider):
    # Mock catalogs.get
    mock_cat = MagicMock()
    mock_cat.owner = "user@example.com"
    mock_provider.client.catalogs.get.return_value = mock_cat
    
    result = await find_owner.execute(object_type="catalog", object_name="main")
    
    assert result["found"] is True
    assert result["owner"] == "user@example.com"
    assert result["object_type"] == "catalog"
    mock_provider.client.catalogs.get.assert_called_with("main")

@pytest.mark.asyncio
async def test_find_owner_table_found(mock_provider):
    # Mock tables.get
    mock_table = MagicMock()
    mock_table.owner = "admin@example.com"
    mock_provider.client.tables.get.return_value = mock_table

    result = await find_owner.execute(object_type="table", object_name="main.schema.table1")
    
    assert result["found"] is True
    assert result["owner"] == "admin@example.com"
    mock_provider.client.tables.get.assert_called_with("main.schema.table1")

@pytest.mark.asyncio
async def test_find_owner_not_found(mock_provider):
    # Mock SDK error for catalog
    mock_provider.client.catalogs.get.side_effect = Exception("Catalog not found")
    
    result = await find_owner.execute(object_type="catalog", object_name="missing_cat")
    
    assert result["found"] is False
    assert "Catalog not found" in result["message"]

@pytest.mark.asyncio
async def test_find_owner_job_found(mock_provider):
    # Mock job response
    mock_job = MagicMock()
    mock_job.creator_user_name = "job_creator@example.com"
    mock_job.settings.name = "My Job"
    
    mock_provider.client.jobs.get.return_value = mock_job
    
    result = await find_owner.execute(object_type="job", object_name="1234")
    
    assert result["found"] is True
    assert result["owner"] == "job_creator@example.com"
    assert result["object_type"] == "job"
    mock_provider.client.jobs.get.assert_called_with(1234)

@pytest.mark.asyncio
async def test_find_owner_dashboard_found(mock_provider):
    # Mock lakeview response
    mock_dash = MagicMock()
    mock_dash.display_name = "My Dashboard"
    
    mock_provider.client.lakeview.get.return_value = mock_dash
    
    result = await find_owner.execute(object_type="dashboard", object_name="dash_id")
    
    assert result["found"] is True
    # Owner for dashboard returns Unknown in current logic as API varies
    assert "Unknown" in result["owner"]
    assert result["object_type"] == "dashboard"
    mock_provider.client.lakeview.get.assert_called_with("dash_id")

@pytest.mark.asyncio
async def test_find_owner_notebook_user_path(mock_provider):
    # Mock workspace response
    mock_provider.client.workspace.get_status.return_value = MagicMock()
    
    result = await find_owner.execute(object_type="notebook", object_name="/Users/alice@example.com/MyNotebook")
    
    assert result["found"] is True
    assert result["owner"] == "alice@example.com"
    assert result["object_type"] == "notebook"

@pytest.mark.asyncio
async def test_find_owner_notebook_shared_path(mock_provider):
    mock_provider.client.workspace.get_status.return_value = MagicMock()
    
    result = await find_owner.execute(object_type="notebook", object_name="/Shared/MyNotebook")
    
    assert result["found"] is True
    assert "Unknown" in result["owner"]
    assert "shared path" in result["message"]

@pytest.mark.asyncio
async def test_find_owner_genie_found(mock_provider):
    mock_space = MagicMock()
    mock_space.name = "My Genie Space"
    mock_provider.client.genie.spaces.get.return_value = mock_space
    
    result = await find_owner.execute(object_type="genie_space", object_name="space_id")
    
    assert result["found"] is True
    assert "Unknown" in result["owner"]
    assert result["object_type"] == "genie_space"

@pytest.mark.asyncio
async def test_find_owner_job_error(mock_provider):
    mock_provider.client.jobs.get.side_effect = Exception("Not found")
    
    result = await find_owner.execute(object_type="job", object_name="999")
    
    assert result["found"] is False
    assert "Job not found" in result["message"]

@pytest.mark.asyncio
async def test_find_owner_unsupported_type_fail_msg(mock_provider):
    # Pass a made-up type. Since valid types list is extensive now, just check logic.
    # Logic in find_owner returns "not implemented" if loop falls through.
    
    result = await find_owner.execute(object_type="weird_type", object_name="foo")
    
    assert result["found"] is False
    assert "not yet implemented" in result["message"]
