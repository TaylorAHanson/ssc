"""
Discover domains and subdomains, as Databricks defines them.

A domain is an account-level object (Databricks Discover → Domains) bound to a
key-only governed tag, e.g. ``supplychain``. A subdomain is a domain with a
parent; its tag key is ``{parent tag key}/{name}``, e.g. ``supplychain/procurement``.

- The data-asset sync caches the published domains in ``discover_domains``
  (``refresh_domain_cache``). Listing them needs ``MANAGE DISCOVER`` for the
  sync identity; if the call fails the last cached list is kept.
- An asset belongs to a domain when it carries the domain's tag key, directly
  or inherited from its catalog or schema (column tags don't count). Tagging an
  asset with a subdomain's tag also puts it in the parent domain, as Databricks
  Discover shows it. Membership is resolved per request from the tags the sync
  already caches, so nothing is inferred from names.
- Dashboards, Genie spaces and apps carry workspace entity tags, read live (and
  cached briefly) through ``discover_visibility.workspace_entity_tag_keys``.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.discover_visibility import (
    WORKSPACE_ENTITY_TYPES,
    scope_keys,
    tag_keys,
    workspace_entity_tag_keys,
)

logger = logging.getLogger(__name__)

DOMAINS_API = "/api/discover/v1/domains"
_PAGE_SIZE = 100

# Principal id -> display name, kept across syncs; owners rarely change.
_principal_names: Dict[int, Optional[str]] = {}


@dataclass(frozen=True)
class Domain:
    id: str
    tag_key: str
    parent_id: Optional[str]
    # The tag key for a domain; the part after "{parent}/" for a subdomain.
    name: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[dict] = None
    technical_owners: Tuple[str, ...] = ()
    business_owners: Tuple[str, ...] = ()

    @property
    def is_subdomain(self) -> bool:
        return self.parent_id is not None


@dataclass(frozen=True)
class Membership:
    """Top-level domain tag keys (subdomain members included) and subdomain tag keys."""

    domains: Tuple[str, ...] = ()
    subdomains: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.domains)


EMPTY = Membership()

# Discover's catch-all card for assets in no domain, so untagged data stays findable.
# A reserved key rather than "other", which a real domain could use as its tag.
OTHER_DOMAIN = "__other__"
OTHER_LABEL = "Other"


def is_other(ref: Optional[str]) -> bool:
    return (ref or "").strip().lower() == OTHER_DOMAIN


class DomainIndex:
    """Published domains, looked up by tag key (case-insensitive) or id."""

    def __init__(self, domains: Iterable[Domain] = ()):
        domains = list(domains)
        by_id = {d.id: d for d in domains}

        def _placed(d: Domain) -> bool:
            # A subdomain whose parent isn't a published top-level domain has nowhere to show.
            parent = by_id.get(d.parent_id) if d.parent_id else None
            return d.parent_id is None or (parent is not None and parent.parent_id is None)

        kept = [d for d in domains if _placed(d)]
        self.by_id: Dict[str, Domain] = {d.id: d for d in kept}
        self._by_key: Dict[str, Domain] = {d.tag_key.strip().lower(): d for d in kept}

    def __bool__(self) -> bool:
        return bool(self.by_id)

    @property
    def top_level(self) -> List[Domain]:
        return [d for d in self.by_id.values() if not d.is_subdomain]

    def children(self, domain_id: str) -> List[Domain]:
        return [d for d in self.by_id.values() if d.parent_id == domain_id]

    def get(self, ref: Optional[str]) -> Optional[Domain]:
        if not ref:
            return None
        return self._by_key.get(ref.strip().lower()) or self.by_id.get(ref)

    def parent(self, d: Domain) -> Optional[Domain]:
        return self.by_id.get(d.parent_id) if d.parent_id else None

    def resolve(self, keys: Iterable[str]) -> Membership:
        """The domains a set of tag keys (any case) puts something in."""
        if not self._by_key:
            return EMPTY
        tops, subs = set(), set()
        for k in keys:
            d = self._by_key.get(str(k).strip().lower())
            if d is None:
                continue
            if d.is_subdomain:
                subs.add(d.tag_key)
                tops.add(self.by_id[d.parent_id].tag_key)
            else:
                tops.add(d.tag_key)
        if not tops:
            return EMPTY
        return Membership(tuple(sorted(tops, key=str.lower)), tuple(sorted(subs, key=str.lower)))

    def for_tags(self, tags: Any, scope_tag_keys: Any) -> Membership:
        """Domains from an asset's own tags plus its catalog's and schema's tag keys."""
        if not self._by_key:
            return EMPTY
        scope = scope_keys(scope_tag_keys)
        keys = tag_keys(tags) | scope.get("catalog", set()) | scope.get("schema", set())
        return self.resolve(keys)

    def for_asset(self, asset: Any) -> Membership:
        """A cached data asset's domains."""
        return self.for_tags(getattr(asset, "tags", None), getattr(asset, "scope_tag_keys", None))

    def search(self, text: str) -> List[Domain]:
        """Domains named by free text: an exact tag key or name, else any whose name contains it
        (ignoring case, spaces and punctuation, so "supply chain" finds ``supplychain``)."""
        exact = self.get(text)
        if exact:
            return [exact]
        needle = _squash(text)
        if not needle:
            return []
        return [d for d in self.by_id.values() if needle in _squash(d.name) or needle in _squash(d.tag_key)]

    def matches(self, m: Membership, domain: Optional[str] = None, subdomain: Optional[str] = None) -> bool:
        """Whether a membership falls in ``domain`` and/or ``subdomain``.

        Either is a tag key or a domain id; ``subdomain`` may also be the
        subdomain's name within ``domain``. ``OTHER_DOMAIN`` matches assets in
        no domain (it has no subdomains).
        """
        if is_other(domain):
            return not m and not subdomain
        top = None
        if domain:
            top = self.get(domain)
            if top is None or top.is_subdomain or top.tag_key not in m.domains:
                return False
        if subdomain:
            sub = self.get(subdomain)
            if (sub is None or not sub.is_subdomain) and top is not None:
                sub = next((c for c in self.children(top.id) if c.name.lower() == subdomain.strip().lower()), None)
            if sub is None or not sub.is_subdomain or sub.tag_key not in m.subdomains:
                return False
        return True


def _squash(text: Optional[str]) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _subdomain_name(tag_key: str, parent_key: Optional[str]) -> str:
    if parent_key and tag_key.lower().startswith(parent_key.lower() + "/"):
        return tag_key[len(parent_key) + 1:]
    return tag_key


def _owner_names(owners: Any) -> Tuple[str, ...]:
    return tuple(o["name"] for o in (owners or []) if isinstance(o, dict) and o.get("name"))


def load_domain_index(db) -> DomainIndex:
    """The cached published domains. Empty until a sync has read them."""
    from app.db.discover_domain import DiscoverDomainModel

    rows = db.query(DiscoverDomainModel).filter(DiscoverDomainModel.draft.is_(False)).all()
    keys = {r.id: r.tag_key for r in rows}
    return DomainIndex(
        Domain(
            id=r.id,
            tag_key=r.tag_key,
            parent_id=r.parent_id,
            name=_subdomain_name(r.tag_key, keys.get(r.parent_id)) if r.parent_id else r.tag_key,
            subtitle=r.subtitle,
            description=r.description,
            icon=r.icon if isinstance(r.icon, dict) else None,
            technical_owners=_owner_names(r.technical_owners),
            business_owners=_owner_names(r.business_owners),
        )
        for r in rows
    )


def workspace_entity_memberships(
    index: DomainIndex, discover_type: str, entity_ids: Iterable[str], client: Any = None,
) -> Dict[str, Membership]:
    """Domains of dashboards / Genie spaces / apps by id. Empty when no domain is cached."""
    entity_type = WORKSPACE_ENTITY_TYPES.get(discover_type)
    if not index or not entity_type:
        return {}
    by_id = workspace_entity_tag_keys(entity_type, entity_ids, client=client)
    return {i: index.resolve(keys) for i, keys in by_id.items()}


def membership_fields(m: Membership) -> Dict[str, List[str]]:
    return {"domains": list(m.domains), "subdomains": list(m.subdomains)}


# ---------------------------------------------------------------------------
# Sync: read the domains from Databricks into the cache
# ---------------------------------------------------------------------------
def fetch_domains(client) -> List[dict]:
    """Every domain and subdomain in the account, following pagination.

    There's no SDK wrapper for this Beta API yet, so it goes through the raw
    API client. Raises on any failure.
    """
    out: List[dict] = []
    token = None
    while True:
        query: Dict[str, Any] = {"page_size": _PAGE_SIZE}
        if token:
            query["page_token"] = token
        resp = client.api_client.do("GET", DOMAINS_API, query=query) or {}
        out.extend(d for d in resp.get("domains") or [] if isinstance(d, dict) and d.get("domain_id") and d.get("tag_key"))
        next_token = resp.get("next_page_token")
        if not isinstance(next_token, str) or not next_token or next_token == token:
            return out
        token = next_token


def _principal_name(client, principal_id: int) -> Optional[str]:
    """A user's, service principal's or group's display name; None if none can be read."""
    if principal_id in _principal_names:
        return _principal_names[principal_id]
    name = None
    for api, attr in ((client.users, "user_name"), (client.service_principals, "application_id"),
                      (client.groups, None)):
        try:
            p = api.get(str(principal_id))
        except Exception:  # noqa: BLE001 - not this kind of principal, or not visible here
            continue
        name = getattr(p, "display_name", None) or (getattr(p, attr, None) if attr else None)
        if name:
            break
    _principal_names[principal_id] = name
    return name


def _owners(client, ids: Any) -> List[dict]:
    out = []
    for i in ids or []:
        try:
            pid = int(i)
        except (TypeError, ValueError):
            continue
        out.append({"id": pid, "name": _principal_name(client, pid)})
    return out


def _used_tag_keys(db) -> set:
    """Lowercased tag keys on cached assets and their catalogs and schemas."""
    from app.db.data_asset import DataAssetModel

    used: set = set()
    for tags, scope_raw in db.query(DataAssetModel.tags, DataAssetModel.scope_tag_keys).all():
        used |= tag_keys(tags)
        scope = scope_keys(scope_raw)
        used |= scope.get("catalog", set()) | scope.get("schema", set())
    return used


def refresh_domain_cache(client, db) -> Optional[int]:
    """Replace the cached domains with the account's. Returns how many, or None on failure.

    Owner names are resolved only for domains that cached assets use (and their
    parents), so a large account doesn't cost a principal lookup per domain.
    """
    from app.db.discover_domain import DiscoverDomainModel

    try:
        domains = fetch_domains(client)
    except Exception as e:  # noqa: BLE001 - keep the last good list; never fail the sync
        logger.warning(
            "Could not read Discover domains from Databricks, so Discover keeps the last list it had. "
            "The sync identity needs MANAGE DISCOVER (granted on the Databricks Discover page under "
            "Manage Domains → Permissions), and the Discover page must be enabled on the workspace: %s", e,
        )
        return None

    used = _used_tag_keys(db)
    by_id = {d["domain_id"]: d for d in domains}
    wanted = {d["domain_id"] for d in domains if d["tag_key"].strip().lower() in used}
    wanted |= {by_id[i].get("parent_domain_id") for i in list(wanted) if by_id[i].get("parent_domain_id")}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for d in domains:
        resolve = d["domain_id"] in wanted
        rows.append(DiscoverDomainModel(
            id=d["domain_id"],
            tag_key=d["tag_key"],
            parent_id=d.get("parent_domain_id") or None,
            subtitle=d.get("subtitle") or None,
            description=d.get("description") or None,
            icon=d.get("icon") if isinstance(d.get("icon"), dict) else None,
            technical_owners=_owners(client, d.get("technical_owner_ids")) if resolve else None,
            business_owners=_owners(client, d.get("business_owner_ids")) if resolve else None,
            draft=bool(d.get("effective_draft", d.get("draft", False))),
            synced_at=now,
        ))
    try:
        db.query(DiscoverDomainModel).delete(synchronize_session=False)
        db.add_all(rows)
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info(
        "Cached %d Discover domain(s); %d are used by synced assets.", len(rows), len(wanted),
        extra={"data": {"domains": len(rows), "used": len(wanted)}},
    )
    return len(rows)
