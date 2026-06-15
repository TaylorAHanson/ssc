"""Small SQL-safety helpers for tools that build queries by string interpolation.

The agent's read tools query system tables by interpolating LLM-supplied values
(dates, identifiers, filter snippets) into SQL. Even though those queries run
read-only and On-Behalf-Of the user (so Unity Catalog bounds the blast radius),
unvalidated interpolation is an injection surface — a prompt-injected value could
smuggle a subquery or break out of a string literal. These helpers give callers a
cheap, consistent way to validate/escape the few free-form values that can't be
expressed as a ``Literal`` in the tool's args schema.

Prefer pydantic ``Literal`` constraints in the tool's ``args_schema`` for fixed
choices (those are now enforced in ``McpTool.execute``); use these helpers for the
inherently free-form values (dates, column/table identifiers, raw WHERE snippets).
"""
from __future__ import annotations

import re
from typing import Iterable, List

# A SQL identifier or dotted path (``col``, ``a.b``, ``user_identity.email``).
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Tokens that have no business inside a read-only WHERE snippet — statement
# stacking, comments, or any DML/DDL/permission verb.
_DANGEROUS_RE = re.compile(
    r"(;|--|/\*|\*/|\b("
    r"insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|"
    r"copy|call|execute|replace)\b)",
    re.IGNORECASE,
)


class SqlSafetyError(ValueError):
    """Raised when an interpolated SQL fragment fails a safety check."""


def quote_literal(value: str) -> str:
    """Return ``value`` wrapped as a safe single-quoted SQL string literal.

    Doubles embedded single quotes (the SQL escape) so the value can't terminate
    the literal early. Backslashes are left as-is (Databricks SQL string literals
    are not C-escaped by default).
    """
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def valid_date(value: str) -> bool:
    """True if ``value`` is a bare ``YYYY-MM-DD`` date (no time, no quotes)."""
    return bool(_DATE_RE.match(value or ""))


def require_date(value: str, field: str) -> str:
    if not valid_date(value):
        raise SqlSafetyError(f"{field} must be a YYYY-MM-DD date, got {value!r}")
    return value


def valid_identifier(value: str) -> bool:
    """True if ``value`` is a safe (optionally dotted) SQL identifier."""
    return bool(_IDENTIFIER_RE.match(value or ""))


def require_identifier(value: str, field: str) -> str:
    if not valid_identifier(value):
        raise SqlSafetyError(f"{field} must be a simple SQL identifier, got {value!r}")
    return value


def require_identifiers(values: Iterable[str], field: str) -> List[str]:
    out: List[str] = []
    for v in values or []:
        out.append(require_identifier(v, field))
    return out


def reject_dangerous_snippet(snippet: str, field: str = "filter") -> str:
    """Guard a raw WHERE snippet against statement stacking / comments / DML.

    Intentionally conservative: this is a defense-in-depth check on a value that's
    documented as "raw SQL", not a full parser. Callers should still scope the
    tool to trusted roles and run read-only OBO.
    """
    if snippet and _DANGEROUS_RE.search(snippet):
        raise SqlSafetyError(
            f"{field} contains disallowed SQL (statement terminators, comments, or "
            "data/DDL keywords are not permitted)"
        )
    return snippet


__all__ = [
    "SqlSafetyError",
    "quote_literal",
    "valid_date",
    "require_date",
    "valid_identifier",
    "require_identifier",
    "require_identifiers",
    "reject_dangerous_snippet",
]
