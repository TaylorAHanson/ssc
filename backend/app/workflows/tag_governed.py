"""Unity Catalog governed tags (account-level tag policies).

A governed tag key may restrict its values to an allowed list. Unity Catalog
only enforces that when the statement runs (``UC_TAG_POLICY_VALUE_NOT_ALLOWED``),
which in Local Execution Mode means part of a change can apply before the rest
fails. Reading the policies up front lets the preview block the change instead,
and lets the editor offer the allowed values while you type.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from databricks.sdk.errors import NotFound

logger = logging.getLogger(__name__)


@dataclass
class GovernedTag:
    key: str
    allowed_values: List[str] = field(default_factory=list)  # empty = any value
    description: str = ""

    def allows(self, value: str) -> bool:
        # A key-only assignment (no value) isn't checked against the list.
        return not self.allowed_values or not value or value in self.allowed_values


@dataclass
class GovernedTags:
    tags: Dict[str, GovernedTag] = field(default_factory=dict)
    # Keys whose policy couldn't be read (permissions, network); not validated.
    unchecked: List[str] = field(default_factory=list)

    def get(self, key: str) -> Optional[GovernedTag]:
        return self.tags.get(key)


def fetch_governed_tags(provider, keys: Iterable[str]) -> GovernedTags:
    """Look up the tag policy for each key; keys without one aren't governed."""
    result = GovernedTags()
    for key in sorted({k for k in keys if k}):
        try:
            policy = provider.client.tag_policies.get_tag_policy(key)
        except NotFound:
            continue
        except Exception as e:  # noqa: BLE001 - advisory; Unity Catalog still enforces on write
            logger.warning(f"Could not read the tag policy for '{key}': {e}")
            result.unchecked.append(key)
            continue
        result.tags[key] = GovernedTag(
            key=key,
            allowed_values=[v.name for v in (policy.values or []) if getattr(v, "name", None)],
            description=policy.description or "",
        )
    return result


def check_governed_values(changes: List[Dict], governed: GovernedTags) -> List[str]:
    """One problem per value that the key's tag policy doesn't allow."""
    problems: List[str] = []
    for change in changes:
        label = f"{change['table']}.{change['column']}" if change.get("column") else change["table"]
        for key, value in (change.get("set") or {}).items():
            tag = governed.get(key)
            if tag and not tag.allows(str(value)):
                allowed = ", ".join(tag.allowed_values)
                problems.append(
                    f"{label}: '{value}' isn't an allowed value for the governed tag '{key}'. "
                    f"Allowed values: {allowed}."
                )
    return problems


# list_tag_policies is account-wide and can return thousands of keys, so the
# list is kept briefly per process and filtered here for the editor's typeahead.
_KEYS_TTL_SECONDS = 300
_MAX_KEYS = 50000
_keys_cache: Dict[str, object] = {"at": 0.0, "keys": None}


def list_governed_keys(provider) -> List[GovernedTag]:
    """Every governed tag key (with its allowed values), cached for a few minutes."""
    now = time.monotonic()
    cached = _keys_cache.get("keys")
    if cached is not None and now - float(_keys_cache["at"]) < _KEYS_TTL_SECONDS:
        return cached  # type: ignore[return-value]
    tags: List[GovernedTag] = []
    for policy in provider.client.tag_policies.list_tag_policies(page_size=1000):
        if not policy.tag_key:
            continue
        tags.append(GovernedTag(
            key=policy.tag_key,
            allowed_values=[v.name for v in (policy.values or []) if getattr(v, "name", None)],
            description=policy.description or "",
        ))
        if len(tags) >= _MAX_KEYS:
            break
    tags.sort(key=lambda t: t.key.lower())
    _keys_cache.update(at=now, keys=tags)
    return tags


def search_governed_keys(provider, query: str, limit: int = 30) -> List[GovernedTag]:
    """Governed keys containing ``query`` (case-insensitive), prefix matches first."""
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = [t for t in list_governed_keys(provider) if q in t.key.lower()]
    hits.sort(key=lambda t: (not t.key.lower().startswith(q), len(t.key), t.key.lower()))
    return hits[:limit]
