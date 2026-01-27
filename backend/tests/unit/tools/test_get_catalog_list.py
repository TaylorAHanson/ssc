import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.get_catalog_list import GetCatalogListTool
from app.core.exceptions import RetryableError

class MockCatalog:
    def __init__(self, name, comment=None, owner="test_owner", catalog_type="MANAGED_CATALOG"):
        self.name = name
        self.comment = comment
        self.owner = owner
        self.catalog_type = MagicMock()
        self.catalog_type.value = catalog_type
        # Also support direct string conversion if value attribute is missing
        self.catalog_type.__str__ = lambda x: catalog_type

class TestGetCatalogListTool:
    
    @pytest.fixture
    def tool(self):
        with patch("app.tools.get_catalog_list.DatabricksProvider") as MockProvider:
            mock_instance = MockProvider.return_value
            tool = GetCatalogListTool()
            tool._provider = mock_instance
            return tool

    @pytest.mark.asyncio
    async def test_properties(self, tool):
        assert tool.name == "get_catalog_list"
        assert "catalog" in tool.description.lower()
        assert tool.input_schema["properties"] == {}

    @pytest.mark.asyncio
    async def test_execute_success(self, tool):
        # Setup mock return data
        mock_catalogs = [
            MockCatalog(name="main", comment="Main catalog"),
            MockCatalog(name="samples", comment=None, catalog_type="SYSTEM_CATALOG")
        ]
        
        # We need to mock the synchronous client call inside the async execute
        # Note: In the tool code, it calls self.provider.client.catalogs.list()
        # This looks synchronous. If the tool wasn't wrapped in run_in_executor, it blocks.
        # But the tool execute method is async. Let's verify if the provider call is async or sync.
        # Looking at previous file view: catalogs = self.provider.client.catalogs.list()
        # It seems direct SDK call, which is sync.
        
        tool.provider.client.catalogs.list.return_value = mock_catalogs

        # Execute
        result = await tool.execute()

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
    async def test_execute_empty(self, tool):
        tool.provider.client.catalogs.list.return_value = []
        
        result = await tool.execute()
        
        assert result["count"] == 0
        assert result["catalogs"] == []

    @pytest.mark.asyncio
    async def test_execute_error(self, tool):
        tool.provider.client.catalogs.list.side_effect = Exception("SDK Error")
        
        with pytest.raises(RetryableError) as excinfo:
            await tool.execute()
            
        assert "Failed to fetch catalog list" in str(excinfo.value)
