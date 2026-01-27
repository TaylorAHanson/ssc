import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.catalog_existence import DoesCatalogExistTool
from app.core.exceptions import RetryableError

class TestDoesCatalogExistTool:
    
    @pytest.fixture
    def tool(self):
        with patch("app.tools.catalog_existence.DatabricksProvider") as MockProvider:
            # Setup the mock provider instance
            mock_instance = MockProvider.return_value
            tool = DoesCatalogExistTool()
            # Force the provider to be our mock
            tool._provider = mock_instance
            return tool

    @pytest.mark.asyncio
    async def test_propertities(self, tool):
        assert tool.name == "does_catalog_exist"
        assert "catalog" in tool.description.lower()
        schema = tool.input_schema
        assert "catalog_name" in schema["properties"]
        assert "catalog_name" in schema["required"]

    @pytest.mark.asyncio
    async def test_execute_exists(self, tool):
        # Setup mock behavior
        tool.provider.execute_sql = AsyncMock(return_value={
            "rows": [{"catalog": "test_catalog"}]
        })

        # Execute
        result = await tool.execute(catalog_name="test_catalog")

        # Verify
        assert result["exists"] is True
        assert result["catalog_name"] == "test_catalog"
        assert result["details"] == {"catalog": "test_catalog"}
        tool.provider.execute_sql.assert_called_once_with("SHOW CATALOGS LIKE 'test_catalog'")

    @pytest.mark.asyncio
    async def test_execute_not_exists(self, tool):
        # Setup mock behavior
        tool.provider.execute_sql = AsyncMock(return_value={
            "rows": []
        })

        # Execute
        result = await tool.execute(catalog_name="missing_catalog")

        # Verify
        assert result["exists"] is False
        assert result["catalog_name"] == "missing_catalog"
        assert result["details"] is None

    @pytest.mark.asyncio
    async def test_execute_error(self, tool):
        # Setup mock behavior
        tool.provider.execute_sql = AsyncMock(side_effect=Exception("API Error"))

        # Verify exception
        with pytest.raises(RetryableError) as excinfo:
            await tool.execute(catalog_name="error_catalog")
        
        assert "Failed to check catalog existence" in str(excinfo.value)
