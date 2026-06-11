"""Unit tests for the poller's retryable/permanent error handling.

These cover the contract the user cares about: a retryable error escalates to a
*permanent* failure once it has exhausted its retry budget, a permanent error
fails immediately, and neither path leaves a request in a state the poller would
keep re-selecting (which previously caused an infinite loop when the failure
handler itself crashed with an ``UnboundLocalError``).
"""
import pytest

from app.workers import poller
from app.models.request import RequestStatus
from app.core.exceptions import RetryableError, PermanentError
from tests.factories.request_factory import RequestFactory


@pytest.mark.asyncio
async def test_retryable_error_retries_within_budget(db_session):
    req = RequestFactory.create(
        db_session, status="processing", retry_count=0, max_retries=3
    )

    await poller._handle_retryable_error(
        db_session, req, RetryableError("transient blip"), "worker-1"
    )

    db_session.refresh(req)
    assert req.retry_count == 1
    # Still within budget -> stays processing for the next poll cycle.
    assert req.status == "processing"
    assert req.last_error["error"] == "transient blip"


@pytest.mark.asyncio
async def test_retryable_error_escalates_to_permanent_when_exhausted(db_session):
    # One attempt away from the cap.
    req = RequestFactory.create(
        db_session, status="processing", retry_count=2, max_retries=3
    )

    await poller._handle_retryable_error(
        db_session, req, RetryableError("keeps failing"), "worker-1"
    )

    db_session.refresh(req)
    assert req.status == RequestStatus.FAILED.value
    # Budget is exhausted so the poller's max-retries guard skips it from now on.
    assert req.retry_count >= req.max_retries
    assert req.last_error["permanent"] is False


@pytest.mark.asyncio
async def test_permanent_error_fails_immediately(db_session):
    req = RequestFactory.create(
        db_session, status="processing", retry_count=0, max_retries=3
    )

    # The previous UnboundLocalError bug made this raise instead of failing the
    # request, so simply completing without an exception is the regression check.
    await poller._handle_permanent_error(
        db_session, req, PermanentError("not recoverable"), "worker-1"
    )

    db_session.refresh(req)
    assert req.status == RequestStatus.FAILED.value
    assert req.retry_count >= req.max_retries
    assert req.last_error["permanent"] is True


@pytest.mark.asyncio
async def test_mark_failed_sets_failed_state_in_memory(db_session):
    """``_mark_request_failed`` sets FAILED + exhausted retries on the instance.

    (The audit-row-failure recovery path performs an internal rollback, which the
    single-transaction test fixture can't model, so this asserts the in-memory
    state the poller relies on rather than re-exercising that recovery branch.)
    """
    req = RequestFactory.create(
        db_session, status="processing", retry_count=1, max_retries=3
    )

    poller._mark_request_failed(
        db_session, req, PermanentError("boom"), "worker-1",
        failure_type="permanent_error", permanent=True,
    )

    assert req.status == RequestStatus.FAILED.value
    assert req.retry_count >= req.max_retries
    assert req.last_error["permanent"] is True
