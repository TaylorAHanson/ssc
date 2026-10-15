"""Local cache of the account's workspace list.

``system.access.workspaces_latest`` can only be read through a SQL warehouse, so
every live lookup pays a warehouse round trip, plus a cold start whenever the
warehouse is stopped. The data-asset sync refreshes this cache as the
governance service principal (:func:`refresh_workspace_cache`); the
``list_workspaces`` tool and the target-workspace listing read it instead.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import dateutil.parser
from sqlalchemy import func

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.db.session import get_lakebase_session
from app.db.workspace_cache import CachedWorkspaceModel

logger = logging.getLogger(__name__)

WORKSPACES_TABLE = "system.access.workspaces_latest"

# Optional columns, first match wins. The documented schema has neither, so
# they're picked off the result rather than named in the query.
_REGION_COLUMNS = ("region", "cloud_region", "aws_region", "location")
_CLOUD_COLUMNS = ("cloud", "cloud_provider", "cloud_type")

_unreadable_warned = False


def enabled() -> bool:
    return bool(getattr(settings, "WORKSPACE_CACHE_ENABLED", True))


def max_age() -> timedelta:
    minutes = int(getattr(settings, "WORKSPACE_CACHE_MAX_AGE_MINUTES", 180) or 180)
    return timedelta(minutes=max(1, minutes))


def normalize_host(url: Optional[str]) -> str:
    """Comparable form of a workspace URL: lowercase, no scheme, no trailing slash."""
    host = (url or "").strip().lower()
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    return host.rstrip("/")


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _naive_utc(value: Any) -> Optional[datetime]:
    """A system-table timestamp as naive UTC, the way the app stores datetimes."""
    if not value:
        return None
    try:
        dt = value if isinstance(value, datetime) else dateutil.parser.isoparse(str(value))
    except (ValueError, TypeError, OverflowError):
        return None
    return _utc(dt).replace(tzinfo=None)


def _text(value: Any) -> Optional[str]:
    return None if value in (None, "") else str(value)


def _first_present(columns: Iterable[str], candidates: Tuple[str, ...]) -> Optional[str]:
    present = {c.lower(): c for c in columns}
    for name in candidates:
        if name in present:
            return present[name]
    return None


def _to_dict(row: CachedWorkspaceModel) -> Dict[str, Any]:
    out = {
        "account_id": row.account_id,
        "workspace_id": row.workspace_id,
        "workspace_name": row.workspace_name,
        "workspace_url": row.workspace_url,
        "create_time": _utc(row.create_time).isoformat() if row.create_time else None,
        "status": row.status,
    }
    if row.region:
        out["region"] = row.region
    if row.cloud:
        out["cloud"] = row.cloud
    return out


def read_cached_workspaces(
    status: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[datetime]]:
    """Cached workspaces sorted by name (only ``status`` ones if given), and when
    they were synced (UTC). ``([], None)`` when the cache is empty.

    Blocking DB read: call it via ``asyncio.to_thread`` from async code.
    """
    db = get_lakebase_session()
    try:
        synced_at = db.query(func.max(CachedWorkspaceModel.synced_at)).scalar()
        if synced_at is None:
            return [], None
        query = db.query(CachedWorkspaceModel)
        if status:
            query = query.filter(CachedWorkspaceModel.status == status)
        rows = query.order_by(CachedWorkspaceModel.workspace_name.asc()).all()
        return [_to_dict(r) for r in rows], _utc(synced_at)
    finally:
        db.close()


def is_fresh(cached_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if cached_at is None:
        return False
    return (now or datetime.now(timezone.utc)) - cached_at <= max_age()


def is_empty() -> bool:
    db = get_lakebase_session()
    try:
        return db.query(CachedWorkspaceModel.workspace_id).first() is None
    finally:
        db.close()


def cached_by_host() -> Dict[str, Dict[str, Any]]:
    """Cached workspaces keyed by :func:`normalize_host` of their URL; ``{}`` when off or empty."""
    if not enabled():
        return {}
    rows, _ = read_cached_workspaces()
    return {normalize_host(r["workspace_url"]): r for r in rows if r.get("workspace_url")}


def _replace_all(rows: List[CachedWorkspaceModel]) -> None:
    db = get_lakebase_session()
    try:
        db.query(CachedWorkspaceModel).delete(synchronize_session=False)
        db.add_all(rows)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def refresh_workspace_cache(provider) -> Optional[int]:
    """Re-read the account's workspaces as ``provider``'s identity and replace the cache.

    Returns how many workspaces were written, or None when the cache was left as
    it was: the identity can't read the system table (warned once, until a
    later run succeeds), a transient failure, or a result with no usable rows.
    An empty or failed read never wipes the cache; old rows just age past the
    max age and ``list_workspaces`` goes back to its live query. Anything
    unexpected propagates to the caller.
    """
    global _unreadable_warned
    try:
        result = await provider.execute_sql(
            f"SELECT * FROM {WORKSPACES_TABLE}",
            warehouse=settings.DATABRICKS_WAREHOUSE_ID,
        )
    except (PermanentError, PermissionError) as e:
        if not _unreadable_warned:
            logger.warning(
                "Workspace cache not refreshed: the data asset sync identity can't read %s (%s). "
                "Grant it USE CATALOG on system, USE SCHEMA on system.access and SELECT on the "
                "table. Until then list_workspaces queries the table live as each user.",
                WORKSPACES_TABLE, e,
            )
            _unreadable_warned = True
        else:
            logger.debug("Workspace cache still can't read %s: %s", WORKSPACES_TABLE, e)
        return None
    except RetryableError as e:
        logger.warning("Workspace cache not refreshed this run (transient failure): %s", e)
        return None

    rows = result.get("rows") or []
    columns = list(result.get("schema") or (rows[0].keys() if rows else []))
    if rows and "workspace_id" not in columns:
        logger.warning(
            "Workspace cache not refreshed: %s returned no workspace_id column (columns: %s).",
            WORKSPACES_TABLE, columns,
        )
        return None
    region_col = _first_present(columns, _REGION_COLUMNS)
    cloud_col = _first_present(columns, _CLOUD_COLUMNS)

    synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
    records: Dict[str, CachedWorkspaceModel] = {}
    for row in rows:
        workspace_id = _text(row.get("workspace_id"))
        if not workspace_id:
            continue
        records[workspace_id] = CachedWorkspaceModel(
            workspace_id=workspace_id,
            account_id=_text(row.get("account_id")),
            workspace_name=_text(row.get("workspace_name")),
            workspace_url=_text(row.get("workspace_url")),
            status=_text(row.get("status")),
            create_time=_naive_utc(row.get("create_time")),
            region=_text(row.get(region_col)) if region_col else None,
            cloud=_text(row.get(cloud_col)) if cloud_col else None,
            synced_at=synced_at,
        )
    if not records:
        logger.warning(
            "Workspace cache not refreshed: %s returned no workspaces to the sync identity.",
            WORKSPACES_TABLE,
        )
        return None

    await asyncio.to_thread(_replace_all, list(records.values()))
    if _unreadable_warned:
        logger.info("Workspace cache: %s is readable again.", WORKSPACES_TABLE)
        _unreadable_warned = False
    logger.info("Cached %d workspace(s) from %s.", len(records), WORKSPACES_TABLE)
    return len(records)
