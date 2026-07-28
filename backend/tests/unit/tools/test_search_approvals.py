"""Tests for the search_approvals tool.

Every call passes an identity. The tool is scoped to what the caller is entitled
to see, and the identity kwargs are how the ToolExecutor supplies that — a call
without them is refused rather than answered from the whole table.
"""
import pytest
from unittest.mock import patch
from app.tools.self_service.search_approvals import search_approvals
from tests.factories.request_factory import RequestFactory
from tests.factories.approval_factory import ApprovalFactory

# A Platform Admin sees every approval type, which keeps the filtering-behavior
# tests below focused on the filter under test.
ADMIN = {"_user_email": "admin@example.com", "_user_roles": "Platform Admin"}


class TestSearchApprovalsTool:
    
    @pytest.fixture
    def tool(self):
        return search_approvals

    @pytest.mark.asyncio
    async def test_search_by_type(self, tool, db_session):
        # We need to patch get_lakebase_session for each execution or via a bigger scope
        # With functional tool, it calls get_lakebase_session() inside the body.
        with patch("app.tools.self_service.search_approvals.get_db", side_effect=lambda: iter([db_session])):
            # Setup
            req1 = RequestFactory.create(db_session, title="Req 1")
            req2 = RequestFactory.create(db_session, title="Req 2")
            
            ApprovalFactory.create(db_session, request_id=req1.id, approval_type="manager")
            ApprovalFactory.create(db_session, request_id=req2.id, approval_type="data_owner")
            
            # Execute
            result = await tool.execute(approval_type="manager", **ADMIN)
            
            # Verify
            assert result["count"] == 1
            assert result["approvals"][0]["approval_type"] == "manager"
            assert result["approvals"][0]["request_title"] == "Req 1"

    @pytest.mark.asyncio
    async def test_search_by_status(self, tool, db_session):
        with patch("app.tools.self_service.search_approvals.get_db", side_effect=lambda: iter([db_session])):
            # Setup
            req = RequestFactory.create(db_session)
            ApprovalFactory.create(db_session, request_id=req.id, status="pending")
            ApprovalFactory.create(db_session, request_id=req.id, status="approved")
            
            # Execute
            result = await tool.execute(status="pending", **ADMIN)
            
            # Verify
            assert result["count"] == 1
            assert result["approvals"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_search_by_request_id(self, tool, db_session):
         with patch("app.tools.self_service.search_approvals.get_db", side_effect=lambda: iter([db_session])):
            # Setup
            req1 = RequestFactory.create(db_session)
            req2 = RequestFactory.create(db_session)
            
            ApprovalFactory.create(db_session, request_id=req1.id, approval_type="manager")
            ApprovalFactory.create(db_session, request_id=req2.id, approval_type="manager")
            
            # Execute
            result = await tool.execute(request_id=req1.id, **ADMIN)
            
            # Verify
            assert result["count"] == 1
            assert result["approvals"][0]["request_id"] == req1.id

    @pytest.mark.asyncio
    async def test_no_results(self, tool, db_session):
         with patch("app.tools.self_service.search_approvals.get_db", side_effect=lambda: iter([db_session])):
            result = await tool.execute(approval_type="platform_admin", **ADMIN)
            assert result["count"] == 0


class TestSearchApprovalsScoping:
    """The tool must never answer more broadly than the caller is entitled to."""

    @pytest.fixture
    def tool(self):
        return search_approvals

    @pytest.mark.asyncio
    async def test_a_call_without_an_identity_is_refused(self, tool, db_session):
        """Fail closed.

        This used to skip the visibility filter entirely, so an unidentified
        caller received every approval in the system — other people's request
        titles and requester addresses included.
        """
        with patch("app.tools.self_service.search_approvals.get_db", side_effect=lambda: iter([db_session])):
            req = RequestFactory.create(db_session, title="Somebody else's request")
            ApprovalFactory.create(db_session, request_id=req.id, approval_type="manager")

            result = await tool.execute(status="pending")

            assert result["status"] == "error"
            assert "approvals" not in result

    @pytest.mark.asyncio
    async def test_a_caller_without_roles_sees_only_their_own_assignments(self, tool, db_session):
        """No roles is not a reason to show everything.

        An identified caller with no approver role still gets a scoped answer:
        the approvals actually routed to them, and nothing else.
        """
        with patch("app.tools.self_service.search_approvals.get_db", side_effect=lambda: iter([db_session])):
            mine = RequestFactory.create(db_session, title="Mine")
            theirs = RequestFactory.create(db_session, title="Theirs")
            ApprovalFactory.create(
                db_session, request_id=mine.id, assigned_to_email="me@example.com"
            )
            ApprovalFactory.create(
                db_session, request_id=theirs.id, assigned_to_email="someone@example.com"
            )

            result = await tool.execute(status="pending", _user_email="me@example.com")

            assert result["count"] == 1
            assert result["approvals"][0]["request_title"] == "Mine"

    @pytest.mark.asyncio
    async def test_group_assigned_approvals_need_the_matching_entitlement(self, tool, db_session):
        with patch("app.tools.self_service.search_approvals.get_db", side_effect=lambda: iter([db_session])):
            req = RequestFactory.create(db_session, title="Owned by a group")
            ApprovalFactory.create(
                db_session, request_id=req.id, assigned_to_role="data-stewards"
            )

            outsider = await tool.execute(
                status="pending", _user_email="me@example.com", _user_entitlements="analysts"
            )
            assert outsider["count"] == 0

            member = await tool.execute(
                status="pending",
                _user_email="me@example.com",
                _user_entitlements="analysts,data-stewards",
            )
            assert member["count"] == 1
