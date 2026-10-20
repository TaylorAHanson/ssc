"""Who may edit what (ADR-0006 D4). Workstream D.

Authority is computed from desired state (owner tags with inheritance), so it is
fast and works for objects that don't exist yet. Reads ``metadata_objects`` /
``metadata_tags`` directly rather than through ``store``.

An item's decision combines:

* policy findings (``policy.check_item``): any deny blocks it outright;
* authority: an admin, a system principal inside its scope, a member of the
  nearest owner group, or (optionally) the object's Unity Catalog owner;
* key editor groups: a key policy may limit who changes the key (admins exempt);
* sensitivity (``policy.sensitive_reasons`` plus the environment rule): needs
  approval even with authority, unless the principal may skip approval.

``allowed`` means "may be written now, without approval". Authority that only
exists through an owner change in the same batch is never direct: the owner
change needs approval, so everything relying on it does too.
"""
from __future__ import annotations

import fnmatch
import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.metadata import OBJECT_ACTIVE, MetadataObjectModel, MetadataObservedModel, MetadataTagModel
from app.models.user import User
from app.services.approval_scope import is_platform_admin, normalize_group, normalize_roles, parse_csv
from app.services.metadata import environments, policy, principals, scope
from app.services.metadata import observed as observed_store
from app.services.metadata.errors import NotFound
from app.services.metadata.types import (
    COLUMN_SEPARATOR,
    RELATION_TYPES,
    SYSTEM_ACTOR,
    Actor,
    AuthzDecision,
    ChangeItem,
    Finding,
    ItemDecision,
    ObjectKey,
    ObservedState,
    PrincipalScope,
)

logger = logging.getLogger(__name__)

# Keeps IN (...) lists well under database parameter limits.
_IN_CHUNK = 500

# The ops that can produce each item action (replace_tags yields both sets and unsets).
_ACTION_OPS: Dict[str, Tuple[str, ...]] = {
    "set": ("set_tags", "replace_tags"),
    "unset": ("unset_tags", "replace_tags"),
    "comment_set": ("set_comment",),
    "comment_clear": ("clear_comment",),
}
_ACTION_LABELS = {
    "set": "set tags",
    "unset": "remove tags",
    "comment_set": "set descriptions",
    "comment_clear": "clear descriptions",
}

Overlay = Dict[str, Dict[str, Optional[str]]]
_ABSENT = object()


class EditableScope(BaseModel):
    """What an actor may edit directly, for search's "editable by me" filter.

    An object is editable when ``all`` is set, or when the nearest object at or
    above it carrying an owner tag is in ``owned`` (fqn_keys), or, for a
    principal, when its name matches one of ``globs``. ``environments``, when set,
    further limits it to catalogs in those environments.
    """

    all: bool = False
    owned: List[str] = Field(default_factory=list)
    # Owner-tagged objects the actor does not own (they override an owned ancestor).
    not_owned: List[str] = Field(default_factory=list)
    globs: List[str] = Field(default_factory=list)
    # Environment keys (lowercased). Empty = every environment.
    environments: List[str] = Field(default_factory=list)


# --- Actors ---------------------------------------------------------------------------


def _unique_groups(values: Iterable[Optional[str]], *, exclude: Optional[str] = None) -> List[str]:
    """Trimmed, de-duplicated case-insensitively (first spelling wins), without ``exclude``."""
    skip = normalize_group(exclude)
    seen = set()
    out: List[str] = []
    for value in values:
        name = str(value or "").strip()
        folded = normalize_group(name)
        if not folded or folded == skip or folded in seen:
            continue
        seen.add(folded)
        out.append(name)
    return out


def _is_admin_roles(roles: Sequence[str]) -> bool:
    editor_roles = set(normalize_roles(settings.METADATA_EDITOR_ROLES or []))
    return is_platform_admin(roles) or bool(set(normalize_roles(roles)) & editor_roles)


def actor_from_user(db: Session, user: User) -> Actor:
    """The Actor for a resolved request user (admin flag, groups, principal scope).

    A service principal is never an admin, whatever roles its groups map to; its
    scope comes from ``metadata_principals`` (None when the row is gone, which
    leaves it unable to edit anything).
    """
    roles = list(user.roles or [])
    if user.actor_type == "service_principal":
        app_id = principals.normalize_id(user.application_id or user.id)
        scope: Optional[PrincipalScope] = None
        try:
            found = principals.get_principal(db, app_id)
            scope = PrincipalScope(**found.model_dump(include=set(PrincipalScope.model_fields)))
        except NotFound:
            logger.warning("Service principal %s has no metadata_principals row; it can't edit metadata.", app_id)
        return Actor(
            id=app_id,
            actor_type="service_principal",
            display_name=user.full_name or app_id,
            roles=roles,
            groups=_unique_groups(user.entitlements or [], exclude=app_id),
            is_admin=False,
            principal=scope,
        )
    email = (user.email or user.id or "").strip()
    return Actor(
        id=email,
        actor_type="user",
        display_name=user.full_name or email,
        email=email or None,
        roles=roles,
        groups=_unique_groups(user.entitlements or [], exclude=email),
        is_admin=_is_admin_roles(roles),
    )


def system_actor() -> Actor:
    return Actor(id=SYSTEM_ACTOR, actor_type="system", display_name="System", is_admin=True)


def can_browse(actor: Actor) -> bool:
    """METADATA_BROWSE_ROLES blank = everyone; principals and the system always may."""
    if actor.actor_type in ("service_principal", "system"):
        return True
    browse = set(normalize_roles(settings.METADATA_BROWSE_ROLES or []))
    if not browse or actor.is_admin:
        return True
    return bool(browse & set(normalize_roles(actor.roles)))


# --- Owner tags -----------------------------------------------------------------------


def _owner_key() -> str:
    return str(settings.METADATA_OWNER_TAG_KEY or "").strip()


def _owner_key_variants() -> List[str]:
    """Spellings of the owner key that count as authority (index-friendly, unlike lower(key))."""
    key = _owner_key()
    return list(dict.fromkeys([key, key.lower(), key.upper()])) if key else []


def _changes_owner_key(item: ChangeItem) -> bool:
    owner = _owner_key().lower()
    return item.field == "tag" and bool(owner) and (item.key or "").strip().lower() == owner


def _chain(key: ObjectKey) -> List[ObjectKey]:
    """The object, then its relation (for a column), schema and catalog."""
    return [key, *key.ancestors()]


def _stored_owner_values(db: Session, fqn_keys: Iterable[str]) -> Dict[str, List[Optional[str]]]:
    """fqn_key -> owner tag values on active desired rows, in one query per chunk of keys."""
    variants = _owner_key_variants()
    keys = sorted({k for k in fqn_keys if k})
    out: Dict[str, List[Optional[str]]] = {}
    if not variants or not keys:
        return out
    for i in range(0, len(keys), _IN_CHUNK):
        rows = (
            db.query(MetadataObjectModel.fqn_key, MetadataTagModel.value)
            .join(MetadataTagModel, MetadataTagModel.object_id == MetadataObjectModel.id)
            .filter(
                MetadataObjectModel.fqn_key.in_(keys[i:i + _IN_CHUNK]),
                MetadataObjectModel.status == OBJECT_ACTIVE,
                MetadataTagModel.key.in_(variants),
            )
            .all()
        )
        for fqn_key, value in rows:
            out.setdefault(fqn_key, []).append(value)
    return out


def _split_groups(values: Iterable[Optional[str]]) -> List[str]:
    return _unique_groups(g for v in values if v for g in parse_csv(v))


def _overlay_value(overlay: Optional[Overlay], fqn_key: str):
    """The pending owner value for ``fqn_key`` (None = removed), or ``_ABSENT`` when the overlay doesn't say."""
    tags = (overlay or {}).get(fqn_key)
    if not tags:
        return _ABSENT
    variants = set(_owner_key_variants())
    matching = [v for k, v in tags.items() if str(k).strip() in variants]
    return matching[-1] if matching else _ABSENT


def _resolve_owners(
    key: ObjectKey, stored: Dict[str, List[Optional[str]]], overlay: Optional[Overlay] = None,
) -> Tuple[List[str], Optional[str]]:
    for node in _chain(key):
        fqn_key = node.fqn_key
        pending = _overlay_value(overlay, fqn_key)
        groups = _split_groups([pending] if pending is not _ABSENT else stored.get(fqn_key, []))
        if groups:
            return groups, fqn_key
    return [], None


def owner_groups(
    db: Session, key: ObjectKey, *, pending_tags: Optional[Dict[str, Dict[str, Optional[str]]]] = None,
) -> Tuple[List[str], Optional[str]]:
    """(owner groups, fqn_key they came from), nearest owner tag first: the object, its relation, schema, catalog.

    ``pending_tags`` overlays desired tags not yet written (fqn_key -> tags), so a
    change that creates a table and sets its owner in one go is evaluated consistently.
    When an overlay entry holds the owner key it replaces the stored value at that
    level; a None value means the owner tag is being removed there, so the next
    level up applies. An owner tag naming no group (key-only) is skipped the same way.
    """
    stored = _stored_owner_values(db, (n.fqn_key for n in _chain(key)))
    return _resolve_owners(key, stored, pending_tags)


def _owner_overlay(items: Sequence[ChangeItem]) -> Overlay:
    """fqn_key -> {owner key: pending value} for the items in a batch that set or remove the owner tag."""
    variants = set(_owner_key_variants())
    overlay: Overlay = {}
    for item in items:
        if item.field != "tag" or (item.key or "").strip() not in variants:
            continue
        overlay.setdefault(item.fqn_key, {})[item.key] = item.after if item.action == "set" else None
    return overlay


def _matching_groups(actor_groups: Sequence[str], groups: Sequence[str]) -> List[str]:
    mine = {normalize_group(g) for g in actor_groups}
    return [g for g in groups if normalize_group(g) in mine]


def _owner_text(groups: Sequence[str]) -> str:
    return groups[0] if len(groups) == 1 else ", ".join(groups[:-1]) + " or " + groups[-1]


# --- Unity Catalog owner --------------------------------------------------------------


def _uc_owner_keys(key: ObjectKey) -> List[str]:
    """The object's own UC owner counts; a column has none, so its relation's does."""
    return [key.fqn_key, key.relation_key().fqn_key] if key.column_name else [key.fqn_key]


def _observed_owners(
    db: Session, fqn_keys: Iterable[str], observed: Optional[Dict[str, ObservedState]] = None,
) -> Dict[str, Optional[str]]:
    """fqn_key -> UC owner, from ``observed`` when given, else (and for keys it lacks) the snapshot table."""
    owners: Dict[str, Optional[str]] = {k: v.owner for k, v in (observed or {}).items()}
    missing = sorted({k for k in fqn_keys if k and k not in owners})
    for i in range(0, len(missing), _IN_CHUNK):
        rows = (
            db.query(MetadataObservedModel.fqn_key, MetadataObservedModel.owner)
            .filter(MetadataObservedModel.fqn_key.in_(missing[i:i + _IN_CHUNK]),
                    MetadataObservedModel.present.is_(True))
            .all()
        )
        owners.update({fqn_key: owner for fqn_key, owner in rows})
    return owners


def _is_uc_owner(actor: Actor, key: ObjectKey, owners: Dict[str, Optional[str]]) -> bool:
    identities = {normalize_group(actor.email)} | {normalize_group(g) for g in actor.groups}
    identities.discard("")
    for fqn_key in _uc_owner_keys(key):
        owner = normalize_group(owners.get(fqn_key))
        if owner:
            return owner in identities
    return False


# --- System principals ----------------------------------------------------------------


def _glob_covers(glob: str, name: str) -> bool:
    """Whether a name glob covers ``name`` or one of its ancestors, matched part by part.

    ``sales_dev.bronze`` covers that schema and everything in it; ``*.bronze.*``
    covers relations in any ``bronze`` schema but not the schemas themselves.
    Matching each dot-separated part on its own keeps ``*`` from spanning parts.
    """
    pattern = glob.strip().lower().split(".")
    parts = name.strip().lower().split(".")
    if not all(pattern) or len(pattern) > len(parts):
        return False
    return all(fnmatch.fnmatchcase(part, pat) for part, pat in zip(parts, pattern))


def _principal_problem(
    actor: Actor, key: ObjectKey, environment: Optional[str], item: Optional[ChangeItem] = None,
) -> Optional[str]:
    """Why ``key`` (and ``item``) is outside the principal's scope, or None when inside it."""
    scope = actor.principal
    if scope is None:
        return f"{actor.id} isn't a registered system principal, so it can't edit metadata."
    who = f"System principal {scope.display_name or scope.application_id}"
    if not scope.enabled:
        return f"{who} is disabled."
    if not scope.scopes:
        return f"{who} has no scopes, so it can't edit anything."
    if not any(_glob_covers(g, key.name) for g in scope.scopes):
        return f"{key.name} is outside {who}'s scopes ({', '.join(scope.scopes)})."
    if scope.environments:
        allowed_envs = {e.lower() for e in scope.environments}
        if not environment or environment.lower() not in allowed_envs:
            where = f"the {environment} environment" if environment else "no environment"
            return f"{key.name} is in {where}; {who} may only edit {', '.join(scope.environments)}."
    if item is None:
        return None
    if item.field == "tag" and scope.allowed_keys:
        tag_key = (item.key or "").strip().lower()
        if not any(fnmatch.fnmatchcase(tag_key, g.strip().lower()) for g in scope.allowed_keys):
            return f"'{item.key}' isn't one of {who}'s allowed keys ({', '.join(scope.allowed_keys)})."
    if scope.allowed_operations and not set(_ACTION_OPS.get(item.action, ())) & set(scope.allowed_operations):
        return (f"{who} may not {_ACTION_LABELS.get(item.action, item.action)} "
                f"(allowed operations: {', '.join(scope.allowed_operations)}).")
    return None


def _skips_approval(actor: Actor) -> bool:
    if actor.actor_type == "system":
        return True
    return actor.actor_type == "service_principal" and actor.principal is not None and actor.principal.can_skip_approval


# --- Decisions ------------------------------------------------------------------------


def _governance_groups() -> List[str]:
    group = str(settings.METADATA_GOVERNANCE_APPROVER_GROUP or "").strip()
    return [group] if group else []


def _governed(db: Session) -> Dict[str, List[str]]:
    try:
        return observed_store.governed_tags(db) or {}
    except Exception as e:  # noqa: BLE001 - advisory; Unity Catalog still enforces governed values on write
        logger.warning("Could not read governed tag values; they aren't checked in this preview: %s", e)
        return {}


def _item_key(item: ChangeItem) -> ObjectKey:
    return ObjectKey.from_fqn_key(item.fqn_key)


def _environment(item: ChangeItem, key: ObjectKey) -> Optional[str]:
    return item.environment or environments.environment_of(key.catalog)


class _Batch:
    """Lookups shared by every item of one ``decide`` call."""

    def __init__(self, db: Session, actor: Actor, items: Sequence[ChangeItem],
                 observed: Optional[Dict[str, ObservedState]]):
        self.actor = actor
        self.keys = [_item_key(i) for i in items]
        self.stored = _stored_owner_values(db, (n.fqn_key for k in self.keys for n in _chain(k)))
        self.overlay = _owner_overlay(items)
        needs_governed = any(i.field == "tag" and i.action == "set" for i in items)
        self.governed = _governed(db) if needs_governed else {}
        self.uc_owners: Dict[str, Optional[str]] = {}
        if settings.METADATA_UC_OWNER_IS_AUTHORITY and actor.actor_type == "user" and not actor.is_admin:
            self.uc_owners = _observed_owners(db, (f for k in self.keys for f in _uc_owner_keys(k)), observed)

    def decide(self, item: ChangeItem, key: ObjectKey) -> ItemDecision:
        findings = policy.check_item(item, governed=self.governed)
        return ItemDecision(item=item, decision=self._decision(item, key, findings), findings=findings)

    def _decision(self, item: ChangeItem, key: ObjectKey, findings: List[Finding]) -> AuthzDecision:
        actor = self.actor
        owners, source = _resolve_owners(key, self.stored)
        env = _environment(item, key)
        sensitive = policy.sensitive_reasons(item)
        if environments.approval_rule(env) == "all":
            sensitive.append(f"Every change in the {env} environment needs approval.")
        decision = AuthzDecision(sensitive_reasons=sensitive, owner_source=source)

        denies = [f.message for f in findings if f.severity == "deny"]
        if denies:
            decision.can_request = False
            decision.reasons = denies
            return decision

        governance = _governance_groups()
        authorized = False
        if actor.actor_type == "service_principal":
            problem = _principal_problem(actor, key, env, item)
            if problem:
                # Owner tags never widen a principal's scope, and it can't request its way out of it.
                decision.can_request = False
                decision.reasons = [problem]
                return decision
            decision.via, authorized = "principal", True
            decision.reasons.append("Inside this system principal's scope.")
        elif actor.is_admin:
            decision.via, authorized = "admin", True
            decision.reasons.append("Admins can edit every object.")
        else:
            mine = _matching_groups(actor.groups, owners)
            if mine:
                decision.via, authorized = "owner", True
                decision.reasons.append(f"You're in {_owner_text(mine)}, named by '{_owner_key()}' on {source}.")
            elif self.uc_owners and _is_uc_owner(actor, key, self.uc_owners):
                decision.via, authorized = "uc_owner", True
                decision.reasons.append("You own this object in Unity Catalog.")
            else:
                reason, pending = self._not_owner_reason(item, key, owners, source)
                decision.reasons.append(reason)
                if pending:
                    decision.via = "owner"

        pol = policy.policy_for(item.key) if item.field == "tag" and item.key else None
        if pol is not None and pol.editor_groups and not actor.is_admin \
                and not _matching_groups(actor.groups, pol.editor_groups):
            decision.reasons.append(f"Only members of {_owner_text(pol.editor_groups)} may change '{item.key}'.")
            return self._needs_approval(decision, list(pol.editor_groups), sensitive, governance)

        if not authorized:
            base = governance if _changes_owner_key(item) or not owners else owners
            return self._needs_approval(decision, base, sensitive, governance)
        if sensitive and not _skips_approval(actor):
            return self._needs_approval(decision, owners, sensitive, governance)
        if sensitive:
            decision.reasons.append("Approval skipped: this principal may skip approval.")
        decision.allowed = True
        return decision

    def _not_owner_reason(self, item: ChangeItem, key: ObjectKey, owners: List[str],
                          source: Optional[str]) -> Tuple[str, bool]:
        """(why the actor lacks authority, whether only a pending owner change in this batch would grant it).

        The owner-key item itself is always judged on stored state, so nobody
        authorizes their own takeover.
        """
        if not _changes_owner_key(item):
            pending, pending_source = _resolve_owners(key, self.stored, self.overlay)
            if pending_source is not None and _matching_groups(self.actor.groups, pending):
                return (f"Relies on setting '{_owner_key()}' on {pending_source} to {', '.join(pending)} in this "
                        "change, which needs approval first."), True
        if owners:
            return (f"Only members of {_owner_text(owners)} (named by '{_owner_key()}' on {source}) can change "
                    "this directly. You can request the change."), False
        return (f"No '{_owner_key()}' tag on this object or above it, so only admins can change it directly. "
                "You can request the change."), False

    @staticmethod
    def _needs_approval(decision: AuthzDecision, base: Sequence[str], sensitive: Sequence[str],
                        governance: Sequence[str]) -> AuthzDecision:
        decision.allowed = False
        decision.needs_approval = True
        decision.can_request = True
        decision.approver_groups = _unique_groups([*base, *(governance if sensitive else [])])
        return decision


def decide(
    db: Session,
    actor: Actor,
    items: Sequence[ChangeItem],
    *,
    observed: Optional[Dict[str, ObservedState]] = None,
) -> List[ItemDecision]:
    """One decision per item, in order, with its policy findings (``policy.check_item``).

    ``observed`` (fqn_key -> snapshot row) supplies UC owners when
    METADATA_UC_OWNER_IS_AUTHORITY is on; keys it lacks are read from the snapshot table.
    Approver groups: the owner groups, or the governance group when there are none
    or the item changes the owner key; the key's editor groups when the actor isn't
    one; the governance group is added for sensitive items. Empty means
    Platform Admins (the gate's approval then has no assignee).
    """
    items = list(items)
    if not items:
        return []
    batch = _Batch(db, actor, items, observed)
    return [batch.decide(item, key) for item, key in zip(items, batch.keys)]


# --- Editable scope -------------------------------------------------------------------


def editable_scope(db: Session, actor: Actor) -> EditableScope:
    """Admins and the system: everything. Principals: their scope globs. Users: owner-tagged objects.

    For a user, ``owned`` lists objects whose owner tag names one of their groups
    (plus relations they own in Unity Catalog, when that counts); ``not_owned``
    lists the other owner-tagged objects. Environment rules aren't reflected here.
    """
    if actor.is_admin or actor.actor_type == "system":
        return EditableScope(all=True)
    if actor.actor_type == "service_principal":
        scope = actor.principal
        if scope is None or not scope.enabled:
            return EditableScope()
        return EditableScope(
            globs=[g.strip().lower() for g in scope.scopes if g.strip()],
            environments=[e.strip().lower() for e in scope.environments if e.strip()],
        )

    variants = _owner_key_variants()
    owned, not_owned = set(), set()
    if variants:
        rows = (
            db.query(MetadataObjectModel.fqn_key, MetadataTagModel.value)
            .join(MetadataTagModel, MetadataTagModel.object_id == MetadataObjectModel.id)
            .filter(MetadataObjectModel.status == OBJECT_ACTIVE, MetadataTagModel.key.in_(variants))
            .all()
        )
        for fqn_key, value in rows:
            groups = _split_groups([value])
            if not groups:
                continue
            (owned if _matching_groups(actor.groups, groups) else not_owned).add(fqn_key)
    uc_owned = set()
    if settings.METADATA_UC_OWNER_IS_AUTHORITY:
        # A column's UC owner is its relation's (see _uc_owner_keys), so only relations are listed.
        identities = sorted(({normalize_group(actor.email)} | {normalize_group(g) for g in actor.groups}) - {""})
        for i in range(0, len(identities), _IN_CHUNK):
            rows = (
                db.query(MetadataObservedModel.fqn_key)
                .filter(MetadataObservedModel.present.is_(True),
                        MetadataObservedModel.securable_type.in_(RELATION_TYPES),
                        func.lower(func.trim(MetadataObservedModel.owner)).in_(identities[i:i + _IN_CHUNK]))
                .all()
            )
            uc_owned.update(fqn_key for (fqn_key,) in rows)
    owned |= uc_owned
    not_owned = {k for k in not_owned
                 if k not in owned and k.partition(COLUMN_SEPARATOR)[0] not in uc_owned}
    return EditableScope(owned=sorted(owned), not_owned=sorted(not_owned))


def is_editable(db: Session, actor: Actor, key: ObjectKey) -> Tuple[bool, List[str]]:
    """(editable directly, reasons) for one object, for the object drawer.

    Object-level: key policies and sensitivity of particular edits aren't known
    here, but an environment where every change needs approval makes it not
    directly editable (except for principals that may skip approval).
    """
    if not scope.manages(key.catalog):
        return False, [scope.outside_message([key.catalog.lower()])]
    env = environments.environment_of(key.catalog)
    if actor.actor_type == "system":
        reasons = ["The app itself can edit every object."]
    elif actor.actor_type == "service_principal":
        problem = _principal_problem(actor, key, env)
        if problem:
            return False, [problem]
        reasons = ["Inside this system principal's scope."]
    elif actor.is_admin:
        reasons = ["Admins can edit every object."]
    else:
        owners, source = owner_groups(db, key)
        mine = _matching_groups(actor.groups, owners)
        if mine:
            reasons = [f"You're in {_owner_text(mine)}, named by '{_owner_key()}' on {source}."]
        elif settings.METADATA_UC_OWNER_IS_AUTHORITY and _is_uc_owner(
                actor, key, _observed_owners(db, _uc_owner_keys(key))):
            reasons = ["You own this object in Unity Catalog."]
        elif owners:
            return False, [f"Only members of {_owner_text(owners)} (named by '{_owner_key()}' on {source}) can "
                           "edit this directly. You can request a change."]
        else:
            return False, [f"No '{_owner_key()}' tag on this object or above it, so only admins can edit it "
                           "directly. You can request a change."]
    if environments.approval_rule(env) == "all" and not _skips_approval(actor):
        return False, reasons + [f"Every change in the {env} environment needs approval."]
    return True, reasons
