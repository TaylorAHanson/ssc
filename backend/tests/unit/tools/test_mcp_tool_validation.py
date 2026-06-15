"""McpTool.execute should enforce the pydantic args_schema (a security boundary).

Tools interpolate args into SQL, so ``Literal``/bounded fields must be validated,
not merely advertised. These tests pin that behavior plus pass-through of
executor-injected context (underscore keys).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from pydantic import BaseModel, Field
from typing_extensions import Literal

from app.tools.mcp import tool


class _Args(BaseModel):
    scope: Literal["table", "schema"] = "table"
    limit: int = Field(10, ge=1, le=100)


@tool(name="_probe", args_schema=_Args, side_effect_class="read")
async def _probe(scope: str = "table", limit: int = 10, **kwargs: Any) -> Dict[str, Any]:
    return {"ok": True, "scope": scope, "limit": limit, "ctx": kwargs.get("_obo_token")}


@pytest.mark.asyncio
async def test_valid_args_pass_and_coerce():
    # "25" (string) should coerce to int 25 via the schema.
    res = await _probe.execute(scope="schema", limit="25", _obo_token="tok")
    assert res["ok"] is True
    assert res["scope"] == "schema"
    assert res["limit"] == 25
    assert res["ctx"] == "tok"  # injected context passes through untouched


@pytest.mark.asyncio
async def test_invalid_literal_is_rejected():
    res = await _probe.execute(scope="DROP TABLE users; --", limit=5)
    assert "error" in res
    assert "ok" not in res


@pytest.mark.asyncio
async def test_out_of_bounds_number_is_rejected():
    res = await _probe.execute(limit=9999)
    assert "error" in res


@pytest.mark.asyncio
async def test_omitted_args_use_function_defaults():
    res = await _probe.execute(_obo_token="tok")
    assert res["scope"] == "table"
    assert res["limit"] == 10
