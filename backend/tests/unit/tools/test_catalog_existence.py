import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.self_service.catalog_existence import does_catalog_exist
from app.core.exceptions import RetryableError

class TestDoesCatalogExistTool:
    
    @pytest.fixture
    def tool(self):
        return does_catalog_exist

    @pytest.fixture
    def mock_provider(self):
        with patch("app.tools.self_service.catalog_existence.DatabricksProvider") as MockProvider:
            mock_instance = MockProvider.return_value
            # Default behavior
            mock_instance.execute_sql = AsyncMock()
            yield mock_instance

    @pytest.mark.asyncio
    async def test_properties(self, tool):
        assert tool.name == "does_catalog_exist"
        assert "catalog" in tool.description.lower()
        schema = tool.input_schema
        assert "catalog_name" in schema["properties"]
        assert "catalog_name" in schema["required"]

    @pytest.mark.asyncio
    async def test_execute_exists(self, tool, mock_provider):
        # Setup mock behavior
        mock_provider.execute_sql.return_value = {
            "rows": [{"catalog": "test_catalog"}]
        }

        # Execute
        result = await tool.execute(catalog_name="test_catalog")

        # Verify
        assert result["exists"] is True
        assert result["catalog_name"] == "test_catalog"
        assert result["details"] == {"catalog": "test_catalog"}
        mock_provider.execute_sql.assert_called_once_with("SHOW CATALOGS LIKE 'test_catalog'")

    @pytest.mark.asyncio
    async def test_execute_not_exists(self, tool, mock_provider):
        # Setup mock behavior
        mock_provider.execute_sql.return_value = {
            "rows": []
        }

        # Execute
        result = await tool.execute(catalog_name="missing_catalog")

        # Verify
        assert result["exists"] is False
        assert result["catalog_name"] == "missing_catalog"
        assert result["details"] is None

    @pytest.mark.asyncio
    async def test_execute_error(self, tool, mock_provider):
        # Setup mock behavior
        mock_provider.execute_sql.side_effect = Exception("API Error")

        # Verify exception
        with pytest.raises(RetryableError) as excinfo:
            await tool.execute(catalog_name="error_catalog")
        
        assert "Failed to check catalog existence" in str(excinfo.value)

