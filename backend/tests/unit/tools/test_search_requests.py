import pytest
from unittest.mock import MagicMock, patch
from app.tools.search_requests import SearchRequestsTool
from tests.factories.request_factory import RequestFactory

class TestSearchRequestsTool:
    
    @pytest.fixture
    def tool(self, db_session):
        # We need to patch get_lakebase_session to return our test db_session
        # Since the tool calls it inside execute: db = get_lakebase_session()
        # We can patch the function in the module
        with patch("app.tools.search_requests.get_lakebase_session", return_value=db_session):
            yield SearchRequestsTool()

    @pytest.mark.asyncio
    async def test_search_by_title(self, tool, db_session):
        # Seed data
        RequestFactory.create(db_session, title="Finance Access Request", status="pending")
        RequestFactory.create(db_session, title="HR Data Access", status="completed")
        RequestFactory.create(db_session, title="Unrelated Thing", status="pending")
        
        # Execute
        result = await tool.execute(query="Finance")
        
        # Verify
        assert result["count"] == 1
        assert result["requests"][0]["title"] == "Finance Access Request"

    @pytest.mark.asyncio
    async def test_search_by_id(self, tool, db_session):
        # Seed data
        req = RequestFactory.create(db_session, id="req-123-abc", title="Target")
        RequestFactory.create(db_session, id="req-456-def", title="Other")
        
        # Execute
        result = await tool.execute(query="123")
        
        # Verify
        assert result["count"] == 1
        assert result["requests"][0]["id"] == "req-123-abc"

    @pytest.mark.asyncio
    async def test_filter_by_status(self, tool, db_session):
        # Seed data
        RequestFactory.create(db_session, title="A", status="pending")
        RequestFactory.create(db_session, title="B", status="completed")
        RequestFactory.create(db_session, title="C", status="pending")
        
        # Execute
        result = await tool.execute(status="pending")
        
        # Verify
        assert result["count"] == 2
        for req in result["requests"]:
            assert req["status"] == "pending"

    @pytest.mark.asyncio
    async def test_limit(self, tool, db_session):
        # Seed data
        for i in range(10):
            RequestFactory.create(db_session, title=f"Bulk {i}")
            
        # Execute
        result = await tool.execute(limit=3)
        
        # Verify
        assert result["count"] == 3
        # Should be newest first
        assert result["requests"][0]["title"] == "Bulk 9"

    @pytest.mark.asyncio
    async def test_no_results(self, tool, db_session):
        result = await tool.execute(query="Nonexistent")
        assert result["count"] == 0
        assert result["requests"] == []
