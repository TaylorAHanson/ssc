
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.state_machines.report_execution import ReportExecutionStateMachine
from app.db.request import RequestModel
from app.models.request import RequestType

@pytest.fixture
def mock_db():
    session = MagicMock()
    query_mock = MagicMock()
    
    # Setup chaining: query() -> query_mock
    session.query.return_value = query_mock
    
    # query_mock.filter() -> query_mock
    query_mock.filter.return_value = query_mock
    
    # query_mock.order_by() -> query_mock
    query_mock.order_by.return_value = query_mock
    
    # query_mock.all() -> []
    query_mock.all.return_value = []
    
    # query_mock.first() -> None
    query_mock.first.return_value = None
    
    return session

@pytest.fixture
def sample_request():
    return RequestModel(
        id="req-test-async",
        type=RequestType.REPORT_EXECUTION.value,
        title="Async Test Report",
        status="provisioning",
        current_state="distribute",
        state_context={
            "name": "Test Report",
            "subscribers": "user@example.com",
            "final_report_html": "<html><body><h1>Report</h1></body></html>"
        }
    )

@pytest.mark.asyncio
async def test_on_enter_distribute_async_sends_html_email(mock_db, sample_request):
    """Verify that on_enter_distribute_async calls send_email with is_html=True."""
    
    # Setup State Machine
    sm = ReportExecutionStateMachine(sample_request, mock_db)
    
    # Mock NotificationProvider
    with patch("app.state_machines.report_execution.NotificationProvider") as MockProvider:
        mock_instance = MockProvider.return_value
        mock_instance.send_email = AsyncMock(return_value=True)
        
        # Execute the async handler
        await sm.on_enter_distribute_async()
        
        # Verify send_email was called correctly
        mock_instance.send_email.assert_called_once()
        call_args = mock_instance.send_email.call_args
        
        # Check arguments (args and kwargs)
        _, kwargs = call_args
        
        assert kwargs.get("to") == "user@example.com"
        assert kwargs.get("subject") == "Report: Test Report"
        assert kwargs.get("body") == "<html><body><h1>Report</h1></body></html>"
        assert kwargs.get("is_html") is True

@pytest.mark.asyncio
async def test_on_enter_assemble_report_async_generates_html(mock_db, sample_request):
    """Verify that assemble_report generates HTML fragment from results."""
    
    # Update request context for this test
    sample_request.state_context["report_results"] = [
        {"label": "Section 1", "html": "<p>Content 1</p>"}
    ]
    sample_request.current_state = "assemble_report"
    
    sm = ReportExecutionStateMachine(sample_request, mock_db)
    
    # Execute
    await sm.on_enter_assemble_report_async()
    
    # Verify state_context update (mock_db.add called for the request)
    mock_db.add.assert_any_call(sample_request)
    
    updated_context = sample_request.state_context
    assert "final_report_html" in updated_context
    assert "Content 1" in updated_context["final_report_html"]
    # We now generate fragments
    assert "report-header" in updated_context["final_report_html"]

@pytest.mark.asyncio
async def test_on_enter_execute_prompts_async_uses_runner(mock_db, sample_request):
    """Verify that execute_prompts calls AgentRunner."""
    
    sample_request.state_context["prompts"] = [
        {"label": "Test Label", "prompt": "Run this"}
    ]
    sample_request.current_state = "execute_prompts"
    
    sm = ReportExecutionStateMachine(sample_request, mock_db)
    
    with patch("app.state_machines.report_execution.AgentRunner") as MockRunner:
        mock_instance = MockRunner.return_value
        mock_instance.run = AsyncMock(return_value={
            "content": "<table>Results</table>",
            "tool_calls": []
        })
        
        await sm.on_enter_execute_prompts_async()
        
        # Verify runner was initialized and called
        MockRunner.assert_called_once()
        mock_instance.run.assert_called_once_with(query="Run this")
        
        # Verify results in context
        results = sample_request.state_context.get("report_results", [])
        assert len(results) == 1
        assert results[0]["label"] == "Test Label"
        assert "Results" in results[0]["html"]
