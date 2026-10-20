"""Registered machine callers (``metadata_principals``, ADR-0006 D4/D7).

Lookup and last-seen bookkeeping are used by request authentication
(``api.deps``); the admin CRUD is workstream D.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db.metadata import MetadataPrincipalModel
from app.services.metadata.errors import MetadataError, NotFound
from app.services.metadata.types import CHANGE_OP_TYPES, PrincipalIn, PrincipalOut, PrincipalScope

logger = logging.getLogger(__name__)

# How often a principal's last-seen row is rewritten (per process), so a busy
# pipeline doesn't write on every request.
_SEEN_WRITE_INTERVAL_SECONDS = 300.0
_last_seen_written: Dict[str, float] = {}

_MAX_ID_LENGTH = 255
_UNSAFE_ID = re.compile(r"[\s,'\"`;\x00-\x1f\x7f]")
# Name globs: the characters ObjectKey refuses, except the glob wildcards stay allowed.
_UNSAFE_GLOB = re.compile(r"[`'\";\s\x00-\x1f\x7f]")
_UNSAFE_KEY = re.compile(r"[`\x00-\x1f\x7f]")


def normalize_id(application_id: str) -> str:
    return str(application_id or "").strip().lower()


def to_scope(row: MetadataPrincipalModel) -> PrincipalScope:
    return PrincipalScope(
        application_id=row.application_id,
        display_name=row.display_name or "",
        description=row.description,
        enabled=bool(row.enabled),
        scopes=list(row.scopes or []),
        allowed_keys=list(row.allowed_keys or []),
        allowed_operations=list(row.allowed_operations or []),
        environments=list(row.environments or []),
        max_objects_per_change=row.max_objects_per_change,
        can_skip_approval=bool(row.can_skip_approval),
    )


def find_principal(db: Session, identifiers: Iterable[str]) -> Optional[PrincipalScope]:
    """The enabled principal registered under any of these identifiers, or None."""
    ids = [normalize_id(i) for i in identifiers if normalize_id(i)]
    if not ids:
        return None
    try:
        rows = (
            db.query(MetadataPrincipalModel)
            .filter(MetadataPrincipalModel.application_id.in_(ids), MetadataPrincipalModel.enabled.is_(True))
            .all()
        )
    except Exception as e:  # noqa: BLE001 - an unreadable table must not break sign-in for humans
        logger.warning("Could not look up metadata principals: %s", e)
        return None
    by_id = {r.application_id: r for r in rows}
    for i in ids:
        if i in by_id:
            return to_scope(by_id[i])
    return None


def record_seen(db: Session, application_id: str, shape: dict) -> None:
    """Remember when a principal last called and which identity headers it carried (throttled)."""
    key = normalize_id(application_id)
    now = time.monotonic()
    last = _last_seen_written.get(key)
    if last is not None and now - last < _SEEN_WRITE_INTERVAL_SECONDS:
        return
    _last_seen_written[key] = now
    try:
        row = db.get(MetadataPrincipalModel, key)
        if row is None:
            return
        first = row.last_seen_at is None
        row.last_seen_at = datetime.utcnow()
        row.last_seen_headers = shape
        db.commit()
        if first:
            logger.info("Service principal %s called for the first time; identity headers: %s", key, shape)
    except Exception as e:  # noqa: BLE001 - bookkeeping only
        db.rollback()
        logger.debug("Could not record last-seen for principal %s: %s", key, e)


def _to_out(row: MetadataPrincipalModel) -> PrincipalOut:
    return PrincipalOut(
        **to_scope(row).model_dump(),
        last_seen_at=row.last_seen_at,
        last_seen_headers=row.last_seen_headers,
        created_at=row.created_at,
        created_by=row.created_by,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def list_principals(db: Session) -> List[PrincipalOut]:
    rows = db.query(MetadataPrincipalModel).order_by(MetadataPrincipalModel.application_id).all()
    return [_to_out(r) for r in rows]


def get_principal(db: Session, application_id: str) -> PrincipalOut:
    """Raises ``errors.NotFound``."""
    row = db.get(MetadataPrincipalModel, normalize_id(application_id)) if normalize_id(application_id) else None
    if row is None:
        raise NotFound(f"No system principal is registered under '{application_id}'.")
    return _to_out(row)


def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(values))


def _validate_application_id(value: str) -> str:
    app_id = normalize_id(value)
    if not app_id:
        raise MetadataError("An application id is required.")
    if len(app_id) > _MAX_ID_LENGTH or _UNSAFE_ID.search(app_id):
        raise MetadataError(f"'{value}' isn't a valid application id: no spaces, quotes or commas.")
    if "@" in app_id:
        # deps.py matches principals on every forwarded identifier, the email included,
        # so registering a person's address would turn that person into this principal.
        raise MetadataError(f"'{value}' looks like an email address. Register the service principal's "
                            "application id instead.")
    return app_id


def _validate_scopes(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in values or []:
        glob = str(raw or "").strip().lower()
        if not glob:
            continue
        parts = glob.split(".")
        if len(parts) > 3 or not all(parts) or _UNSAFE_GLOB.search(glob):
            raise MetadataError(f"Scope '{raw}' isn't a valid name glob. Use catalog, catalog.schema or "
                                "catalog.schema.table, with * and ? as wildcards (e.g. sales_*.bronze).")
        out.append(glob)
    return _unique(out)


def _validate_keys(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in values or []:
        key = str(raw or "").strip()
        if not key:
            continue
        if _UNSAFE_KEY.search(key):
            raise MetadataError(f"Allowed key '{raw}' contains backticks or control characters.")
        out.append(key)
    return _unique(out)


def _validate_operations(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in values or []:
        op = str(raw or "").strip()
        if op not in CHANGE_OP_TYPES:
            raise MetadataError(f"'{raw}' isn't an operation. Use {', '.join(CHANGE_OP_TYPES)}.")
        out.append(op)
    return _unique(out)


def _validate_environments(values: Iterable[str]) -> List[str]:
    from app.services.metadata import environments

    configured = {k.lower(): k for k in environments.environment_keys()}
    out: List[str] = []
    for raw in values or []:
        env = str(raw or "").strip()
        if not env:
            continue
        if env.lower() not in configured:
            known = ", ".join(configured.values()) or "none are configured"
            raise MetadataError(f"'{env}' isn't a configured environment ({known}).")
        out.append(configured[env.lower()])
    return _unique(out)


def upsert_principal(db: Session, data: PrincipalIn, *, actor: str) -> Tuple[PrincipalOut, bool]:
    """(principal, created). Validates globs, operations and environments; raises ``errors.MetadataError``.

    The application id is normalized (trimmed, lowercased); scopes are lowercased.
    """
    app_id = _validate_application_id(data.application_id)
    scopes = _validate_scopes(data.scopes)
    allowed_keys = _validate_keys(data.allowed_keys)
    operations = _validate_operations(data.allowed_operations)
    envs = _validate_environments(data.environments)
    if data.max_objects_per_change is not None and data.max_objects_per_change < 1:
        raise MetadataError("The per-change object limit must be at least 1, or blank for the default.")

    now = datetime.utcnow()
    row = db.get(MetadataPrincipalModel, app_id)
    created = row is None
    if created:
        row = MetadataPrincipalModel(application_id=app_id, created_at=now, created_by=actor)
        db.add(row)
    row.display_name = (data.display_name or "").strip()
    row.description = (data.description or "").strip() or None
    row.enabled = bool(data.enabled)
    row.scopes = scopes
    row.allowed_keys = allowed_keys
    row.allowed_operations = operations
    row.environments = envs
    row.max_objects_per_change = data.max_objects_per_change
    row.can_skip_approval = bool(data.can_skip_approval)
    row.updated_at = now
    row.updated_by = actor
    db.commit()
    db.refresh(row)
    logger.info("System principal %s %s by %s", app_id, "registered" if created else "updated", actor)
    return _to_out(row), created


def delete_principal(db: Session, application_id: str, *, actor: str) -> None:
    """Raises ``errors.NotFound``."""
    key = normalize_id(application_id)
    row = db.get(MetadataPrincipalModel, key) if key else None
    if row is None:
        raise NotFound(f"No system principal is registered under '{application_id}'.")
    db.delete(row)
    db.commit()
    _last_seen_written.pop(key, None)
    logger.info("System principal %s removed by %s", key, actor)
