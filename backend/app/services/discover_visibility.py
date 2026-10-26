"""
Hide assets from Discover by tag key (``DISCOVER_HIDDEN_TAG_KEYS``).

An asset is hidden when a tag whose key is listed (any value, case-insensitive)
sits on its catalog, its schema, the asset itself or any of its columns.

- Unity Catalog assets (tables, views, metric views) are checked against the
  tags the data-asset sync caches: ``tags`` (the asset's own) and
  ``scope_tag_keys`` (its catalog's, schema's and columns'). A catalog or schema
  carrying a hidden key hides every name under it, cached or not, so live
  lookups (lineage, table details, the agent's ``get_table_list``) honour it too.
- Dashboards, Genie spaces and apps carry workspace entity tags, read live per
  entity and cached for ``_ENTITY_TTL_SECONDS``.
- Jobs carry job tags in their settings.

Everything is evaluated when a request is served, so changing the setting
applies at once; a tag added or removed in Unity Catalog shows after the next
sync. With the setting empty every helper returns without touching the DB or
Databricks.
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Set

from app.core.config import settings

logger = logging.getLogger(__name__)

SCOPE_LEVELS = ("catalog", "schema", "column")

# Discover asset type -> workspace entity-tag API entity type.
WORKSPACE_ENTITY_TYPES = {"dashboard": "dashboards", "genie_space": "geniespaces", "app": "apps"}

_ENTITY_TTL_SECONDS = 600
_ENTITY_CACHE_MAX = 20000
_ENTITY_CONCURRENCY = 8


def hidden_tag_keys() -> FrozenSet[str]:
    """The configured keys, lowercased. Empty = the feature is off."""
    raw = getattr(settings, "DISCOVER_HIDDEN_TAG_KEYS", None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    return frozenset(k.strip().lower() for k in raw if isinstance(k, str) and k.strip())


def tag_key(tag: Any) -> Optional[str]:
    """The lowercased key of one tag: ``"key=value"`` / ``"key"`` strings, dicts or SDK objects."""
    if isinstance(tag, str):
        key = tag.split("=", 1)[0]
    elif isinstance(tag, dict):
        key = tag.get("key") or tag.get("tag_key") or tag.get("tag_name") or tag.get("name") or ""
    else:
        key = getattr(tag, "tag_key", None) or getattr(tag, "key", None) or ""
    key = str(key).strip().lower()
    return key or None


def tag_keys(tags: Any) -> Set[str]:
    """Every key in a tag collection: a list of tags, a ``{key: value}`` dict, or its JSON text."""
    if not tags:
        return set()
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = [tags]
    if isinstance(tags, dict):
        return {str(k).strip().lower() for k in tags if str(k).strip()}
    if not isinstance(tags, (list, tuple, set)):
        return set()
    return {k for k in (tag_key(t) for t in tags) if k}


def has_hidden_tag(tags: Any, keys: Optional[FrozenSet[str]] = None) -> bool:
    keys = hidden_tag_keys() if keys is None else keys
    return bool(keys) and not keys.isdisjoint(tag_keys(tags))


def _scope(scope_tag_keys: Any) -> Dict[str, Set[str]]:
    if isinstance(scope_tag_keys, str):
        try:
            scope_tag_keys = json.loads(scope_tag_keys)
        except ValueError:
            scope_tag_keys = None
    if not isinstance(scope_tag_keys, dict):
        return {}
    return {level: tag_keys(scope_tag_keys.get(level)) for level in SCOPE_LEVELS}


def _norm(name: Optional[str]) -> str:
    return (name or "").replace("`", "").strip().lower()


@dataclass
class DiscoverVisibility:
    """Which cached assets, catalogs and schemas the configured keys hide."""

    keys: FrozenSet[str] = frozenset()
    # Lowercased names hidden by their own or a column's tag.
    hidden_ids: Set[str] = field(default_factory=set)
    hidden_catalogs: Set[str] = field(default_factory=set)
    hidden_schemas: Set[str] = field(default_factory=set)
    # Exact ``data_assets.id`` values hidden at any level, for SQL ``NOT IN``.
    hidden_asset_ids: Set[str] = field(default_factory=set)

    @property
    def active(self) -> bool:
        return bool(self.keys)

    def is_hidden_id(self, asset_id: Optional[str]) -> bool:
        """Whether a cached asset id or a ``catalog.schema.table`` name is hidden."""
        if not self.active or not asset_id:
            return False
        name = _norm(asset_id)
        if name in self.hidden_ids:
            return True
        parts = name.split(".")
        if len(parts) != 3:
            return False
        return parts[0] in self.hidden_catalogs or f"{parts[0]}.{parts[1]}" in self.hidden_schemas

    def is_hidden_asset(self, asset: Any) -> bool:
        if not self.active or asset is None:
            return False
        if self.is_hidden_id(getattr(asset, "id", None)):
            return True
        scope = _scope(getattr(asset, "scope_tag_keys", None))
        own = tag_keys(getattr(asset, "tags", None))
        return not self.keys.isdisjoint(own.union(*scope.values()) if scope else own)

    def visible(self, assets: Iterable[Any]) -> List[Any]:
        return [a for a in assets if not self.is_hidden_asset(a)]

    def visible_upstream(self, entries: Any) -> Any:
        """Upstream-table entries (``{"fqn": ...}`` dicts or FQN strings) minus hidden ones."""
        if not self.active or not isinstance(entries, list):
            return entries
        out = []
        for e in entries:
            fqn = e.get("fqn") or e.get("name") if isinstance(e, dict) else e
            if isinstance(fqn, str) and self.is_hidden_id(fqn):
                continue
            out.append(e)
        return out


def load_visibility(db) -> DiscoverVisibility:
    """Hidden ids, catalogs and schemas from the cache. No query when the feature is off."""
    keys = hidden_tag_keys()
    vis = DiscoverVisibility(keys=keys)
    if not keys:
        return vis
    from app.db.data_asset import DataAssetModel

    rows = db.query(
        DataAssetModel.id, DataAssetModel.catalog, DataAssetModel.schema,
        DataAssetModel.tags, DataAssetModel.scope_tag_keys,
    ).all()
    for asset_id, catalog, schema, tags, scope_raw in rows:
        scope = _scope(scope_raw)
        cat, sch = _norm(catalog), _norm(schema)
        if cat and not keys.isdisjoint(scope.get("catalog", ())):
            vis.hidden_catalogs.add(cat)
        if cat and sch and not keys.isdisjoint(scope.get("schema", ())):
            vis.hidden_schemas.add(f"{cat}.{sch}")
        if not keys.isdisjoint(tag_keys(tags)) or not keys.isdisjoint(scope.get("column", ())):
            vis.hidden_ids.add(_norm(asset_id))
    # Second pass: catalog/schema hits are only complete once every row is seen.
    vis.hidden_asset_ids = {
        asset_id for asset_id, catalog, schema, _t, _s in rows
        if _norm(asset_id) in vis.hidden_ids
        or _norm(catalog) in vis.hidden_catalogs
        or f"{_norm(catalog)}.{_norm(schema)}" in vis.hidden_schemas
    }
    return vis


# ---------------------------------------------------------------------------
# Workspace entities (dashboards, Genie spaces, apps)
# ---------------------------------------------------------------------------
_entity_cache: Dict[tuple, tuple] = {}
_entity_lock = threading.Lock()
_warned_types: Set[str] = set()


def _default_client():
    """The app's workspace client, as the Discover workspace listings use."""
    from app.providers.databricks import DatabricksProvider

    return DatabricksProvider(
        host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
        token=settings.DATABRICKS_TOKEN,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET,
    ).client


def _fetch_entity_keys(client, entity_type: str, entity_id: str) -> FrozenSet[str]:
    try:
        assignments = client.workspace_entity_tag_assignments.list_tag_assignments(
            entity_type=entity_type, entity_id=entity_id
        )
        return frozenset(tag_keys(list(assignments)))
    except Exception as e:  # noqa: BLE001 - fail open: an unreadable tag never blanks Discover
        if entity_type not in _warned_types:
            _warned_types.add(entity_type)
            logger.warning(
                "Could not read workspace tags for %s (e.g. %s); they won't be hidden by "
                "DISCOVER_HIDDEN_TAG_KEYS until this works: %s", entity_type, entity_id, e,
            )
        else:
            logger.debug("Workspace tags for %s %s unavailable: %s", entity_type, entity_id, e)
        return frozenset()


def workspace_entity_tag_keys(
    entity_type: str,
    entity_ids: Iterable[str],
    client: Any = None,
    fetch: Optional[Callable[[Any, str, str], FrozenSet[str]]] = None,
) -> Dict[str, FrozenSet[str]]:
    """Tag keys per entity id, read live (in parallel) and cached for a few minutes."""
    ids = list(dict.fromkeys(str(i) for i in entity_ids if i))
    now = time.monotonic()
    out: Dict[str, FrozenSet[str]] = {}
    missing = []
    with _entity_lock:
        for i in ids:
            hit = _entity_cache.get((entity_type, i))
            if hit and hit[0] > now:
                out[i] = hit[1]
            else:
                missing.append(i)
    if not missing:
        return out
    fetch = fetch or _fetch_entity_keys
    try:
        client = client or _default_client()
    except Exception as e:  # noqa: BLE001 - fail open, like an unreadable tag
        logger.warning("Could not build a workspace client to read %s tags: %s", entity_type, e)
        return {**out, **{i: frozenset() for i in missing}}
    with ThreadPoolExecutor(max_workers=min(_ENTITY_CONCURRENCY, len(missing))) as pool:
        fetched = dict(zip(missing, pool.map(lambda i: fetch(client, entity_type, i), missing)))
    expires = time.monotonic() + _ENTITY_TTL_SECONDS
    with _entity_lock:
        if len(_entity_cache) + len(fetched) > _ENTITY_CACHE_MAX:
            _entity_cache.clear()
        for i, keys in fetched.items():
            _entity_cache[(entity_type, i)] = (expires, keys)
    out.update(fetched)
    return out


def hidden_workspace_entities(discover_type: str, entity_ids: Iterable[str], client: Any = None) -> Set[str]:
    """Ids of dashboards / Genie spaces / apps carrying a hidden key. Empty when the feature is off."""
    keys = hidden_tag_keys()
    entity_type = WORKSPACE_ENTITY_TYPES.get(discover_type)
    if not keys or not entity_type:
        return set()
    by_id = workspace_entity_tag_keys(entity_type, entity_ids, client=client)
    return {i for i, k in by_id.items() if not keys.isdisjoint(k)}


def hidden_linked_dashboards(assets: Iterable[Any], client: Any = None) -> Set[str]:
    """Hidden ids among the dashboards linked (``downstream_dashboards``) to these assets.

    One batched lookup per request, so a page of metric views costs one round
    of (cached) tag reads rather than one per view.
    """
    if not hidden_tag_keys():
        return set()
    ids = {
        d.get("id")
        for a in assets
        for d in (getattr(a, "downstream_dashboards", None) or [])
        if isinstance(d, dict) and d.get("id")
    }
    return hidden_workspace_entities("dashboard", ids, client=client) if ids else set()


def visible_dashboards(entries: Any, hidden: Set[str]) -> Any:
    """Dashboard entries (``{"id": ...}``) minus the ``hidden`` ids."""
    if not hidden or not isinstance(entries, list):
        return entries
    return [e for e in entries if not (isinstance(e, dict) and e.get("id") in hidden)]


def clear_entity_cache() -> None:
    with _entity_lock:
        _entity_cache.clear()
    _warned_types.clear()
