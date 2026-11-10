"""Where a ``run_sql`` answer came from: the tables a query read, and what the catalog knows of them.

People asked "what table is this number from?" and "can I trust it?". The
query's own text answers the first; the cached catalog (certification, data
quality, owner, source system) answers the second, so the chat can show both
next to the result and the agent can answer from them without another lookup.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.data_asset import DataAssetModel
from app.services.data_asset_owner import resolve_data_asset_owner
from app.services.discover_visibility import load_visibility

logger = logging.getLogger(__name__)

# Enough for any reasonable query; keeps the tool result bounded.
_MAX_SOURCES = 12
_MAX_ISSUES = 3


def source_tables(sql: str) -> List[str]:
    """Tables ``sql`` reads, lowercased (Unity Catalog names are), sorted, CTE names excluded.

    Empty when the statement can't be parsed (e.g. SHOW / DESCRIBE).
    """
    import sqlglot
    from sqlglot import exp

    try:
        statements = sqlglot.parse(sql, read="databricks")
    except Exception as e:
        logger.debug("source_tables: could not parse SQL: %s", e)
        return []
    found = set()
    for statement in statements:
        if statement is None:
            continue
        ctes = {c.alias_or_name.lower() for c in statement.find_all(exp.CTE)}
        for table in statement.find_all(exp.Table):
            parts = [p.lower() for p in (table.catalog, table.db, table.name) if p]
            if not parts or (len(parts) == 1 and parts[0] in ctes):
                continue
            found.add(".".join(parts))
    return sorted(found)[:_MAX_SOURCES]


def _quality(dq: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(dq, dict) or not dq:
        return None
    out = {k: v for k, v in dq.items() if k != "failed_rules"}
    failed = dq.get("failed_rules")
    if isinstance(failed, list) and failed:
        out["failed_rules"] = failed[:_MAX_ISSUES]
    return out


def describe_sources(db: Session, names: List[str]) -> List[Dict[str, Any]]:
    """What the catalog knows about each table: certification, quality, owner, source system.

    A table the catalog doesn't hold (or hides from Discover) is listed by
    name only, with ``in_catalog`` false, so nothing hidden leaks into the chat.
    """
    if not names:
        return []
    vis = load_visibility(db)
    wanted = {n.lower(): n for n in names}
    try:
        rows = db.query(DataAssetModel).filter(func.lower(DataAssetModel.id).in_(list(wanted))).all()
    except Exception as e:
        logger.warning("describe_sources: catalog lookup failed: %s", e)
        rows = []
    by_id = {r.id.lower(): r for r in vis.visible(rows)}
    source_tag = getattr(settings, "DATA_ASSET_SOURCE_SYSTEM_TAG", "") or ""
    out: List[Dict[str, Any]] = []
    for key, name in wanted.items():
        asset = by_id.get(key)
        if asset is None:
            out.append({"name": name, "in_catalog": False})
            continue
        violations = asset.certification_violations if isinstance(asset.certification_violations, list) else []
        out.append({
            "name": asset.id,
            "in_catalog": True,
            "type": asset.type,
            "certified": bool(asset.certified),
            "certification_issues": [str(v) for v in violations[:_MAX_ISSUES]],
            "data_quality": _quality(asset.data_quality),
            "owner": asset.effective_owner,
            "source_system": resolve_data_asset_owner(None, asset.tags, owner_tag=source_tag) if source_tag else None,
            "catalog_synced_at": asset.last_synced_at.isoformat() if asset.last_synced_at else None,
            "discover_path": f"/discovery?{urlencode({'asset': asset.id})}",
        })
    return out
