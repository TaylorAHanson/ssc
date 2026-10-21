"""Catalog-to-environment mapping and counterparts (ADR-0006 D5). Workstream D.

A catalog belongs to the first ``METADATA_ENVIRONMENTS`` row that lists it in
``catalogs`` or whose ``catalog_pattern`` (with ``{base}``) matches it. The pattern
also defines counterparts: ``sales_dev`` under ``{base}_dev`` is ``sales_prd`` under
``{base}_prd``; schema, table and column names don't change.

Settings are read on every call, since admins edit them live.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Dict, List, Literal, Optional, Tuple

from app.core.config import settings
from app.services.metadata.types import EnvironmentInfo, ObjectKey

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{base\}", re.IGNORECASE)
_APPROVALS = ("sensitive", "all")


def _as_list(value: object) -> List[str]:
    if not value:
        return []
    parts = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    return [str(p).strip() for p in parts if p is not None and str(p).strip()]


def environments() -> List[EnvironmentInfo]:
    """``METADATA_ENVIRONMENTS`` rows in order; rows without a key and repeated keys are skipped."""
    out: List[EnvironmentInfo] = []
    seen = set()
    for raw in settings.METADATA_ENVIRONMENTS or []:
        if isinstance(raw, EnvironmentInfo):
            raw = raw.model_dump()
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        approval = str(raw.get("approval") or "").strip().lower()
        out.append(EnvironmentInfo(
            key=key,
            catalog_pattern=str(raw.get("catalog_pattern") or "").strip() or None,
            catalogs=list(dict.fromkeys(c.lower() for c in _as_list(raw.get("catalogs")))),
            approval=approval if approval in _APPROVALS else "sensitive",
        ))
    return out


def environment_keys() -> List[str]:
    return [e.key for e in environments()]


@lru_cache(maxsize=256)
def _pattern_regex(pattern: str) -> Optional[re.Pattern]:
    """``{base}_dev`` -> a case-insensitive regex capturing a non-empty ``base``; None without ``{base}``."""
    parts = _PLACEHOLDER.split(pattern)
    if len(parts) < 2:
        logger.warning("Metadata environment pattern %r has no {base} placeholder; it matches nothing.", pattern)
        return None
    regex = re.escape(parts[0])
    for i, part in enumerate(parts[1:]):
        regex += ("(?P<base>.+)" if i == 0 else "(?P=base)") + re.escape(part)
    return re.compile(regex, re.IGNORECASE)


def _base(env: EnvironmentInfo, catalog: str) -> Optional[str]:
    if not env.catalog_pattern:
        return None
    rx = _pattern_regex(env.catalog_pattern)
    m = rx.fullmatch(catalog) if rx is not None else None
    return m.group("base") if m else None


def _match(catalog: Optional[str]) -> Optional[Tuple[EnvironmentInfo, Optional[str]]]:
    """(the catalog's environment, its pattern base or None when only an explicit list matched)."""
    name = (catalog or "").strip()
    if not name:
        return None
    for env in environments():
        base = _base(env, name)
        if base is not None or name.lower() in env.catalogs:
            return env, base
    return None


def _find(environment: Optional[str]) -> Optional[EnvironmentInfo]:
    wanted = (environment or "").strip().lower()
    if not wanted:
        return None
    return next((e for e in environments() if e.key.lower() == wanted), None)


def environment_of(catalog: str) -> Optional[str]:
    found = _match(catalog)
    return found[0].key if found else None


def base_of(catalog: str) -> Optional[str]:
    """``sales`` for ``sales_dev`` under ``{base}_dev``; None when no environment pattern names the catalog."""
    found = _match(catalog)
    return found[1] if found else None


def counterpart(key: ObjectKey, target_env: str) -> Optional[ObjectKey]:
    """The same object in ``target_env``, or None when the catalog has no pattern-based counterpart.

    None as well for the object's own environment, an unknown target, a target
    without a pattern, and a resolved catalog that some earlier row claims for a
    different environment.
    """
    target = _find(target_env)
    if target is None or not target.catalog_pattern or _pattern_regex(target.catalog_pattern) is None:
        return None
    found = _match(key.catalog)
    if found is None:
        return None
    source, base = found
    if base is None or source.key.lower() == target.key.lower():
        return None
    catalog = _PLACEHOLDER.sub(lambda _m: base, target.catalog_pattern)
    if (environment_of(catalog) or "").lower() != target.key.lower():
        logger.debug("Counterpart %s of %s in %s belongs to another environment; skipped.",
                     catalog, key.catalog, target.key)
        return None
    name = ".".join(p for p in (catalog, key.schema_name, key.object_name) if p)
    try:
        return ObjectKey.parse(name, key.column_name)
    except ValueError as e:
        logger.warning("Counterpart of %s in %s isn't a valid name (%s); skipped.", key.display, target.key, e)
        return None


def counterparts(key: ObjectKey) -> Dict[str, ObjectKey]:
    """Every other environment's counterpart, keyed by environment."""
    found = _match(key.catalog)
    if found is None or found[1] is None:
        return {}
    out: Dict[str, ObjectKey] = {}
    for env in environments():
        if env.key.lower() == found[0].key.lower():
            continue
        other = counterpart(key, env.key)
        if other is not None:
            out[env.key] = other
    return out


def approval_rule(environment: Optional[str]) -> Literal["sensitive", "all"]:
    env = _find(environment)
    return env.approval if env is not None else "sensitive"
