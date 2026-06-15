"""Tests for the shared SQL-safety helpers used by query-building tools."""
from __future__ import annotations

import pytest

from app.tools.sql_safety import (
    SqlSafetyError,
    quote_literal,
    reject_dangerous_snippet,
    require_date,
    require_identifier,
    require_identifiers,
    valid_date,
    valid_identifier,
)


def test_quote_literal_escapes_single_quotes():
    assert quote_literal("o'brien") == "'o''brien'"
    # A break-out attempt is neutralized into a single quoted literal.
    assert quote_literal("x' OR '1'='1") == "'x'' OR ''1''=''1'"


@pytest.mark.parametrize("good", ["2026-01-02", "1999-12-31"])
def test_valid_date_true(good):
    assert valid_date(good)


@pytest.mark.parametrize("bad", ["2026-1-2", "2026/01/02", "today", "2026-01-02'; DROP", ""])
def test_valid_date_false(bad):
    assert not valid_date(bad)
    with pytest.raises(SqlSafetyError):
        require_date(bad, "d")


@pytest.mark.parametrize("good", ["col", "a.b", "user_identity.email", "main.default.t"])
def test_valid_identifier_true(good):
    assert valid_identifier(good)


@pytest.mark.parametrize("bad", ["1col", "a;b", "a-b", "drop table", "a','b", "tbl)"])
def test_valid_identifier_false(bad):
    assert not valid_identifier(bad)
    with pytest.raises(SqlSafetyError):
        require_identifier(bad, "id")


def test_require_identifiers_rejects_any_bad():
    assert require_identifiers(["a", "b.c"], "cols") == ["a", "b.c"]
    with pytest.raises(SqlSafetyError):
        require_identifiers(["ok", "bad;"], "cols")


@pytest.mark.parametrize(
    "bad",
    [
        "1=1; DROP TABLE x",
        "1=1 -- comment",
        "1=1 /* c */",
        "x = (SELECT 1) UNION INSERT",
        "DELETE FROM y",
    ],
)
def test_reject_dangerous_snippet(bad):
    with pytest.raises(SqlSafetyError):
        reject_dangerous_snippet(bad, "additional_where")


def test_reject_dangerous_snippet_allows_plain_filter():
    assert reject_dangerous_snippet("response.statusCode = '200'", "w") == "response.statusCode = '200'"
