"""GitOps tag-change SQL builder (relocated from the legacy tag_change SM)."""
from typing import Any, Dict, List


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
