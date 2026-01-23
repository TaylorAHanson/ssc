import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.catalog_existence import DoesCatalogExistTool
from app.providers.databricks.client import DatabricksProvider

@pytest.mark.asyncio
async def test_does_catalog_exist_tool():
    # Mock settings
    with patch("app.tools.catalog_existence.settings") as mock_settings:
        mock_settings.DATABRICKS_HOST = "https://example.cloud.databricks.com"
        mock_settings.DATABRICKS_TOKEN = "dapi123456789"
        
        # Initialize tool (which init provider)
        # We need to mock DatabricksProvider inside the tool or mock where it's instantiated
        # Since it's instantiated in __init__, we can mock the class
        with patch("app.tools.catalog_existence.DatabricksProvider") as MockProvider:
            mock_instance = MockProvider.return_value
            mock_instance.execute_sql = AsyncMock()
            
            tool = DoesCatalogExistTool()
            
            # Verify MCP metadata
            assert tool.name == "does_catalog_exist"
            assert "catalog" in tool.description
            assert "catalog_name" in tool.input_schema["properties"]
            
            # Test exists=True
            mock_instance.execute_sql.return_value = {
                "rows": [{"name": "test_catalog"}],
                "schema": ["name"]
            }
            
            result = await tool.execute(catalog_name="test_catalog")
            assert result["exists"] is True
            assert result["catalog_name"] == "test_catalog"
            mock_instance.execute_sql.assert_called_with("SHOW CATALOGS LIKE 'test_catalog'")
            
            # Test exists=False
            mock_instance.execute_sql.return_value = {
                "rows": [],
                "schema": ["name"]
            }
            
            result = await tool.execute(catalog_name="non_existent")
            assert result["exists"] is False
