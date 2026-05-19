import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.self_service.get_catalog_list import get_catalog_list
from app.core.exceptions import RetryableError

class MockCatalog:
    def __init__(self, name, comment=None, owner="test_owner", catalog_type="MANAGED_CATALOG", properties=None):
        self.name = name
        self.comment = comment
        self.owner = owner
        self.catalog_type = MagicMock()
        self.catalog_type.value = catalog_type
        # Also support direct string conversion if value attribute is missing
        self.catalog_type.__str__ = lambda x: catalog_type
        self.properties = properties

class TestGetCatalogListTool:
    
    @pytest.fixture
    def tool(self):
        return get_catalog_list
    
    @pytest.fixture
    def mock_provider(self):
        with patch("app.tools.self_service.get_catalog_list.DatabricksProvider") as MockProvider:
            with patch("app.core.workspaces.get_workspace_config") as mock_ws_config:
                mock_ws_config.return_value = MagicMock(host="https://test.azuredatabricks.net", token="test", client_id=None, client_secret=None)
                yield MockProvider.return_value

    @pytest.mark.asyncio
    async def test_properties(self, tool):
        assert tool.name == "get_catalog_list"
        assert "catalog" in tool.description.lower()
        schema = tool.input_schema
        assert "name_pattern" in schema.get("properties", {})
        assert "target_host" in schema.get("properties", {})

    @pytest.mark.asyncio
    async def test_execute_success(self, tool, mock_provider):
        # Setup mock return data
        mock_catalogs = [
            MockCatalog(name="main", comment="Main catalog"),
            MockCatalog(name="samples", comment=None, catalog_type="SYSTEM_CATALOG")
        ]
        
        mock_provider.client.catalogs.list.return_value = mock_catalogs

        # Execute
        result = await tool.execute(target_host="https://test.azuredatabricks.net")

        # Verify
        assert result["count"] == 2
        assert len(result["catalogs"]) == 2
        
        first = result["catalogs"][0]
        assert first["name"] == "main"
        assert first["comment"] == "Main catalog"
        assert first["catalog_type"] == "MANAGED_CATALOG"
        
        second = result["catalogs"][1]
        assert second["name"] == "samples"
        assert second["comment"] == "No description provided"
        assert second["catalog_type"] == "SYSTEM_CATALOG"

    @pytest.mark.asyncio
    async def test_execute_empty(self, tool, mock_provider):
        mock_provider.client.catalogs.list.return_value = []
        
        result = await tool.execute(target_host="https://test.azuredatabricks.net")
        
        assert result["count"] == 0
        assert result["catalogs"] == []

    @pytest.mark.asyncio
    async def test_execute_error(self, tool, mock_provider):
        mock_provider.client.catalogs.list.side_effect = Exception("SDK Error")
        
        with pytest.raises(RetryableError) as excinfo:
            await tool.execute(target_host="https://test.azuredatabricks.net")
            
        assert "Failed to fetch catalog list" in str(excinfo.value)
