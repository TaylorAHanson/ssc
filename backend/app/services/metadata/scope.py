"""The catalogs this deployment manages: the boundary for every write, edit and adoption (ADR-0006 D3).

METADATA_RECONCILE_CATALOGS lists catalog names or glob patterns (``*_dev``; ``*`` is
every catalog). Left blank, the deployment reads and plans every catalog it can see
but writes to none: enforce needs an explicit list, so a lower-environment deployment
on a shared metastore never writes to catalogs it wasn't given.

METADATA_ENFORCE_SCHEMAS narrows writes further, to ``catalog.schema`` names or
patterns inside those catalogs. Everything else they hold is still read, planned and
editable, but reported only, so enforcement can start on one schema of a catalog
people are already using. ``*`` is every schema and the catalogs' own tags; blank is
nothing, so a list that's cleared, or a saved value that fails to load, can't widen
what enforce writes.
"""

from __future__ import annotations

import fnmatch
from typing import Iterable, List, Optional

from app.core.config import settings
from app.services.metadata.types import COLUMN_SEPARATOR

_GLOB_CHARS = frozenset("*?")
_SHOWN = 5
EVERYTHING = "*"


def patterns() -> List[str]:
    """METADATA_RECONCILE_CATALOGS, lowercased and deduplicated."""
    configured = getattr(settings, "METADATA_RECONCILE_CATALOGS", None) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    return list(dict.fromkeys(str(c).strip().lower() for c in configured if str(c).strip()))


def is_explicit() -> bool:
    return bool(patterns())


def is_pattern(value: str) -> bool:
    return any(ch in _GLOB_CHARS for ch in value)


def catalog_of(fqn_key: str) -> str:
    return (fqn_key or "").partition(COLUMN_SEPARATOR)[0].split(".", 1)[0].strip().lower()


def manages(catalog: Optional[str]) -> bool:
    """Whether this deployment accepts edits to and may write to ``catalog``. A blank list means every catalog."""
    wanted = patterns()
    if not wanted:
        return True
    name = (catalog or "").strip().lower()
    return bool(name) and any(fnmatch.fnmatchcase(name, p) for p in wanted)


def enforce_patterns() -> List[str]:
    """METADATA_ENFORCE_SCHEMAS, lowercased and deduplicated; ``*`` = everything, empty = nothing."""
    configured = getattr(settings, "METADATA_ENFORCE_SCHEMAS", None) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    return list(dict.fromkeys(str(s).strip().lower() for s in configured if str(s).strip()))


def enforces_everything(wanted: Optional[List[str]] = None) -> bool:
    return EVERYTHING in (enforce_patterns() if wanted is None else wanted)


def enforces(fqn_key: str, wanted: Optional[List[str]] = None) -> bool:
    """Whether enforce may write this object: it's in a listed schema, or the list is ``*``.

    A catalog's own tags are outside every named schema. ``wanted`` is
    ``enforce_patterns()`` read once by the caller, so a run decides every object the same way.
    """
    wanted = enforce_patterns() if wanted is None else wanted
    if enforces_everything(wanted):
        return True
    parts = (fqn_key or "").partition(COLUMN_SEPARATOR)[0].strip().lower().split(".")
    if len(parts) < 2 or not parts[1]:
        return False
    return any(fnmatch.fnmatchcase(f"{parts[0]}.{parts[1]}", p) for p in wanted)


def outside(catalogs: Iterable[Optional[str]]) -> List[str]:
    """The catalogs among ``catalogs`` this deployment doesn't manage, sorted."""
    return sorted({c.strip().lower() for c in catalogs if c and c.strip() and not manages(c)})


def outside_message(catalogs: List[str]) -> str:
    shown = ", ".join(catalogs[:_SHOWN])
    more = len(catalogs) - _SHOWN
    names = f"{shown} and {more} more" if more > 0 else shown
    return (
        f"This deployment manages only catalogs matching {', '.join(patterns())}, so it can't change {names}. "
        "Make the change in the deployment that manages them, or ask a Platform Admin to add them to "
        "Catalogs to reconcile."
    )


def enforce_blocked_reason() -> Optional[str]:
    """Why enforce is set but this deployment only plans, or None."""
    if str(getattr(settings, "METADATA_RECONCILE_MODE", "") or "").strip().lower() != "enforce":
        return None
    if not is_explicit():
        return (
            "Reconcile mode is enforce, but Catalogs to reconcile is blank, so nothing is applied. List the "
            "catalogs this deployment manages (or * for every catalog) to start enforcing."
        )
    if not enforce_patterns():
        return (
            "Reconcile mode is enforce, but Enforce only in these schemas is blank, so nothing is applied. List "
            "the schemas to write to, as catalog.schema, or * for every schema of the catalogs above."
        )
    return None


def schema_pattern_problem(value: str) -> Optional[str]:
    """Why ``value`` can't be an Enforce only in these schemas entry, or None."""
    text = (value or "").strip()
    if text == EVERYTHING:
        return None
    parts = text.split(".")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return f"'{text}' isn't catalog.schema. Use a name or pattern such as sales_dev.bronze or sales_dev.*_raw, or * for every schema."
    if any(ch in text for ch in "`'\"; "):
        return f"'{text}' can't contain quotes, semicolons or spaces."
    return None
