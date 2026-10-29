"""Reads and the single write path over ``metadata_objects`` / ``metadata_tags`` and the change log.

Workstream A. Every write goes through ``write_items``: it checks versions, creates
pending rows for objects that don't have one yet, writes tags and the description
with their authorship, one event per change, the change set, the object's
``desired_hash`` and ``updated_at``, and enqueues the affected objects for the
reconciler, all in one transaction.

The write path uses Core statements (``UPDATE ... WHERE id = :id AND version = :v``
with a rowcount check, inserts guarded by the ``(object_id, key)`` unique
constraint), so two writers can't both win on Postgres. Reads use
``populate_existing`` so the session's identity map never serves stale versions.

A deleted row (``status=deleted``) reads as no tags, no description and every
version 0; writing to it restores it. Items that are ``stops_managing`` (an undo of
the change set that created the row) soft-delete it instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.metadata import (
    EVENT_COMMENT_CLEAR,
    EVENT_COMMENT_SET,
    EVENT_OBJECT_CREATE,
    EVENT_OBJECT_DELETE,
    EVENT_OBJECT_RESTORE,
    EVENT_TAG_SET,
    EVENT_TAG_UNSET,
    OBJECT_ACTIVE,
    OBJECT_DELETED,
    SOURCE_SYSTEM,
    STATE_EXISTS,
    STATE_ORPHANED,
    STATE_PENDING,
    MetadataChangeSetModel,
    MetadataEventModel,
    MetadataObjectModel,
    MetadataTagModel,
)
from app.services.metadata import authz, environments, errors, reconciler
from app.services.metadata.types import (
    COLUMN_SEPARATOR,
    Actor,
    ChangeEvent,
    ChangeItem,
    ChangeSet,
    ChangeSetPage,
    ChangeSource,
    CommentState,
    DesiredState,
    ObjectKey,
    ObjectState,
    SecurableType,
    TagAssignment,
    VersionConflictItem,
    normalize_value,
)

logger = logging.getLogger(__name__)

_OBJECTS = MetadataObjectModel.__table__
_TAGS = MetadataTagModel.__table__
_CHANGE_SETS = MetadataChangeSetModel.__table__

# IN lists are batched to this size.
_CHUNK = 500
_STATES = (STATE_EXISTS, STATE_PENDING, STATE_ORPHANED)


class ObjectSeed(BaseModel):
    """An object whose desired row must exist after a write, even when none of its tags or its
    description change (an adopted object with no tags, for one)."""

    key: ObjectKey
    securable_type: SecurableType
    object_state: ObjectState = "pending"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _chunks(values: Sequence[Any], size: int = _CHUNK) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _display(item: ChangeItem) -> str:
    return f"{item.name}.{item.column}" if item.column else item.name


def desired_hash(tags: Dict[str, Optional[str]], comment: Optional[str]) -> str:
    """sha256 of the canonical tags and comment; equal state always hashes equal."""
    payload = {
        "tags": {str(k): normalize_value(v) for k, v in sorted((tags or {}).items())},
        "comment": comment,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Reads -----------------------------------------------------------------------


def _object_key(row: Any) -> ObjectKey:
    return ObjectKey(catalog=row.catalog, schema_name=row.schema_name, object_name=row.object_name,
                     column_name=row.column_name)


def _tag_assignment(row: MetadataTagModel) -> TagAssignment:
    return TagAssignment(
        key=row.key,
        value=row.value,
        version=row.version,
        set_by=row.set_by,
        set_by_type=row.set_by_type,
        source=row.source,
        set_at=row.set_at,
        change_set_id=row.change_set_id,
    )


def _desired(row: MetadataObjectModel, tags: Sequence[MetadataTagModel]) -> DesiredState:
    details = sorted((_tag_assignment(t) for t in tags), key=lambda t: t.key)
    return DesiredState(
        id=row.id,
        key=_object_key(row),
        fqn_key=row.fqn_key,
        display_name=row.display_name,
        securable_type=row.securable_type,
        environment=row.environment,
        tags={t.key: t.value for t in details},
        tag_details=details,
        comment=CommentState(
            value=row.comment,
            version=row.comment_version or 0,
            set_by=row.comment_set_by,
            set_by_type=row.comment_set_by_type,
            source=row.comment_source,
            set_at=row.comment_set_at,
            change_set_id=row.comment_change_set_id,
        ),
        status=row.status,
        object_state=row.object_state,
        orphaned_at=row.orphaned_at,
        created_at=row.created_at,
        created_by=row.created_by,
        created_by_type=row.created_by_type,
        updated_at=row.updated_at,
        last_applied_at=row.last_applied_at,
        last_apply_status=row.last_apply_status,
        last_apply_error=row.last_apply_error,
        last_observed_at=row.last_observed_at,
        observed_created=row.observed_created,
        drift_detected_at=row.drift_detected_at,
        desired_hash=row.desired_hash,
    )


def _tags_by_object(db: Session, object_ids: Sequence[str]) -> Dict[str, List[MetadataTagModel]]:
    out: Dict[str, List[MetadataTagModel]] = {}
    ids = list(dict.fromkeys(object_ids))
    for chunk in _chunks(ids):
        for tag in db.query(MetadataTagModel).populate_existing().filter(MetadataTagModel.object_id.in_(chunk)):
            out.setdefault(tag.object_id, []).append(tag)
    return out


def _with_tags(db: Session, rows: Sequence[MetadataObjectModel]) -> List[DesiredState]:
    tags = _tags_by_object(db, [r.id for r in rows])
    return [_desired(r, tags.get(r.id, [])) for r in rows]


def get_object(db: Session, fqn_key: str, *, include_deleted: bool = False) -> Optional[DesiredState]:
    return get_objects(db, [fqn_key], include_deleted=include_deleted).get(fqn_key)


def get_objects(db: Session, fqn_keys: Iterable[str], *, include_deleted: bool = False) -> Dict[str, DesiredState]:
    """Desired state keyed by fqn_key; keys with no row are absent."""
    keys = sorted({k for k in fqn_keys if k})
    rows: List[MetadataObjectModel] = []
    for chunk in _chunks(keys):
        query = db.query(MetadataObjectModel).populate_existing().filter(MetadataObjectModel.fqn_key.in_(chunk))
        if not include_deleted:
            query = query.filter(MetadataObjectModel.status == OBJECT_ACTIVE)
        rows.extend(query.all())
    return {d.fqn_key: d for d in _with_tags(db, rows)}


def get_columns(db: Session, relation_fqn_key: str) -> List[DesiredState]:
    """Active desired rows for a relation's columns, ordered by column name."""
    rows = (
        db.query(MetadataObjectModel)
        .populate_existing()
        .filter(MetadataObjectModel.parent_key == relation_fqn_key, MetadataObjectModel.status == OBJECT_ACTIVE)
        .order_by(MetadataObjectModel.fqn_key)
        .all()
    )
    return _with_tags(db, rows)


# --- The write path --------------------------------------------------------------


@dataclass
class _Target:
    """One object a write touches, with its current row and tags (read with Core, never cached)."""

    fqn_key: str
    key: ObjectKey
    securable_type: str
    object_state: str
    items: List[ChangeItem] = field(default_factory=list)
    row: Any = None
    tags: Dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.row is not None and self.row.status == OBJECT_ACTIVE

    @property
    def display(self) -> str:
        return self.row.display_name if self.row is not None else self.key.display


def _parse_item_key(item: ChangeItem) -> ObjectKey:
    try:
        key = ObjectKey.parse(item.name, item.column)
    except ValueError as exc:
        raise errors.MetadataError(str(exc)) from None
    if key.fqn_key != item.fqn_key:
        raise errors.MetadataError(f"'{_display(item)}' doesn't match its key '{item.fqn_key}'.")
    return key


def _group(
    items: Sequence[ChangeItem],
    ensure_objects: Sequence[ObjectSeed],
    securable_types: Dict[str, SecurableType],
) -> Dict[str, _Target]:
    targets: Dict[str, _Target] = {}
    seen = set()
    for item in items:
        if item.field == "tag" and not (item.key or "").strip():
            raise errors.MetadataError(f"A tag change on {_display(item)} has no key.")
        mark = (item.fqn_key, item.field, item.key if item.field == "tag" else None)
        if mark in seen:
            raise errors.MetadataError(
                f"{_display(item)} ({item.key or 'description'}) is changed more than once in one change; "
                "combine the edits."
            )
        seen.add(mark)
        target = targets.get(item.fqn_key)
        if target is None:
            target = _Target(
                fqn_key=item.fqn_key,
                key=_parse_item_key(item),
                securable_type=securable_types.get(item.fqn_key) or item.securable_type,
                object_state=item.object_state,
            )
            targets[item.fqn_key] = target
        target.items.append(item)
    for seed in ensure_objects:
        fqn = seed.key.fqn_key
        if fqn not in targets:
            targets[fqn] = _Target(
                fqn_key=fqn,
                key=seed.key,
                securable_type=securable_types.get(fqn) or seed.securable_type,
                object_state=seed.object_state,
            )
    return targets


def _load_current(db: Session, targets: Dict[str, _Target]) -> None:
    keys = list(targets)
    for chunk in _chunks(keys):
        rows = db.execute(
            select(_OBJECTS.c.id, _OBJECTS.c.fqn_key, _OBJECTS.c.status, _OBJECTS.c.display_name,
                   _OBJECTS.c.comment, _OBJECTS.c.comment_version, _OBJECTS.c.object_state)
            .where(_OBJECTS.c.fqn_key.in_(chunk))
        ).all()
        for row in rows:
            targets[row.fqn_key].row = row
    by_id = {t.row.id: t for t in targets.values() if t.active}
    for chunk in _chunks(list(by_id)):
        rows = db.execute(
            select(_TAGS.c.id, _TAGS.c.object_id, _TAGS.c.key, _TAGS.c.value, _TAGS.c.version)
            .where(_TAGS.c.object_id.in_(chunk))
        ).all()
        for row in rows:
            by_id[row.object_id].tags[row.key] = row


def _conflicts(targets: Dict[str, _Target]) -> List[VersionConflictItem]:
    found: List[VersionConflictItem] = []
    for target in targets.values():
        if target.active and any(i.stops_managing for i in target.items):
            # Dropping the row drops every tag on it, so one set since the undo was planned is a conflict too.
            planned = {i.key for i in target.items if i.field == "tag"}
            found.extend(
                VersionConflictItem(fqn_key=target.fqn_key, name=target.display, key=key, expected_version=0,
                                    current_version=tag.version, current_value=tag.value)
                for key, tag in sorted(target.tags.items()) if key not in planned
            )
        for item in target.items:
            if item.field == "tag":
                current = target.tags.get(item.key)
                version = current.version if current is not None else 0
                value = current.value if current is not None else None
            elif target.active:
                version, value = target.row.comment_version or 0, target.row.comment
            else:
                version, value = 0, None
            if version != item.before_version:
                found.append(VersionConflictItem(
                    fqn_key=item.fqn_key,
                    name=target.display,
                    key=item.key if item.field == "tag" else None,
                    expected_version=item.before_version,
                    current_version=version,
                    current_value=value,
                ))
    return found


def _current_conflicts(db: Session, items: Sequence[ChangeItem]) -> List[VersionConflictItem]:
    targets = _group(items, (), {})
    _load_current(db, targets)
    return _conflicts(targets)


def _race_conflict(db: Session, items: Sequence[ChangeItem]) -> errors.VersionConflict:
    """After a lost race the transaction is rolled back; report what the winner left."""
    db.rollback()
    conflicts = _current_conflicts(db, items)
    if not conflicts:
        conflicts = [
            VersionConflictItem(fqn_key=i.fqn_key, name=_display(i), key=i.key if i.field == "tag" else None,
                                expected_version=i.before_version, current_version=i.before_version + 1)
            for i in items
        ]
    return errors.VersionConflict(conflicts)


class _Writer:
    """Applies grouped targets inside the caller's transaction and collects events and counts."""

    def __init__(self, db: Session, *, change_set_id: str, actor: Actor, source: str, now: datetime):
        self.db = db
        self.change_set_id = change_set_id
        self.actor = actor
        self.source = source
        self.now = now
        self.events: List[MetadataEventModel] = []
        self.names: Dict[str, str] = {}
        self.counts: Counter = Counter()
        self.touched: Dict[str, str] = {}
        self.dropped: set = set()
        self.raced: List[ChangeItem] = []
        self.environments: set = set()

    def _event(self, object_id: str, fqn_key: str, action: str, *, key: Optional[str] = None,
               before: Optional[dict] = None, after: Optional[dict] = None) -> None:
        self.events.append(MetadataEventModel(
            change_set_id=self.change_set_id,
            object_id=object_id,
            fqn_key=fqn_key,
            action=action,
            key=key,
            before=before,
            after=after,
            actor=self.actor.id,
            actor_type=self.actor.actor_type,
            source=self.source,
            created_at=self.now,
        ))
        self.touched[object_id] = fqn_key

    def _create(self, target: _Target) -> str:
        object_id = str(uuid.uuid4())
        parent = target.key.parent()
        environment = environments.environment_of(target.key.catalog)
        self.db.execute(insert(_OBJECTS).values(
            id=object_id,
            fqn_key=target.fqn_key,
            securable_type=target.securable_type,
            catalog=target.key.catalog,
            schema_name=target.key.schema_name,
            object_name=target.key.object_name,
            column_name=target.key.column_name,
            parent_key=parent.fqn_key if parent is not None else None,
            display_name=target.key.display,
            environment=environment,
            comment=None,
            comment_version=0,
            status=OBJECT_ACTIVE,
            object_state=target.object_state,
            orphaned_at=self.now if target.object_state == STATE_ORPHANED else None,
            created_at=self.now,
            created_by=self.actor.id,
            created_by_type=self.actor.actor_type,
            updated_at=self.now,
            last_apply_status="pending",
        ))
        self._event(object_id, target.fqn_key, EVENT_OBJECT_CREATE, after={
            "securable_type": target.securable_type,
            "object_state": target.object_state,
            "environment": environment,
            "display_name": target.key.display,
        })
        self.counts["created"] += 1
        if environment:
            self.environments.add(environment)
        return object_id

    def _restore(self, target: _Target) -> bool:
        row = target.row
        self.db.execute(delete(_TAGS).where(_TAGS.c.object_id == row.id))
        result = self.db.execute(
            update(_OBJECTS)
            .where(_OBJECTS.c.id == row.id, _OBJECTS.c.status == OBJECT_DELETED)
            .values(
                status=OBJECT_ACTIVE,
                object_state=target.object_state,
                orphaned_at=self.now if target.object_state == STATE_ORPHANED else None,
                comment=None,
                comment_version=0,
                comment_set_by=None,
                comment_set_by_type=None,
                comment_source=None,
                comment_set_at=None,
                comment_change_set_id=None,
            )
        )
        if result.rowcount != 1:
            return False
        self._event(row.id, target.fqn_key, EVENT_OBJECT_RESTORE,
                    before={"status": OBJECT_DELETED, "object_state": row.object_state},
                    after={"status": OBJECT_ACTIVE, "object_state": target.object_state})
        self.counts["restored"] += 1
        return True

    def _set_tag(self, object_id: str, target: _Target, item: ChangeItem) -> None:
        current = target.tags.get(item.key)
        values = dict(
            value=item.after,
            set_by=self.actor.id,
            set_by_type=self.actor.actor_type,
            source=self.source,
            set_at=self.now,
            change_set_id=self.change_set_id,
        )
        if current is None:
            self.db.execute(insert(_TAGS).values(object_id=object_id, key=item.key, version=1, **values))
            version = 1
        else:
            version = item.before_version + 1
            result = self.db.execute(
                update(_TAGS)
                .where(_TAGS.c.id == current.id, _TAGS.c.version == item.before_version)
                .values(version=version, **values)
            )
            if result.rowcount != 1:
                self.raced.append(item)
                return
        before = {"value": current.value, "version": current.version} if current is not None else None
        self._event(object_id, target.fqn_key, EVENT_TAG_SET, key=item.key, before=before,
                    after={"value": item.after, "version": version})
        self.counts["tags_set"] += 1

    def _unset_tag(self, object_id: str, target: _Target, item: ChangeItem) -> None:
        current = target.tags.get(item.key)
        if current is None:
            if item.before_exists:
                # A first row leaving out a value UC has: nothing to delete here, but syncing removes it from UC.
                self._event(object_id, target.fqn_key, EVENT_TAG_UNSET, key=item.key,
                            before={"value": item.before, "version": 0})
                self.counts["tags_unset"] += 1
            return
        result = self.db.execute(
            delete(_TAGS).where(_TAGS.c.id == current.id, _TAGS.c.version == item.before_version)
        )
        if result.rowcount != 1:
            self.raced.append(item)
            return
        self._event(object_id, target.fqn_key, EVENT_TAG_UNSET, key=item.key,
                    before={"value": current.value, "version": current.version})
        self.counts["tags_unset"] += 1

    def _set_comment(self, object_id: str, target: _Target, item: ChangeItem) -> None:
        value = (item.after or None) if item.action == "comment_set" else None
        version = item.before_version + 1
        result = self.db.execute(
            update(_OBJECTS)
            .where(_OBJECTS.c.id == object_id, _OBJECTS.c.comment_version == item.before_version)
            .values(
                comment=value,
                comment_set_by=self.actor.id,
                comment_set_by_type=self.actor.actor_type,
                comment_source=self.source,
                comment_set_at=self.now,
                comment_change_set_id=self.change_set_id,
                comment_version=version,
            )
        )
        if result.rowcount != 1:
            self.raced.append(item)
            return
        if target.active:
            previous = target.row.comment
        else:
            previous = item.before if value is None and item.before_exists else None
        before = {"value": previous, "version": item.before_version} if previous is not None else None
        after = {"value": value, "version": version} if value is not None else None
        self._event(object_id, target.fqn_key, EVENT_COMMENT_SET if value is not None else EVENT_COMMENT_CLEAR,
                    before=before, after=after)
        self.counts["comments"] += 1

    def _drop(self, target: _Target) -> None:
        """Soft-delete the row, so the object is no longer managed; the object_delete event keeps what it held."""
        if any(not i.stops_managing for i in target.items):
            raise errors.MetadataError(f"{target.display} can't stop being managed and change in the same change.")
        if not target.active:
            return
        row = target.row
        for tag in target.tags.values():
            if self.db.execute(delete(_TAGS).where(_TAGS.c.id == tag.id, _TAGS.c.version == tag.version)).rowcount != 1:
                self.raced.extend(target.items)
                return
        left = self.db.execute(select(func.count()).select_from(_TAGS).where(_TAGS.c.object_id == row.id)).scalar()
        result = self.db.execute(
            update(_OBJECTS)
            .where(_OBJECTS.c.id == row.id, _OBJECTS.c.status == OBJECT_ACTIVE,
                   _OBJECTS.c.comment_version == (row.comment_version or 0))
            .values(status=OBJECT_DELETED, comment=None, comment_version=0, comment_set_by=None,
                    comment_set_by_type=None, comment_source=None, comment_set_at=None,
                    comment_change_set_id=None, desired_hash=None, updated_at=self.now)
        )
        if left or result.rowcount != 1:
            self.raced.extend(target.items)
            return
        self._event(row.id, target.fqn_key, EVENT_OBJECT_DELETE, before={
            "status": OBJECT_ACTIVE,
            "object_state": row.object_state,
            "securable_type": target.securable_type,
            "tags": {key: tag.value for key, tag in sorted(target.tags.items())},
            "comment": row.comment,
        }, after={"status": OBJECT_DELETED})
        self.names[row.id] = target.display
        self.dropped.add(row.id)
        self.counts["unmanaged"] += 1

    def write(self, target: _Target) -> None:
        if any(i.stops_managing for i in target.items):
            self._drop(target)
            return
        if target.row is None:
            object_id = self._create(target)
        elif target.row.status == OBJECT_DELETED:
            object_id = target.row.id
            if not self._restore(target):
                self.raced.extend(target.items)
                return
        else:
            object_id = target.row.id
        self.names[object_id] = target.display
        for item in target.items:
            if item.field == "comment":
                self._set_comment(object_id, target, item)
            elif item.action == "unset":
                self._unset_tag(object_id, target, item)
            else:
                self._set_tag(object_id, target, item)

    def finish_objects(self) -> None:
        """Recompute desired_hash from what the rows hold now (a concurrent writer may have set other keys)."""
        ids = [i for i in self.touched if i not in self.dropped]
        tags: Dict[str, Dict[str, Optional[str]]] = {i: {} for i in ids}
        comments: Dict[str, Optional[str]] = {}
        for chunk in _chunks(ids):
            for row in self.db.execute(
                select(_TAGS.c.object_id, _TAGS.c.key, _TAGS.c.value).where(_TAGS.c.object_id.in_(chunk))
            ):
                tags[row.object_id][row.key] = row.value
            for row in self.db.execute(select(_OBJECTS.c.id, _OBJECTS.c.comment).where(_OBJECTS.c.id.in_(chunk))):
                comments[row.id] = row.comment
        for object_id in ids:
            self.db.execute(
                update(_OBJECTS)
                .where(_OBJECTS.c.id == object_id)
                .values(desired_hash=desired_hash(tags[object_id], comments.get(object_id)),
                        updated_at=self.now, last_apply_status="pending")
            )


def _event_dto(event: MetadataEventModel, name: Optional[str]) -> ChangeEvent:
    return ChangeEvent(
        id=event.id,
        change_set_id=event.change_set_id,
        object_id=event.object_id,
        fqn_key=event.fqn_key,
        name=name,
        action=event.action,
        key=event.key,
        before=event.before,
        after=event.after,
        actor=event.actor,
        actor_type=event.actor_type,
        source=event.source,
        created_at=event.created_at,
    )


def _change_set_dto(cs: MetadataChangeSetModel, events: Sequence[ChangeEvent] = ()) -> ChangeSet:
    return ChangeSet(
        id=cs.id,
        actor=cs.actor,
        actor_type=cs.actor_type or "user",
        source=cs.source,
        reason=cs.reason,
        idempotency_key=cs.idempotency_key,
        request_id=cs.request_id,
        reverts_change_set_id=cs.reverts_change_set_id,
        reverted_by=cs.reverted_by,
        summary=dict(cs.summary or {}),
        created_at=cs.created_at,
        events=list(events),
    )


def _mark_reverted(db: Session, reverted_id: str, by: str) -> None:
    result = db.execute(
        update(_CHANGE_SETS)
        .where(_CHANGE_SETS.c.id == reverted_id, _CHANGE_SETS.c.reverted_by.is_(None))
        .values(reverted_by=by)
    )
    if result.rowcount == 1:
        return
    current = db.execute(select(_CHANGE_SETS.c.reverted_by).where(_CHANGE_SETS.c.id == reverted_id)).first()
    if current is None:
        raise errors.NotFound(f"Change set {reverted_id} doesn't exist.")
    raise errors.MetadataError(f"Change set {reverted_id} was already reverted by change set {current.reverted_by}.")


def write_items(
    db: Session,
    items: Sequence[ChangeItem],
    *,
    actor: Actor,
    source: ChangeSource,
    reason: str = "",
    idempotency_key: Optional[str] = None,
    request_id: Optional[str] = None,
    reverts_change_set_id: Optional[str] = None,
    securable_types: Optional[Dict[str, SecurableType]] = None,
    result_payload: Optional[dict] = None,
    ensure_objects: Sequence[ObjectSeed] = (),
    commit: bool = True,
) -> ChangeSet:
    """Write resolved items atomically and return the change set.

    Each item's ``before_version`` is the version the caller expects; a mismatch
    raises ``errors.VersionConflict`` listing every conflict and writes nothing.
    ``securable_types`` gives the type for objects created by this write;
    ``ensure_objects`` creates (or restores) rows even when nothing on them changes.
    ``stops_managing`` items soft-delete their object's row instead (every item on
    it must be one). With no items and no seeds it records a change set with no events (an
    idempotency record for a change filed for approval). Commits the session unless
    ``commit`` is false, when the write is only flushed and joins the caller's
    transaction. A second change set with the same actor and idempotency key raises
    ``IntegrityError``.
    """
    try:
        change_set = _write(
            db, items, actor=actor, source=source, reason=reason, idempotency_key=idempotency_key,
            request_id=request_id, reverts_change_set_id=reverts_change_set_id,
            securable_types=securable_types or {}, result_payload=result_payload, ensure_objects=ensure_objects,
        )
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Metadata: %s wrote change set %s (%d events, source %s)",
                actor.id, change_set.id, len(change_set.events), source)
    return change_set


def _write(
    db: Session,
    items: Sequence[ChangeItem],
    *,
    actor: Actor,
    source: str,
    reason: str,
    idempotency_key: Optional[str],
    request_id: Optional[str],
    reverts_change_set_id: Optional[str],
    securable_types: Dict[str, SecurableType],
    result_payload: Optional[dict],
    ensure_objects: Sequence[ObjectSeed],
) -> ChangeSet:
    now = _now()
    change_set_id = str(uuid.uuid4())
    targets = _group(items, ensure_objects, securable_types)
    _load_current(db, targets)
    conflicts = _conflicts(targets)
    if conflicts:
        raise errors.VersionConflict(conflicts)

    writer = _Writer(db, change_set_id=change_set_id, actor=actor, source=source, now=now)
    try:
        for target in targets.values():
            if target.items or not target.active:
                writer.write(target)
    except IntegrityError:
        raise _race_conflict(db, items) from None
    if writer.raced:
        raise _race_conflict(db, writer.raced)
    writer.finish_objects()

    if reverts_change_set_id:
        _mark_reverted(db, reverts_change_set_id, change_set_id)

    counts = writer.counts
    summary = {
        "objects": len(writer.touched),
        "tags_set": counts["tags_set"],
        "tags_unset": counts["tags_unset"],
        "comments": counts["comments"],
        "created": counts["created"],
        "restored": counts["restored"],
        "environments": sorted({i.environment for i in items if i.environment} | writer.environments),
    }
    if counts["unmanaged"]:
        summary["unmanaged"] = counts["unmanaged"]
    change_set = MetadataChangeSetModel(
        id=change_set_id,
        actor=actor.id,
        actor_type=actor.actor_type,
        source=source,
        reason=(reason or "").strip() or None,
        idempotency_key=idempotency_key or None,
        request_id=request_id or None,
        reverts_change_set_id=reverts_change_set_id or None,
        summary=summary,
        result=result_payload,
        created_at=now,
    )
    db.add(change_set)
    db.add_all(writer.events)
    db.flush()
    queued = [fqn for object_id, fqn in writer.touched.items() if object_id not in writer.dropped]
    if queued:
        reconciler.enqueue(db, queued, reason="change", requested_by=actor.id, change_set_id=change_set_id)
    events = [_event_dto(e, writer.names.get(e.object_id)) for e in writer.events]
    return _change_set_dto(change_set, events)


# --- Change sets and history -----------------------------------------------------


def _names(db: Session, object_ids: Iterable[str]) -> Dict[str, str]:
    ids = list(dict.fromkeys(object_ids))
    out: Dict[str, str] = {}
    for chunk in _chunks(ids):
        for row in db.execute(select(_OBJECTS.c.id, _OBJECTS.c.display_name).where(_OBJECTS.c.id.in_(chunk))):
            out[row.id] = row.display_name
    return out


def _events_dto(db: Session, events: Sequence[MetadataEventModel]) -> List[ChangeEvent]:
    names = _names(db, [e.object_id for e in events])
    return [_event_dto(e, names.get(e.object_id)) for e in events]


def _object_and_columns(column, fqn_key: str):
    return or_(column == fqn_key, column.startswith(f"{fqn_key}{COLUMN_SEPARATOR}", autoescape=True))


def _object_and_below(column, fqn_key: str):
    return or_(_object_and_columns(column, fqn_key), column.startswith(f"{fqn_key}.", autoescape=True))


def find_idempotent(db: Session, actor_id: str, idempotency_key: str) -> Optional[dict]:
    """The stored ``ChangeResult`` dict for an earlier change by this actor with this key.

    The stored result leaves out its own change set; a committed or partial result
    gets it back from the row it was stored on.
    """
    if not actor_id or not idempotency_key:
        return None
    row = (
        db.query(MetadataChangeSetModel)
        .populate_existing()
        .filter(MetadataChangeSetModel.actor == actor_id, MetadataChangeSetModel.idempotency_key == idempotency_key)
        .order_by(MetadataChangeSetModel.created_at, MetadataChangeSetModel.id)
        .first()
    )
    if row is None:
        return None
    stored = dict(row.result or {}) or {"status": "committed", "preview": {}, "message": ""}
    if stored.get("change_set") is None and stored.get("status") in ("committed", "partial"):
        stored["change_set"] = get_change_set(db, row.id).model_dump(mode="json")
    return stored


def update_result(db: Session, change_set_id: str, result_payload: dict, *, request_id: Optional[str] = None) -> None:
    """Replace the stored result of a change set, and its request id when given (a change
    learns its request id after it's written). Commits."""
    values: Dict[str, Any] = {"result": result_payload}
    if request_id:
        values["request_id"] = request_id
    try:
        db.execute(update(_CHANGE_SETS).where(_CHANGE_SETS.c.id == change_set_id).values(**values))
        db.commit()
    except Exception:
        db.rollback()
        raise


def find_recorded(db: Session, request_id: str) -> Optional[ChangeSet]:
    """The change set that recorded an approved request (one with events, not an idempotency record)."""
    if not request_id:
        return None
    has_events = (
        select(MetadataEventModel.id).where(MetadataEventModel.change_set_id == MetadataChangeSetModel.id).exists()
    )
    row = (
        db.query(MetadataChangeSetModel)
        .populate_existing()
        .filter(MetadataChangeSetModel.request_id == request_id, has_events)
        .order_by(MetadataChangeSetModel.created_at)
        .first()
    )
    return get_change_set(db, row.id) if row is not None else None


def list_change_sets(
    db: Session,
    *,
    actor: Optional[str] = None,
    source: Optional[str] = None,
    fqn_key: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    browse: Optional[authz.BrowseScope] = None,
) -> ChangeSetPage:
    """Newest first, without events. ``fqn_key`` matches change sets touching the object or anything under it.

    With ``browse``, only change sets with at least one event on an object it sees.
    """
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 1), 500)
    query = db.query(MetadataChangeSetModel).populate_existing()
    visible = authz.browse_clause(browse, fqn=_OBJECTS.c.fqn_key, name_parts=(
        _OBJECTS.c.catalog, _OBJECTS.c.schema_name, _OBJECTS.c.object_name)) if browse is not None else None
    if visible is not None:
        query = query.filter(
            select(MetadataEventModel.id)
            .join(_OBJECTS, _OBJECTS.c.id == MetadataEventModel.object_id)
            .where(MetadataEventModel.change_set_id == MetadataChangeSetModel.id, visible)
            .exists()
        )
    if (actor or "").strip():
        query = query.filter(func.lower(MetadataChangeSetModel.actor) == actor.strip().lower())
    if (source or "").strip():
        query = query.filter(MetadataChangeSetModel.source == source.strip())
    if (fqn_key or "").strip():
        touches = (
            select(MetadataEventModel.id)
            .where(MetadataEventModel.change_set_id == MetadataChangeSetModel.id,
                   _object_and_below(MetadataEventModel.fqn_key, fqn_key.strip()))
            .exists()
        )
        query = query.filter(touches)
    total = query.count()
    rows = (
        query.order_by(MetadataChangeSetModel.created_at.desc(), MetadataChangeSetModel.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ChangeSetPage(items=[_change_set_dto(r) for r in rows], total=total, page=page, page_size=page_size)


def get_change_set(db: Session, change_set_id: str, *, include_events: bool = True) -> ChangeSet:
    """Raises ``errors.NotFound``. Callers showing it to a person apply ``visibility.visible_change_set``."""
    row = db.get(MetadataChangeSetModel, change_set_id, populate_existing=True) if change_set_id else None
    if row is None:
        raise errors.NotFound(f"Change set {change_set_id} doesn't exist.")
    events: List[ChangeEvent] = []
    if include_events:
        models = (
            db.query(MetadataEventModel)
            .filter(MetadataEventModel.change_set_id == row.id)
            .order_by(MetadataEventModel.id)
            .all()
        )
        events = _events_dto(db, models)
    return _change_set_dto(row, events)


def object_history(db: Session, fqn_key: str, *, limit: int = 50) -> List[ChangeEvent]:
    """Events for an object, newest first (columns' events are included for a relation)."""
    models = (
        db.query(MetadataEventModel)
        .filter(_object_and_columns(MetadataEventModel.fqn_key, fqn_key))
        .order_by(MetadataEventModel.id.desc())
        .limit(max(int(limit or 50), 1))
        .all()
    )
    return _events_dto(db, models)


_VALUE_EVENTS = (EVENT_TAG_SET, EVENT_TAG_UNSET, EVENT_COMMENT_SET, EVENT_COMMENT_CLEAR)
_ROW_EVENTS = (EVENT_OBJECT_CREATE, EVENT_OBJECT_RESTORE, EVENT_OBJECT_DELETE)


def _field_of(event: MetadataEventModel) -> Tuple[str, Optional[str]]:
    return ("tag", event.key) if event.action in (EVENT_TAG_SET, EVENT_TAG_UNSET) else ("comment", None)


@dataclass
class RevertPlan:
    """What undoing a change set does, built against current state."""

    items: List[ChangeItem]
    # Objects whose row the change set created: the undo drops the row (``ChangeItem.stops_managing``).
    unmanaged: List[str] = field(default_factory=list)
    # (fqn_key, display name, tag key or None for the description) a later change set changed again.
    changed_since: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)


def revert_items(db: Session, change_set_id: str) -> List[ChangeItem]:
    return revert_plan(db, change_set_id).items


def revert_plan(db: Session, change_set_id: str) -> RevertPlan:
    """Items that undo the change set: restore every tag and description it touched to its before value.

    An object whose row the change set created (Start managing, or a first edit) and
    that is still that row gets one ``stops_managing`` item per desired tag, plus one
    for the description, so the undo drops the row: the object is no longer managed
    and UC keeps what it has. Built against current state, so a value changed again
    since is still restored (and its current version is the expected one); those are
    listed in ``changed_since``. Values already back where they were produce no item.
    Raises ``errors.NotFound`` and ``errors.MetadataError`` (already reverted, or
    nothing to revert).
    """
    cs = db.get(MetadataChangeSetModel, change_set_id, populate_existing=True) if change_set_id else None
    if cs is None:
        raise errors.NotFound(f"Change set {change_set_id} doesn't exist.")
    if cs.reverted_by:
        raise errors.MetadataError(f"Change set {change_set_id} was already reverted by change set {cs.reverted_by}.")
    own = (
        db.query(MetadataEventModel)
        .filter(MetadataEventModel.change_set_id == cs.id)
        .order_by(MetadataEventModel.id)
        .all()
    )
    events = [e for e in own if e.action in _VALUE_EVENTS]
    created = {e.object_id for e in own if e.action in (EVENT_OBJECT_CREATE, EVENT_OBJECT_RESTORE)}
    if not events and not created:
        if any(e.action == EVENT_OBJECT_DELETE for e in own):
            raise errors.MetadataError(f"Change set {change_set_id} stopped managing objects, so there's nothing to "
                                       "revert. To manage them again, use Start managing.")
        raise errors.MetadataError(f"Change set {change_set_id} didn't change any tags or descriptions, "
                                   "so there's nothing to revert.")
    # The first event per tag or description holds the value from before the change set.
    targets: Dict[tuple, MetadataEventModel] = {}
    for event in events:
        field_name, key = _field_of(event)
        targets.setdefault((event.fqn_key, field_name, key), event)

    object_ids = list({e.object_id for e in events} | created)
    by_id = {
        r.id: r
        for r in db.query(MetadataObjectModel).populate_existing().filter(MetadataObjectModel.id.in_(object_ids))
    }
    rows = {r.fqn_key: r for r in by_id.values()}
    later: List[MetadataEventModel] = []
    for chunk in _chunks(object_ids):
        later.extend(
            db.query(MetadataEventModel)
            .filter(MetadataEventModel.object_id.in_(chunk), MetadataEventModel.id > own[-1].id,
                    MetadataEventModel.action.in_(_VALUE_EVENTS + _ROW_EVENTS))
            .order_by(MetadataEventModel.id)
            .all()
        )
    # Still the row the change set created: no later change set deleted it or brought it back.
    recreated = {e.object_id for e in later if e.action in _ROW_EVENTS}
    dropped = {by_id[i].fqn_key for i in created
               if i in by_id and by_id[i].status == OBJECT_ACTIVE and i not in recreated}
    current = get_objects(db, rows)

    changed_since: Dict[tuple, Tuple[str, str, Optional[str]]] = {}
    for event in later:
        if event.action not in _VALUE_EVENTS:
            continue
        field_name, key = _field_of(event)
        if event.fqn_key in dropped or (event.fqn_key, field_name, key) in targets:
            changed_since.setdefault((event.fqn_key, field_name, key),
                                     (event.fqn_key, rows[event.fqn_key].display_name, key))

    items: List[ChangeItem] = []
    for fqn in sorted(dropped):
        items.extend(_unmanage_items(rows[fqn], current[fqn]))
    for (fqn, field_name, key), event in targets.items():
        row = rows.get(fqn)
        if row is None or fqn in dropped:
            continue
        desired = current.get(fqn)
        wanted = (event.before or {}).get("value") if event.before is not None else None
        base = _row_item_base(row, object_exists=desired is not None)
        if field_name == "tag":
            tags = desired.tags if desired else {}
            versions = {t.key: t.version for t in desired.tag_details} if desired else {}
            exists_now = key in tags
            if event.before is not None:
                if exists_now and tags[key] == wanted:
                    continue
                items.append(ChangeItem(**base, field="tag", key=key, action="set", before=tags.get(key),
                                        before_exists=exists_now, before_version=versions.get(key, 0), after=wanted))
            elif exists_now:
                items.append(ChangeItem(**base, field="tag", key=key, action="unset", before=tags[key],
                                        before_exists=True, before_version=versions[key], after=None))
        else:
            now_value = desired.comment.value if desired else None
            if now_value == wanted:
                continue
            items.append(ChangeItem(
                **base, field="comment", action="comment_set" if wanted is not None else "comment_clear",
                before=now_value, before_exists=now_value is not None,
                before_version=desired.comment.version if desired else 0, after=wanted,
            ))
    return RevertPlan(items=items, unmanaged=sorted(dropped), changed_since=list(changed_since.values()))


def _row_item_base(row: MetadataObjectModel, *, object_exists: bool) -> Dict[str, Any]:
    return dict(
        fqn_key=row.fqn_key,
        name=_object_key(row).name,
        column=row.column_name,
        securable_type=row.securable_type,
        environment=row.environment,
        object_exists=object_exists,
        object_state=row.object_state,
    )


def _unmanage_items(row: MetadataObjectModel, desired: DesiredState) -> List[ChangeItem]:
    base = dict(_row_item_base(row, object_exists=True), unchanged_in_uc=True)
    items = [
        ChangeItem(**base, field="tag", key=t.key, action="unset", before=t.value, before_exists=True,
                   before_version=t.version, after=None)
        for t in sorted(desired.tag_details, key=lambda t: t.key)
    ]
    items.append(ChangeItem(
        **base, field="comment", action="comment_clear", before=desired.comment.value,
        before_exists=desired.comment.value is not None, before_version=desired.comment.version, after=None,
    ))
    return items


# --- Reconciler bookkeeping ------------------------------------------------------


def set_object_state(db: Session, fqn_key: str, state: str, *, securable_type: Optional[SecurableType] = None) -> None:
    """Observation bookkeeping used by the reconciler: exists / pending / orphaned (and the type once known).

    Not a user change, so no change set and ``updated_at`` stays. Sets
    ``orphaned_at`` on entering orphaned (keeping it while orphaned) and clears it
    on leaving. A key with no active row is ignored. Doesn't commit.
    """
    if state not in _STATES:
        raise errors.MetadataError(f"'{state}' isn't an object state. Use one of: {', '.join(_STATES)}.")
    values: Dict[str, Any] = {"object_state": state}
    if state == STATE_ORPHANED:
        values["orphaned_at"] = func.coalesce(_OBJECTS.c.orphaned_at, _now())
    else:
        values["orphaned_at"] = None
    if securable_type:
        values["securable_type"] = securable_type
    result = db.execute(
        update(_OBJECTS).where(_OBJECTS.c.fqn_key == fqn_key, _OBJECTS.c.status == OBJECT_ACTIVE).values(**values)
    )
    if result.rowcount == 0:
        logger.debug("Metadata: no active desired row for %s; state %s not recorded", fqn_key, state)


def purge_orphans(db: Session, *, older_than_days: int, read_after: Mapping[str, datetime]) -> int:
    """Soft-delete rows orphaned longer than the retention, under one system change set. Commits.

    Only rows whose catalog (lowercased) is in ``read_after`` with a successful read
    that began after the row was orphaned: an orphan is purged only once a later full
    read confirmed the object is still gone. A purged row keeps its identity but loses
    its tags and description; its ``object_delete`` event keeps what they were.
    """
    cutoff = _now() - timedelta(days=max(int(older_than_days or 0), 0))
    rows = [
        row for row in db.execute(
            select(_OBJECTS.c.id, _OBJECTS.c.fqn_key, _OBJECTS.c.catalog, _OBJECTS.c.display_name,
                   _OBJECTS.c.securable_type, _OBJECTS.c.comment, _OBJECTS.c.orphaned_at)
            .where(_OBJECTS.c.status == OBJECT_ACTIVE, _OBJECTS.c.object_state == STATE_ORPHANED,
                   _OBJECTS.c.orphaned_at.is_not(None), _OBJECTS.c.orphaned_at < cutoff)
            .order_by(_OBJECTS.c.fqn_key)
        ).all()
        if (read_after.get((row.catalog or "").lower()) or datetime.min) > row.orphaned_at
    ]
    if not rows:
        return 0
    actor = authz.system_actor()
    now = _now()
    change_set_id = str(uuid.uuid4())
    try:
        tags: Dict[str, Dict[str, Optional[str]]] = {}
        for chunk in _chunks([r.id for r in rows]):
            for tag in db.execute(select(_TAGS.c.object_id, _TAGS.c.key, _TAGS.c.value)
                                  .where(_TAGS.c.object_id.in_(chunk))):
                tags.setdefault(tag.object_id, {})[tag.key] = tag.value
        purged = 0
        for row in rows:
            result = db.execute(
                update(_OBJECTS)
                .where(_OBJECTS.c.id == row.id, _OBJECTS.c.status == OBJECT_ACTIVE)
                .values(status=OBJECT_DELETED, comment=None, comment_version=0, comment_set_by=None,
                        comment_set_by_type=None, comment_source=None, comment_set_at=None,
                        comment_change_set_id=None, desired_hash=None, updated_at=now)
            )
            if result.rowcount != 1:
                continue
            db.execute(delete(_TAGS).where(_TAGS.c.object_id == row.id))
            db.add(MetadataEventModel(
                change_set_id=change_set_id,
                object_id=row.id,
                fqn_key=row.fqn_key,
                action=EVENT_OBJECT_DELETE,
                before={
                    "status": OBJECT_ACTIVE,
                    "object_state": STATE_ORPHANED,
                    "securable_type": row.securable_type,
                    "orphaned_at": row.orphaned_at.isoformat() if row.orphaned_at else None,
                    "tags": tags.get(row.id, {}),
                    "comment": row.comment,
                },
                after={"status": OBJECT_DELETED},
                actor=actor.id,
                actor_type=actor.actor_type,
                source=SOURCE_SYSTEM,
                created_at=now,
            ))
            purged += 1
        if purged:
            db.add(MetadataChangeSetModel(
                id=change_set_id,
                actor=actor.id,
                actor_type=actor.actor_type,
                source=SOURCE_SYSTEM,
                reason=f"Purged objects orphaned for more than {int(older_than_days or 0)} days.",
                summary={"objects": purged, "deleted": purged},
                created_at=now,
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Metadata: purged %d orphaned object(s) in change set %s", purged, change_set_id)
    return purged


def object_key(desired: DesiredState) -> ObjectKey:
    return desired.key
