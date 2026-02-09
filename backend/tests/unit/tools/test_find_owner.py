import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.self_service.find_owner import find_owner
from app.core.exceptions import RetryableError

@pytest.fixture
def mock_provider():
    with patch("app.tools.self_service.find_owner.DatabricksProvider") as MockProvider:
        mock_instance = AsyncMock()
        # Mock the find_object_owner method since that's what find_owner now calls
        mock_instance.find_object_owner = AsyncMock()
        MockProvider.return_value = mock_instance
        yield mock_instance

@pytest.mark.asyncio
async def test_find_owner_catalog_found(mock_provider):
    # Mock the find_object_owner response
    mock_provider.find_object_owner.return_value = {
        "found": True,
        "owner": "user@example.com",
        "object_type": "catalog",
        "object_name": "main"
    }

    result = await find_owner.execute(object_type="catalog", object_name="main")

    assert result["found"] is True
    assert result["owner"] == "user@example.com"
    assert result["object_type"] == "catalog"
    mock_provider.find_object_owner.assert_called_with("catalog", "main")

@pytest.mark.asyncio
async def test_find_owner_table_found(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": True,
        "owner": "admin@example.com",
        "object_type": "table",
        "object_name": "main.schema.table1"
    }

    result = await find_owner.execute(object_type="table", object_name="main.schema.table1")

    assert result["found"] is True
    assert result["owner"] == "admin@example.com"
    mock_provider.find_object_owner.assert_called_with("table", "main.schema.table1")

@pytest.mark.asyncio
async def test_find_owner_not_found(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": False,
        "message": "Catalog not found"
    }

    result = await find_owner.execute(object_type="catalog", object_name="missing_cat")

    assert result["found"] is False
    assert "Catalog not found" in result["message"]

@pytest.mark.asyncio
async def test_find_owner_job_found(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": True,
        "owner": "job_creator@example.com",
        "object_type": "job",
        "object_name": "1234"
    }

    result = await find_owner.execute(object_type="job", object_name="1234")

    assert result["found"] is True
    assert result["owner"] == "job_creator@example.com"
    assert result["object_type"] == "job"
    mock_provider.find_object_owner.assert_called_with("job", "1234")

@pytest.mark.asyncio
async def test_find_owner_dashboard_found(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": True,
        "owner": "Unknown (Dashboard found)",
        "object_type": "dashboard",
        "object_name": "dash_id"
    }

    result = await find_owner.execute(object_type="dashboard", object_name="dash_id")

    assert result["found"] is True
    assert "Unknown" in result["owner"]
    assert result["object_type"] == "dashboard"
    mock_provider.find_object_owner.assert_called_with("dashboard", "dash_id")

@pytest.mark.asyncio
async def test_find_owner_notebook_user_path(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": True,
        "owner": "alice@example.com",
        "object_type": "notebook",
        "object_name": "/Users/alice@example.com/MyNotebook"
    }

    result = await find_owner.execute(object_type="notebook", object_name="/Users/alice@example.com/MyNotebook")

    assert result["found"] is True
    assert result["owner"] == "alice@example.com"
    assert result["object_type"] == "notebook"

@pytest.mark.asyncio
async def test_find_owner_notebook_shared_path(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": True,
        "owner": "Unknown (shared path)",
        "object_type": "notebook",
        "object_name": "/Shared/MyNotebook",
        "message": "Notebook found at shared path, owner could not be determined."
    }

    result = await find_owner.execute(object_type="notebook", object_name="/Shared/MyNotebook")

    assert result["found"] is True
    assert "Unknown" in result["owner"]
    assert "shared path" in result["message"]

@pytest.mark.asyncio
async def test_find_owner_genie_found(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": True,
        "owner": "Unknown (Genie space found)",
        "object_type": "genie_space",
        "object_name": "space_id"
    }

    result = await find_owner.execute(object_type="genie_space", object_name="space_id")

    assert result["found"] is True
    assert "Unknown" in result["owner"]
    assert result["object_type"] == "genie_space"

@pytest.mark.asyncio
async def test_find_owner_job_error(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": False,
        "message": "Job not found: Not found"
    }

    result = await find_owner.execute(object_type="job", object_name="999")

    assert result["found"] is False
    assert "Job not found" in result["message"]

@pytest.mark.asyncio
async def test_find_owner_unsupported_type_fail_msg(mock_provider):
    mock_provider.find_object_owner.return_value = {
        "found": False,
        "message": "Object type 'weird_type' is not yet implemented."
    }

    result = await find_owner.execute(object_type="weird_type", object_name="foo")

    assert result["found"] is False
    assert "not yet implemented" in result["message"]
