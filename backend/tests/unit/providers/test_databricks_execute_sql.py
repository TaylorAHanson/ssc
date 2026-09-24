"""execute_sql returns quick statements inline and doesn't retry statements that can't succeed."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import PermanentError, RetryableError
from app.providers.databricks import DatabricksProvider


def _status(state, code=None, message=""):
    error = SimpleNamespace(error_code=SimpleNamespace(value=code), message=message) if code else None
    return SimpleNamespace(state=SimpleNamespace(value=state), error=error)


def _response(state, code=None, message="", rows=None):
    return SimpleNamespace(
        statement_id="st-1",
        status=_status(state, code, message),
        manifest=SimpleNamespace(schema=SimpleNamespace(columns=[SimpleNamespace(name="n")])),
        result=SimpleNamespace(data_array=rows) if rows is not None else None,
    )


def _provider(statements):
    with patch("app.providers.databricks.client._build_workspace_client", return_value=MagicMock()):
        provider = DatabricksProvider(host="https://h", token="t", config={"warehouse_id": "wh"})
    provider.client.statement_execution = statements
    return provider


def test_quick_statement_returns_inline_without_polling():
    statements = MagicMock()
    statements.execute_statement.return_value = _response("SUCCEEDED", rows=[["1"]])

    out = asyncio.run(_provider(statements).execute_sql("SELECT 1"))

    assert out["rows"] == [{"n": "1"}]
    assert statements.execute_statement.call_args.kwargs["wait_timeout"] == "5s"
    statements.get_statement.assert_not_called()


def test_slow_statement_is_polled_until_done():
    statements = MagicMock()
    statements.execute_statement.return_value = _response("RUNNING")
    statements.get_statement.side_effect = [_response("RUNNING"), _response("SUCCEEDED", rows=[["2"]])]

    with patch("app.providers.databricks.client.asyncio.sleep", return_value=None) as sleep:
        out = asyncio.run(_provider(statements).execute_sql("SELECT 2"))

    assert out["rows"] == [{"n": "2"}]
    assert [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0]


def test_bad_request_fails_once_without_retrying():
    statements = MagicMock()
    statements.execute_statement.return_value = _response(
        "FAILED", "BAD_REQUEST", "PERMISSION_DENIED: User does not have SELECT on Table 'a.b.c'")

    with pytest.raises(PermanentError, match="does not have SELECT"):
        asyncio.run(_provider(statements).execute_sql("SELECT * FROM a.b.c"))
    assert statements.execute_statement.call_count == 1


def test_transient_failure_is_still_retried():
    statements = MagicMock()
    statements.execute_statement.return_value = _response("FAILED", "TEMPORARILY_UNAVAILABLE", "busy")

    with patch("tenacity.nap.time.sleep", return_value=None), \
         patch("asyncio.sleep", return_value=None):
        with pytest.raises(RetryableError):
            asyncio.run(_provider(statements).execute_sql("SELECT 1"))
    assert statements.execute_statement.call_count == 3
