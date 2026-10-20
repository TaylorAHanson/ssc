"""SQL statement builders for UC tags and comments (ADR-0006 D3). Workstream C.

Moved from ``workflows/tag_plan.py`` and extended with comment forms for tables,
views, materialized views, streaming tables and columns. Every name part is
backtick-quoted and every value is a single-quoted literal.

Statement forms (Databricks SQL):

* catalog, schema and relation tags: ``ALTER {CATALOG|SCHEMA|TABLE|VIEW|MATERIALIZED
  VIEW|STREAMING TABLE} name SET TAGS ('k' = 'v', ...)`` / ``UNSET TAGS ('k', ...)``.
  Metric views are views. A key-only tag is written ``'k' = ''``.
* a column of a table, materialized view or streaming table: ``ALTER <type> t ALTER
  COLUMN `col` SET TAGS (...)`` / ``UNSET TAGS (...)``.
* a column of a view or metric view: ``ALTER VIEW`` has no ``ALTER COLUMN`` form, so
  it's ``SET TAG ON COLUMN t.`col` `k` = `v``` / ``UNSET TAG ON COLUMN t.`col` `k```,
  one tag per statement. Keys and values are identifiers there, a key-only tag has no
  ``= value``, and ``SET TAG`` refuses to overwrite an existing key (unset it first).
* comments: ``COMMENT ON {CATALOG|SCHEMA|TABLE|COLUMN} name IS 'x' | NULL``.
  ``COMMENT ON TABLE`` covers tables, views, materialized views and streaming tables,
  and ``COMMENT ON COLUMN`` covers columns of all of them.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# The ALTER keyword for each securable type that can be tagged.
_ALTER_KEYWORDS: Dict[str, str] = {
    "catalog": "CATALOG",
    "schema": "SCHEMA",
    "table": "TABLE",
    "view": "VIEW",
    "metric_view": "VIEW",
    "materialized_view": "MATERIALIZED VIEW",
    "streaming_table": "STREAMING TABLE",
}
VIEW_TYPES = ("view", "metric_view")
_NAME_PARTS = {"catalog": 1, "schema": 2}


def quote_identifier(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def quote_fqn(fqn: str) -> str:
    """``catalog``, ``catalog.schema`` or ``catalog.schema.table``, each part quoted."""
    parts = [p.strip() for p in (fqn or "").strip().split(".")]
    if not 1 <= len(parts) <= 3 or not all(parts):
        raise ValueError(f"Expected catalog, catalog.schema or catalog.schema.table, got: '{fqn}'")
    return ".".join(quote_identifier(p) for p in parts)


def sql_string_literal(value: str) -> str:
    """A Databricks SQL string literal.

    Backslash is an escape character in Databricks string literals, and two
    adjacent literals concatenate, so ``'It''s'`` reads as ``Its``. Escape the
    backslash first, then the quote.
    """
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def alter_keyword(securable_type: str) -> str:
    """CATALOG, SCHEMA, TABLE, VIEW, MATERIALIZED VIEW or STREAMING TABLE."""
    keyword = _ALTER_KEYWORDS.get(securable_type)
    if keyword is None:
        raise ValueError(f"No ALTER statement for securable type '{securable_type}'.")
    return keyword


def _relation_type(securable_type: str, column: Optional[str]) -> str:
    """The type whose statement form applies; for a column, ``securable_type`` is its relation's."""
    if column is not None and securable_type == "column":
        return "table"
    return securable_type


def _target(securable_type: str, name: str, column: Optional[str]) -> str:
    """The quoted name, refusing a name whose part count doesn't match the type.

    ``ALTER TABLE `c``` would otherwise address a table ``c`` in the session's schema.
    """
    quoted = quote_fqn(name)
    parts = len(name.strip().split("."))
    expected = _NAME_PARTS.get(securable_type, 3)
    if parts != expected:
        raise ValueError(f"A {securable_type} needs a {expected}-part name, got '{name}'.")
    if column is not None and securable_type in _NAME_PARTS:
        raise ValueError(f"Only tables and views have columns, got a {securable_type} '{name}'.")
    if column is not None and not str(column).strip():
        raise ValueError(f"Empty column name on '{name}'.")
    return quoted


def _tag_value(value: Optional[str]) -> str:
    return "" if value is None else str(value)


def build_tag_statement(
    securable_type: str,
    name: str,
    operation: str,
    *,
    tags: Optional[Dict[str, Optional[str]]] = None,
    keys: Optional[List[str]] = None,
    column: Optional[str] = None,
) -> List[str]:
    """``ALTER ... SET TAGS`` / ``UNSET TAGS`` (or the one-tag-per-statement view-column form).

    ``operation`` is ``set`` (uses ``tags``; a None value is a key-only tag) or ``unset``
    (uses ``keys``). With ``column``, ``securable_type`` is the column's relation type
    (``column`` itself is read as ``table``). Nothing to set or unset returns ``[]``.
    Raises ValueError for an unknown type or operation, or a name that doesn't fit the type.
    """
    if operation not in ("set", "unset"):
        raise ValueError(f"Unknown tag operation '{operation}'; use 'set' or 'unset'.")
    relation_type = _relation_type(securable_type, column)
    keyword = alter_keyword(relation_type)
    target = _target(relation_type, name, column)
    if operation == "set":
        pairs = list((tags or {}).items())
        if not pairs:
            return []
    else:
        unset_keys = list(keys or [])
        if not unset_keys:
            return []

    if column is not None and relation_type in VIEW_TYPES:
        on_column = f"{target}.{quote_identifier(column)}"
        if operation == "unset":
            return [f"UNSET TAG ON COLUMN {on_column} {quote_identifier(k)};" for k in unset_keys]
        statements = []
        for key, value in pairs:
            text = _tag_value(value)
            assignment = f"{quote_identifier(key)} = {quote_identifier(text)}" if text else quote_identifier(key)
            statements.append(f"SET TAG ON COLUMN {on_column} {assignment};")
        return statements

    if column is not None:
        target = f"{target} ALTER COLUMN {quote_identifier(column)}"
    if operation == "set":
        body = ", ".join(f"{sql_string_literal(k)} = {sql_string_literal(_tag_value(v))}" for k, v in pairs)
        return [f"ALTER {keyword} {target} SET TAGS ({body});"]
    body = ", ".join(sql_string_literal(k) for k in unset_keys)
    return [f"ALTER {keyword} {target} UNSET TAGS ({body});"]


def build_comment_statement(securable_type: str, name: str, comment: Optional[str], *, column: Optional[str] = None) -> str:
    """``COMMENT ON {CATALOG|SCHEMA|TABLE|COLUMN} ... IS 'x' | NULL``.

    Every relation type takes ``COMMENT ON TABLE`` (there's no ``COMMENT ON VIEW``), and a
    column of any relation takes ``COMMENT ON COLUMN catalog.schema.table.column``. None or
    ``""`` clears the comment.
    """
    relation_type = _relation_type(securable_type, column)
    alter_keyword(relation_type)
    target = _target(relation_type, name, column)
    value = sql_string_literal(comment) if comment else "NULL"
    if column is not None:
        return f"COMMENT ON COLUMN {target}.{quote_identifier(column)} IS {value};"
    keyword = {"catalog": "CATALOG", "schema": "SCHEMA"}.get(relation_type, "TABLE")
    return f"COMMENT ON {keyword} {target} IS {value};"
