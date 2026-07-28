"""GitOps tag-change SQL builder (relocated from the legacy tag_change SM).

This module is one half of a cross-repo contract: the governance repo parses
what we emit here against a strict whitelist and rejects the PR on anything it
does not recognize. See ``CONTRACT.md`` in that repo before changing the
statement forms, the filename, or the header — both sides move together.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional


def _escape_sql_literal(value: Any) -> str:
    return str(value).replace("'", "''")


def build_tag_sql(changes: List[Dict[str, Any]]) -> str:
    """Build ``ALTER TABLE ... SET/UNSET TAGS`` SQL from a list of changes.

    Each change is ``{"table": "<fqn>", "set": {k: v, ...}, "unset": [k, ...]}``.
    Returns the full SQL script (one statement per line), or "" when empty.
    """
    lines: List[str] = []
    for change in changes:
        table = change.get("table")
        if not table:
            continue
        set_tags = change.get("set") or {}
        unset_tags = change.get("unset") or []
        if set_tags:
            pairs = ", ".join(
                f"'{_escape_sql_literal(k)}' = '{_escape_sql_literal(v)}'"
                for k, v in set_tags.items()
            )
            lines.append(f"ALTER TABLE {table} SET TAGS ({pairs});")
        if unset_tags:
            keys = ", ".join(f"'{_escape_sql_literal(k)}'" for k in unset_tags)
            lines.append(f"ALTER TABLE {table} UNSET TAGS ({keys});")
    return "\n".join(lines)


def migration_filename(request_id: str, generated_at: datetime) -> str:
    """Filename for a migration: ``{YYYYMMDDHHMMSS}-{request_id}.sql``.

    Migrations apply in filename order, so the timestamp must be the request's
    submission time (stable across retries), not the moment the PR happens to be
    opened — otherwise re-running the step would add a second migration file
    instead of updating the first.
    """
    return f"{generated_at.strftime('%Y%m%d%H%M%S')}-{request_id}.sql"


def _requested_by_line(name: Optional[str], email: Optional[str]) -> str:
    name = (name or "").strip()
    email = (email or "").strip()
    if name and email and name != email:
        return f"{name} <{email}>"
    return name or email or "unknown"


def build_migration_file(
    request_id: str,
    dataset_name: str,
    requested_by: Optional[str],
    requested_by_email: Optional[str],
    generated_at: datetime,
    sql: str,
) -> str:
    """Render the full migration file: provenance header, blank line, statements.

    The header is `--` line comments only; the governance repo's parser rejects
    `/* */` blocks because they can hide content from a reviewer skimming the diff.
    """
    header = [
        f"-- Tag change request: {request_id}",
        f"-- Dataset: {dataset_name}",
        f"-- Requested by: {_requested_by_line(requested_by, requested_by_email)}",
        f"-- Generated: {generated_at.isoformat()}",
    ]
    return "\n".join(header) + "\n\n" + sql.rstrip("\n") + "\n"
