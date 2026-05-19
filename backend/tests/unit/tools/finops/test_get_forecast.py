import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.tools.finops.get_forecast import get_forecasted_spend
from datetime import datetime

@pytest.fixture
def tool():
    return get_forecasted_spend

@pytest.fixture
def mock_provider():
    with patch("app.tools.finops.get_forecast.DatabricksProvider") as MockProvider:
        provider_instance = MockProvider.return_value
        provider_instance.execute_sql = AsyncMock()
        yield provider_instance

@pytest.mark.asyncio
async def test_get_forecasted_spend(tool, mock_provider):
    # Mock date to be deterministically mid-month?
    with patch("app.tools.finops.get_forecast.datetime") as mock_datetime:
        from datetime import timezone
        mock_now = datetime(2023, 1, 15, tzinfo=timezone.utc)
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
        # remaining days = 16
        
        # 30 day cost mock (1000 total -> ~33.3/day)
        # MTD cost mock (500 total)
        
        mock_provider.execute_sql.side_effect = [
             {"rows": [{"total_cost": 900.0}]}, # 30 day (run rate = 30/day)
             {"rows": [{"total_cost": 450.0}]}  # MTD (15 days * 30 = 450)
        ]
        
        result = await tool.execute()
        
        # Daily rate = 900 / 30 = 30.0
        # Remaining = 16 days * 30.0 = 480.0
        # Forecast = 450.0 (MTD) + 480.0 = 930.0
        
        assert result["daily_run_rate"] == 30.0
        assert result["mtd_cost"] == 450.0
        assert result["forecast_total"] == 930.0
        
        # Verify 2 calls
        assert mock_provider.execute_sql.call_count == 2
        
        # Verify first call (30 day) has join and timeout
        args, kwargs = mock_provider.execute_sql.call_args_list[0]
        assert "JOIN system.billing.list_prices" in args[0]
        assert kwargs["timeout_seconds"] == 300
