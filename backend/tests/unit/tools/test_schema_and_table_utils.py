import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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

@pytest.fixture
def mock_provider():
    # Both tools resolve their Unity Catalog connection through uc_client_for,
    # which returns (provider, client) bound to the caller's identity. Patching
    # that seam keeps the tests free of workspace config and creds.
    provider = MagicMock()
    provider.execute_sql = AsyncMock(return_value={"rows": []})
    with patch("app.core.workspaces.uc_client_for", return_value=(provider, provider.client)):
        yield provider


class TestGetSchemaListTool:
    @pytest.fixture
    def tool(self):
        return get_schema_list

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

    @pytest.mark.asyncio
    async def test_tags_are_attached_to_the_owning_table(self, tool, mock_provider):
        mock_provider.client.tables.list.return_value = [
            MockTable("table1", "c1", "MANAGED"),
            MockTable("table2", "c2", "MANAGED"),
        ]
        mock_provider.execute_sql.return_value = {"rows": [
            {"table_name": "table1", "tag_name": "pii", "tag_value": "true"},
            {"table_name": "table1", "tag_name": "tier", "tag_value": "gold"},
        ]}

        result = await tool.execute(target_host="https://test.azuredatabricks.net", catalog_name="main", schema_name="default")

        assert result["tables"][0]["tags"] == {"pii": "true", "tier": "gold"}
        assert result["tables"][1]["tags"] == {}

    @pytest.mark.asyncio
    async def test_tag_lookup_failure_does_not_fail_the_listing(self, tool, mock_provider):
        """Tags come from a separate system-table query the caller may not be
        able to read; losing them must not cost the whole listing."""
        mock_provider.client.tables.list.return_value = [MockTable("table1", "c1", "MANAGED")]
        mock_provider.execute_sql.side_effect = Exception("PERMISSION_DENIED on system.information_schema")

        result = await tool.execute(target_host="https://test.azuredatabricks.net", catalog_name="main", schema_name="default")

        assert result["count"] == 1
        assert result["tables"][0]["tags"] == {}
