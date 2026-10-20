"""One object's full picture for the drawer, ``GET /object`` and ``get_metadata`` (ADR-0006 D2).

Joins desired and observed state from Lakebase; never reads Unity Catalog.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.metadata import MetadataReconcileItemModel
from app.services.metadata import authz, diff, environments, observed, policy, store
from app.services.metadata.errors import MetadataError, NotFound
from app.services.metadata.types import (
    COLUMN_SEPARATOR,
    Actor,
    DesiredState,
    ObjectDetail,
    ObjectKey,
    ObjectSummary,
    ObservedState,
    PlanOutcome,
)

_RECENT_PLAN_LIMIT = 20


def _summary(key: ObjectKey, desired: Optional[DesiredState], seen: Optional[ObservedState]) -> ObjectSummary:
    exempt = policy.exempt_keys()
    has_drift = bool(desired and seen and seen.present
                     and any(not e.exempt for e in diff.drift_entries(desired, seen, exempt_keys=exempt)))
    return ObjectSummary(
        id=desired.id if desired else None,
        fqn_key=key.fqn_key,
        name=key.name,
        column=key.column_name,
        securable_type=(desired.securable_type if desired else seen.securable_type),  # type: ignore[union-attr]
        catalog=key.catalog,
        schema_name=key.schema_name,
        object_name=key.object_name,
        environment=desired.environment if desired else environments.environment_of(key.catalog),
        managed=desired is not None,
        tags=dict(desired.tags) if desired else {},
        tag_versions={t.key: t.version for t in desired.tag_details} if desired else None,
        comment=desired.comment.value if desired else None,
        comment_version=desired.comment.version if desired else None,
        observed_tags=dict(seen.tags) if seen else None,
        observed_comment=seen.comment if seen else None,
        object_state=desired.object_state if desired else None,
        drift=has_drift,
        last_apply_status=desired.last_apply_status if desired else None,
        last_applied_at=desired.last_applied_at if desired else None,
        updated_at=desired.updated_at if desired else None,
        data_type=seen.data_type if seen else None,
    )


def _columns(db: Session, key: ObjectKey) -> List[ObjectSummary]:
    relation = key.fqn_key
    desired_cols: Dict[str, DesiredState] = {d.fqn_key: d for d in store.get_columns(db, relation)}
    observed_cols = observed.get_columns(db, relation)
    out: List[ObjectSummary] = []
    for seen in observed_cols:
        out.append(_summary(seen.key, desired_cols.pop(seen.fqn_key, None), seen))
    for desired in sorted(desired_cols.values(), key=lambda d: d.fqn_key):
        out.append(_summary(desired.key, desired, None))
    return out


def _recent_plan(db: Session, key: ObjectKey) -> List[PlanOutcome]:
    rows = (
        db.query(MetadataReconcileItemModel)
        .filter(
            (MetadataReconcileItemModel.fqn_key == key.fqn_key)
            | MetadataReconcileItemModel.fqn_key.like(f"{key.fqn_key}{COLUMN_SEPARATOR}%")
        )
        .order_by(MetadataReconcileItemModel.created_at.desc(), MetadataReconcileItemModel.id.desc())
        .limit(_RECENT_PLAN_LIMIT)
        .all()
    )
    out: List[PlanOutcome] = []
    for row in rows:
        item_key = ObjectKey.from_fqn_key(row.fqn_key)
        out.append(PlanOutcome(
            fqn_key=row.fqn_key,
            object_id=row.object_id,
            name=item_key.name,
            column=item_key.column_name,
            securable_type="column" if item_key.column_name else "table",
            operation=row.operation,
            sql=row.sql or "",
            status=row.status,
            detail=row.detail,
        ))
    return out


def object_detail(db: Session, actor: Actor, name: str, column: Optional[str] = None) -> ObjectDetail:
    """Raises ``errors.MetadataError`` for a bad name and ``errors.NotFound`` when nothing is known about it."""
    try:
        key = ObjectKey.parse(name, column)
    except ValueError as exc:
        raise MetadataError(str(exc)) from None
    desired = store.get_object(db, key.fqn_key)
    seen = observed.get_observed(db, [key.fqn_key]).get(key.fqn_key)
    if desired is None and seen is None:
        raise NotFound(f"Nothing is known about '{key.display}': it has no desired state and hasn't been seen in "
                       "Unity Catalog. Declare tags for it to create a pending entry.")
    owners, owner_source = authz.owner_groups(db, key)
    editable, reasons = authz.is_editable(db, actor, key)
    return ObjectDetail(
        key=key,
        fqn_key=key.fqn_key,
        desired=desired,
        observed=seen,
        drift=diff.drift_entries(desired, seen, exempt_keys=policy.exempt_keys()),
        columns=_columns(db, key) if key.level == "relation" else [],
        owner_groups=owners,
        owner_source=owner_source,
        editable=editable,
        edit_reasons=reasons,
        counterparts={env: other.display for env, other in environments.counterparts(key).items()},
        history=store.object_history(db, key.fqn_key),
        recent_plan=_recent_plan(db, key),
    )
