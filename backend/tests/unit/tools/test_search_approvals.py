import pytest
from unittest.mock import patch
from app.tools.self_service.search_approvals import search_approvals
from tests.factories.request_factory import RequestFactory
from tests.factories.approval_factory import ApprovalFactory

class TestSearchApprovalsTool:
    
    @pytest.fixture
    def tool(self):
        return search_approvals

    @pytest.mark.asyncio
    async def test_search_by_type(self, tool, db_session):
        # We need to patch get_lakebase_session for each execution or via a bigger scope
        # With functional tool, it calls get_lakebase_session() inside the body.
        with patch("app.tools.self_service.search_approvals.get_lakebase_session", return_value=db_session): 
            # Setup
            req1 = RequestFactory.create(db_session, title="Req 1")
            req2 = RequestFactory.create(db_session, title="Req 2")
            
            ApprovalFactory.create(db_session, request_id=req1.id, approval_type="manager")
            ApprovalFactory.create(db_session, request_id=req2.id, approval_type="data_owner")
            
            # Execute
            result = await tool.execute(approval_type="manager")
            
            # Verify
            assert result["count"] == 1
            assert result["approvals"][0]["approval_type"] == "manager"
            assert result["approvals"][0]["request_title"] == "Req 1"

    @pytest.mark.asyncio
    async def test_search_by_status(self, tool, db_session):
        with patch("app.tools.self_service.search_approvals.get_lakebase_session", return_value=db_session):
            # Setup
            req = RequestFactory.create(db_session)
            ApprovalFactory.create(db_session, request_id=req.id, status="pending")
            ApprovalFactory.create(db_session, request_id=req.id, status="approved")
            
            # Execute
            result = await tool.execute(status="pending")
            
            # Verify
            assert result["count"] == 1
            assert result["approvals"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_search_by_request_id(self, tool, db_session):
         with patch("app.tools.self_service.search_approvals.get_lakebase_session", return_value=db_session):
            # Setup
            req1 = RequestFactory.create(db_session)
            req2 = RequestFactory.create(db_session)
            
            ApprovalFactory.create(db_session, request_id=req1.id, approval_type="manager")
            ApprovalFactory.create(db_session, request_id=req2.id, approval_type="manager")
            
            # Execute
            result = await tool.execute(request_id=req1.id)
            
            # Verify
            assert result["count"] == 1
            assert result["approvals"][0]["request_id"] == req1.id

    @pytest.mark.asyncio
    async def test_no_results(self, tool, db_session):
         with patch("app.tools.self_service.search_approvals.get_lakebase_session", return_value=db_session):
            result = await tool.execute(approval_type="platform_admin")
            assert result["count"] == 0
