import pytest
from datetime import datetime, timezone, timedelta
from app.db.report_subscription import ReportSubscription
from app.db.request import RequestModel
from app.workers.poller import process_scheduled_reports
from unittest.mock import patch, MagicMock

# Mock get_lakebase_session to use our test db_session
@pytest.fixture
def mock_db_session(db_session):
    # Prevent the code under test from closing the session
    db_session.close = MagicMock()
    with patch("app.workers.poller.get_db", side_effect=lambda: iter([db_session])) as mock:
        yield mock

@pytest.mark.asyncio
async def test_scheduled_report_execution(db_session, mock_db_session):
    """Test that due reports spawn requests and update their schedule."""

    # 1. Setup: Create a subscription due in the past
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=10)

    sub = ReportSubscription(
        id="test-sub-unit-1",
        name="UNIT_TEST_REPORT",
        subscribers="test@example.com",
        schedule_cron="*/5 * * * *", # Every 5 mins
        prompts=[{"label": "Test", "prompt": "Test Prompt"}],
        is_active=True,
        next_run_at=past,
        created_at=now
    )
    db_session.add(sub)
    db_session.commit()

    # 2. Action: Run the poller logic
    await process_scheduled_reports()

    # 3. Verification: Check Request Creation
    req = db_session.query(RequestModel).filter(
        RequestModel.title == "Report: UNIT_TEST_REPORT",
        RequestModel.type == "report_execution"
    ).first()

    assert req is not None, "Request should be created"
    assert req.state_context["subscription_id"] == "test-sub-unit-1"

    # 4. Verification: Check Schedule Update
    # Re-query the object to ensure we get the fresh state from DB
    updated_sub = db_session.query(ReportSubscription).filter(ReportSubscription.id == sub.id).first()
    assert updated_sub.last_run_at is not None
    # SQLite stores datetimes as naive strings, so we need to ensure we compare correctly.
    # Or just check that it's greater than the original next_run_at
    assert updated_sub.next_run_at.replace(tzinfo=timezone.utc) > past
