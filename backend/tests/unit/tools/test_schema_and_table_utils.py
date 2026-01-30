import pytest
from unittest.mock import MagicMock, patch
from app.tools.self_service.get_schema_list import GetSchemaListTool
from app.tools.self_service.get_table_list import GetTableListTool
from app.core.exceptions import RetryableError

class MockSchema:
    def __init__(self, name, comment=None, catalog_name="main", owner="owner"):
        self.name = name
        self.comment = comment
        self.catalog_name = catalog_name
        self.owner = owner

class MockTable:
    def __init__(self, name, comment=None, table_type="MANAGED", catalog_name="main", schema_name="default", owner="owner"):
        self.name = name
        self.comment = comment
        # Simulate enum or object with string representation
        self.table_type = MagicMock()
        self.table_type.value = table_type
        self.table_type.__str__ = lambda x: table_type
        
        self.catalog_name = catalog_name
        self.schema_name = schema_name
        self.owner = owner

class TestGetSchemaListTool:
    @pytest.fixture
    def tool(self):
        with patch("app.tools.self_service.get_schema_list.DatabricksProvider") as MockProvider:
            tool = GetSchemaListTool()
            tool._provider = MockProvider.return_value
            return tool

    @pytest.mark.asyncio
    async def test_execute_success(self, tool):
        tool.provider.client.schemas.list.return_value = [
            MockSchema("schema1", "comment1"),
            MockSchema("schema2")
        ]
        
        result = await tool.execute(catalog_name="main")
        
        assert result["count"] == 2
        assert result["schemas"][0]["name"] == "schema1"
        tool.provider.client.schemas.list.assert_called_with(catalog_name="main")

class TestGetTableListTool:
    @pytest.fixture
    def tool(self):
        with patch("app.tools.self_service.get_table_list.DatabricksProvider") as MockProvider:
            tool = GetTableListTool()
            tool._provider = MockProvider.return_value
            return tool

    @pytest.mark.asyncio
    async def test_execute_success(self, tool):
        tool.provider.client.tables.list.return_value = [
            MockTable("table1", "c1", "MANAGED"),
            MockTable("view1", "c2", "VIEW")
        ]
        
        result = await tool.execute(catalog_name="main", schema_name="default")
        
        assert result["count"] == 2
        assert result["tables"][0]["name"] == "table1"
        assert result["tables"][1]["table_type"] == "VIEW"
        tool.provider.client.tables.list.assert_called_with(catalog_name="main", schema_name="default")
