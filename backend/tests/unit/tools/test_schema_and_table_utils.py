import pytest
from unittest.mock import MagicMock, patch
from app.tools.self_service.get_schema_list import get_schema_list
from app.tools.self_service.get_table_list import get_table_list
from app.core.exceptions import RetryableError

class MockSchema:
    def __init__(self, name, comment=None, catalog_name="main", owner="owner", properties=None):
        self.name = name
        self.comment = comment
        self.catalog_name = catalog_name
        self.owner = owner
        self.properties = properties

class MockTable:
    def __init__(self, name, comment=None, table_type="MANAGED", catalog_name="main", schema_name="default", owner="owner", properties=None):
        self.name = name
        self.comment = comment
        # Simulate enum or object with string representation
        self.table_type = MagicMock()
        self.table_type.value = table_type
        self.table_type.__str__ = lambda x: table_type
        
        self.catalog_name = catalog_name
        self.schema_name = schema_name
        self.owner = owner
        self.properties = properties

class TestGetSchemaListTool:
    @pytest.fixture
    def tool(self):
        return get_schema_list
    
    @pytest.fixture
    def mock_provider(self):
        with patch("app.tools.self_service.get_schema_list.DatabricksProvider") as MockProvider:
            with patch("app.core.workspaces.get_workspace_config") as mock_ws_config:
                mock_ws_config.return_value = MagicMock(host="https://test.azuredatabricks.net", token="test", client_id=None, client_secret=None)
                yield MockProvider.return_value

    @pytest.mark.asyncio
    async def test_execute_success(self, tool, mock_provider):
        mock_provider.client.schemas.list.return_value = [
            MockSchema("schema1", "comment1"),
            MockSchema("schema2")
        ]
        
        result = await tool.execute(target_host="https://test.azuredatabricks.net", catalog_name="main")
        
        assert result["count"] == 2
        assert result["schemas"][0]["name"] == "schema1"
        mock_provider.client.schemas.list.assert_called_with(catalog_name="main")

class TestGetTableListTool:
    @pytest.fixture
    def tool(self):
        return get_table_list
    
    @pytest.fixture
    def mock_provider(self):
        with patch("app.tools.self_service.get_table_list.DatabricksProvider") as MockProvider:
            with patch("app.core.workspaces.get_workspace_config") as mock_ws_config:
                mock_ws_config.return_value = MagicMock(host="https://test.azuredatabricks.net", token="test", client_id=None, client_secret=None)
                yield MockProvider.return_value

    @pytest.mark.asyncio
    async def test_execute_success(self, tool, mock_provider):
        mock_provider.client.tables.list.return_value = [
            MockTable("table1", "c1", "MANAGED"),
            MockTable("view1", "c2", "VIEW")
        ]
        
        result = await tool.execute(target_host="https://test.azuredatabricks.net", catalog_name="main", schema_name="default")
        
        assert result["count"] == 2
        assert result["tables"][0]["name"] == "table1"
        assert result["tables"][1]["table_type"] == "VIEW"
        mock_provider.client.tables.list.assert_called_with(catalog_name="main", schema_name="default")
