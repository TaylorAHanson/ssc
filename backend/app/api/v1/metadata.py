"""Metadata Manager API (``/api/v1/metadata``, ADR-0006): desired state for UC tags and descriptions.

One change service behind every route (``services/metadata/changes.py``); this
module only resolves the caller, checks browse/admin rights and maps errors.
Registered service principals may call every route; their scope is enforced by
the change service. Names travel as query parameters or in the body, never in
the path, since they contain dots. Every route is gated by ``features.metadata_v2``
and runs its database work in a worker thread.
"""
import asyncio
import logging
from contextlib import contextmanager
from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api import deps, identity
from app.core.config import settings
from app.core.feature_flags import is_feature_enabled
from app.db.session import get_db
from app.models.user import User
from app.services.metadata import (
    authz,
    changes,
    detail,
    environments,
    expand,
    observed,
    policy,
    principals,
    reconciler,
    scope,
    search,
    store,
)
from app.services.metadata.errors import ChangeBlocked, Forbidden, MetadataError, VersionConflict
from app.services.metadata.types import (
    Actor,
    AdoptRequest,
    ChangePreview,
    ChangeRequest,
    ChangeResult,
    ChangeSet,
    ChangeSetPage,
    ChangeSource,
    ImportRequest,
    KeyInfo,
    MetadataConfig,
    ObjectDetail,
    ObjectKey,
    PrincipalIn,
    PrincipalOut,
    PromoteRequest,
    ReconcileAccepted,
    ReconcileNotify,
    ReconcileResult,
    ReconcileRunPage,
    RevertRequest,
    RunRequest,
    SearchQuery,
    SearchResult,
    TagFilter,
    WhoAmI,
)

logger = logging.getLogger(__name__)


def _require_feature() -> None:
    if not is_feature_enabled("metadata_v2"):
        raise HTTPException(status_code=404, detail="The Metadata Manager is turned off")


router = APIRouter(dependencies=[Depends(_require_feature)])


@contextmanager
def _http_errors() -> Iterator[None]:
    """Map the services' exceptions to the status codes and detail shapes in the ADR contract."""
    try:
        yield
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": exc.message, "conflicts": [c.model_dump(mode="json") for c in exc.conflicts],
        }) from exc
    except ChangeBlocked as exc:
        raise HTTPException(status_code=422, detail={
            "message": exc.message, "preview": exc.preview.model_dump(mode="json"),
        }) from exc
    except MetadataError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _actor(db: Session, user: User) -> Actor:
    return authz.actor_from_user(db, user)


def _browse(db: Session, user: User) -> Actor:
    actor = _actor(db, user)
    if not authz.can_browse(actor):
        raise Forbidden("Your role can't browse the Metadata Manager. Ask a Platform Admin for access.")
    return actor


def _admin(db: Session, user: User, action: str) -> Actor:
    actor = _actor(db, user)
    if not actor.is_admin:
        raise Forbidden(f"Only Platform Admins and Metadata Manager editors can {action}.")
    return actor


def _source(request: Request, actor: Actor) -> ChangeSource:
    if actor.actor_type == "service_principal":
        return "api"
    # Browsers send Sec-Fetch-* on every fetch; scripts and pipelines don't.
    return "ui" if request.headers.get("sec-fetch-mode") else "api"


def _with_key(body_key: Optional[str], header_key: Optional[str]) -> Optional[str]:
    return (body_key or header_key or "").strip() or None


# --- Identity and configuration ----------------------------------------------------------


def _whoami(db: Session, request: Request) -> WhoAmI:
    state_user = getattr(request.state, "user", None) or {}
    fwd = state_user.get("forwarded") or {}
    ids = {"user": fwd.get("user_id"), "username": fwd.get("username"), "email": fwd.get("email")}
    headers_seen = list(fwd.get("headers_seen") or [])
    try:
        user = deps.get_current_user_or_principal(request, db, None)
    except HTTPException as exc:
        if exc.status_code != 403 or not identity.identifiers(fwd):
            raise
        caller = identity.identifiers(fwd)[0]
        return WhoAmI(
            actor=Actor(id=caller, actor_type="service_principal", display_name=caller),
            recognized_as="unregistered_service_principal", headers_seen=headers_seen, identifiers=ids,
            can_browse=False, message=str(exc.detail),
        )
    actor = _actor(db, user)
    recognized = "service_principal" if actor.actor_type == "service_principal" else "user"
    return WhoAmI(actor=actor, recognized_as=recognized, headers_seen=headers_seen, identifiers=ids,
                  can_browse=authz.can_browse(actor))


@router.get("/whoami", response_model=WhoAmI)
async def whoami(request: Request, db: Session = Depends(get_db)) -> WhoAmI:
    """What the app resolved for this caller. Answers unregistered service principals too, so they can register."""
    return await asyncio.to_thread(_whoami, db, request)


def _config(db: Session, user: User) -> MetadataConfig:
    _browse(db, user)
    return MetadataConfig(
        reconcile_mode=reconciler.current_mode(),
        owner_tag_key=settings.METADATA_OWNER_TAG_KEY,
        exempt_keys=policy.exempt_keys(),
        key_mode=settings.METADATA_KEY_MODE if settings.METADATA_KEY_MODE in ("open", "known_only") else "open",
        key_policies=policy.key_policies(),
        environments=environments.environments(),
        max_objects_per_change=settings.METADATA_MAX_OBJECTS_PER_CHANGE,
        min_reason_length=settings.METADATA_MIN_REASON_LENGTH,
        min_justification_length=settings.METADATA_MIN_JUSTIFICATION_LENGTH,
        new_object_policy="ignore" if settings.METADATA_NEW_OBJECT_POLICY == "ignore" else "adopt",
        last_sweep_at=observed.last_sweep_at(db),
        unread_catalogs=observed.unread_catalogs(db),
        circuit_breaker_tripped=reconciler.circuit_breaker_tripped(db),
        managed_catalogs=scope.patterns(),
        enforce_blocked_reason=scope.enforce_blocked_reason(),
    )


@router.get("/config", response_model=MetadataConfig)
async def get_config(
    db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> MetadataConfig:
    with _http_errors():
        return await asyncio.to_thread(_config, db, user)


# --- Search and detail -------------------------------------------------------------------


def _merged(*lists: List[str]) -> List[str]:
    return list(dict.fromkeys(v for values in lists for v in values if v))


@router.get("/objects", response_model=SearchResult)
async def search_objects(
    q: str = Query("", max_length=500),
    catalogs: List[str] = Query([]),
    catalog: List[str] = Query([]),
    schemas: List[str] = Query([]),
    schema: List[str] = Query([]),
    securable_types: List[str] = Query([]),
    securable_type: List[str] = Query([]),
    environments_: List[str] = Query([], alias="environments"),
    environment: List[str] = Query([]),
    object_states: List[str] = Query([]),
    object_state: List[str] = Query([]),
    tag: List[str] = Query([], description="key=value, key (has the key) or !key (lacks the key); repeatable"),
    drift: Optional[bool] = None,
    include_columns: bool = False,
    editable_only: bool = False,
    include_unmanaged: bool = True,
    sort: str = "name",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> SearchResult:
    with _http_errors():
        try:
            query = SearchQuery(
                q=q, catalogs=_merged(catalogs, catalog), schemas=_merged(schemas, schema),
                securable_types=_merged(securable_types, securable_type),
                environments=_merged(environments_, environment), object_states=_merged(object_states, object_state),
                tags=[TagFilter.parse(t) for t in tag if t.strip()], drift=drift, include_columns=include_columns,
                editable_only=editable_only, include_unmanaged=include_unmanaged, sort=sort, order=order,
                page=page, page_size=page_size,
            )
        except ValueError as exc:
            raise MetadataError(f"Invalid search: {exc}") from None

        def run() -> SearchResult:
            return search.search(db, query, _browse(db, user))

        return await asyncio.to_thread(run)


@router.get("/object", response_model=ObjectDetail)
async def get_object(
    name: str = Query(..., min_length=1, max_length=800),
    column: Optional[str] = Query(None, max_length=400),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> ObjectDetail:
    with _http_errors():
        return await asyncio.to_thread(lambda: detail.object_detail(db, _browse(db, user), name, column))


@router.get("/keys", response_model=List[KeyInfo])
async def list_keys(
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> List[KeyInfo]:
    with _http_errors():
        def run() -> List[KeyInfo]:
            _browse(db, user)
            return search.list_keys(db, q=q, limit=limit)

        return await asyncio.to_thread(run)


@router.get("/drift", response_model=SearchResult)
async def list_drift(
    catalog: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> SearchResult:
    with _http_errors():
        query = SearchQuery(catalogs=[catalog] if catalog else [], drift=True, include_columns=True,
                            include_unmanaged=False, sort="drift_detected_at", order="desc", page=page,
                            page_size=page_size)
        return await asyncio.to_thread(lambda: search.search(db, query, _browse(db, user)))


# --- Changes -----------------------------------------------------------------------------


@router.post("/changes/preview", response_model=ChangePreview)
async def preview_change(
    body: ChangeRequest, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ChangePreview:
    with _http_errors():
        return await asyncio.to_thread(lambda: expand.preview(db, body, _browse(db, user)))


@router.post("/changes", response_model=ChangeResult)
async def submit_change(
    body: ChangeRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> ChangeResult:
    """Commit what the caller may change directly; route the rest per ``on_approval_needed``."""
    with _http_errors():
        def run() -> ChangeResult:
            actor = _actor(db, user)
            ask = body.model_copy(update={"idempotency_key": _with_key(body.idempotency_key, idempotency_key)})
            result = expand.submit(db, ask, actor, source=_source(request, actor))
            logger.info("Metadata: %s (%s) submitted %d op(s): %s", actor.id, actor.actor_type, len(body.ops),
                        result.status)
            return result

        return await asyncio.to_thread(run)


@router.post("/changes/promote/preview", response_model=ChangePreview)
async def preview_promote(
    body: PromoteRequest, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ChangePreview:
    with _http_errors():
        return await asyncio.to_thread(lambda: changes.preview_promote(db, body, _browse(db, user)))


@router.post("/changes/promote", response_model=ChangeResult)
async def promote(
    body: PromoteRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> ChangeResult:
    with _http_errors():
        def run() -> ChangeResult:
            actor = _actor(db, user)
            ask = body.model_copy(update={"idempotency_key": _with_key(body.idempotency_key, idempotency_key)})
            return changes.promote(db, ask, actor, source=_source(request, actor))

        return await asyncio.to_thread(run)


@router.get("/changes", response_model=ChangeSetPage)
async def list_changes(
    actor: Optional[str] = None,
    source: Optional[str] = None,
    name: Optional[str] = Query(None, max_length=800),
    column: Optional[str] = Query(None, max_length=400),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> ChangeSetPage:
    with _http_errors():
        def run() -> ChangeSetPage:
            _browse(db, user)
            fqn_key = None
            if name:
                try:
                    fqn_key = ObjectKey.parse(name, column).fqn_key
                except ValueError as exc:
                    raise MetadataError(str(exc)) from None
            return store.list_change_sets(db, actor=actor, source=source, fqn_key=fqn_key, page=page,
                                          page_size=page_size)

        return await asyncio.to_thread(run)


@router.get("/changes/{change_set_id}", response_model=ChangeSet)
async def get_change(
    change_set_id: str, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ChangeSet:
    with _http_errors():
        def run() -> ChangeSet:
            _browse(db, user)
            return store.get_change_set(db, change_set_id)

        return await asyncio.to_thread(run)


@router.post("/changes/{change_set_id}/revert/preview", response_model=ChangePreview)
async def preview_revert(
    change_set_id: str,
    body: RevertRequest,
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> ChangePreview:
    with _http_errors():
        return await asyncio.to_thread(lambda: changes.preview_revert(db, change_set_id, body, _browse(db, user)))


@router.post("/changes/{change_set_id}/revert", response_model=ChangeResult)
async def revert(
    change_set_id: str,
    body: RevertRequest,
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> ChangeResult:
    with _http_errors():
        def run() -> ChangeResult:
            actor = _actor(db, user)
            return changes.revert(db, change_set_id, body, actor, source="revert")

        return await asyncio.to_thread(run)


# --- Reconciler --------------------------------------------------------------------------


def _notify(db: Session, user: User, body: ReconcileNotify) -> ReconcileAccepted:
    actor = _browse(db, user)
    accepted: List[str] = []
    rejected = []
    for name in body.names:
        try:
            key = ObjectKey.from_fqn_key(name.strip())
        except ValueError as exc:
            rejected.append({"name": name, "reason": str(exc)})
            continue
        if not scope.manages(key.catalog):
            rejected.append({"name": name, "reason": scope.outside_message([key.catalog.lower()])})
            continue
        if actor.actor_type == "service_principal":
            ok, reasons = authz.is_editable(db, actor, key)
            if not ok:
                rejected.append({"name": name, "reason": "; ".join(reasons) or "Outside this principal's scope."})
                continue
        accepted.append(key.fqn_key)
    enqueued = reconciler.enqueue(db, accepted, reason="notify", requested_by=actor.id)
    db.commit()
    logger.info("Metadata: %s notified %d object(s) (%d rejected)", actor.id, len(enqueued), len(rejected))
    return ReconcileAccepted(enqueued=enqueued, rejected=rejected)


@router.post("/reconcile", response_model=ReconcileAccepted)
async def notify(
    body: ReconcileNotify, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ReconcileAccepted:
    """A pipeline telling us it just created or changed objects; the reconciler applies their desired state."""
    with _http_errors():
        return await asyncio.to_thread(_notify, db, user, body)


@router.get("/runs", response_model=ReconcileRunPage)
async def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> ReconcileRunPage:
    with _http_errors():
        def run() -> ReconcileRunPage:
            _browse(db, user)
            return reconciler.list_runs(db, page=page, page_size=page_size)

        return await asyncio.to_thread(run)


@router.get("/runs/{run_id}", response_model=ReconcileResult)
async def get_run(
    run_id: str, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ReconcileResult:
    with _http_errors():
        def run() -> ReconcileResult:
            _browse(db, user)
            return reconciler.get_run(db, run_id)

        return await asyncio.to_thread(run)


@router.post("/runs", response_model=ReconcileResult)
async def start_run(
    body: RunRequest, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ReconcileResult:
    """Run the reconciler now: drain the queue, or sweep (``plan`` sweeps without writing)."""
    with _http_errors():
        def run() -> ReconcileResult:
            actor = _admin(db, user, "run the reconciler")
            logger.info("Metadata: %s started a %s run (catalogs %s, override %s)", actor.id, body.kind,
                        body.catalogs or "all", body.override_circuit_breaker)
            if body.kind == "queue":
                drained = reconciler.drain_queue(db)
                if drained is not None:
                    return drained
                return ReconcileResult(run_id="", trigger="queue", mode=reconciler.current_mode(), status="skipped",
                                       actor=actor.id, paused_reason="The queue is empty.")
            return reconciler.run(
                db, trigger="manual", catalogs=body.catalogs or None, mode="plan" if body.kind == "plan" else None,
                actor=actor.id, override_circuit_breaker=body.override_circuit_breaker,
            )

        return await asyncio.to_thread(run)


# --- System principals -------------------------------------------------------------------


@router.get("/principals", response_model=List[PrincipalOut])
async def list_principals(
    db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> List[PrincipalOut]:
    with _http_errors():
        def run() -> List[PrincipalOut]:
            _admin(db, user, "manage system principals")
            return principals.list_principals(db)

        return await asyncio.to_thread(run)


def _save_principal(db: Session, user: User, body: PrincipalIn, application_id: Optional[str]) -> PrincipalOut:
    actor = _admin(db, user, "manage system principals")
    if application_id is not None and principals.normalize_id(application_id) != principals.normalize_id(
            body.application_id):
        raise MetadataError("The application id in the body doesn't match the one in the path.")
    saved, created = principals.upsert_principal(db, body, actor=actor.id)
    logger.info("Metadata: %s %s system principal %s", actor.id, "registered" if created else "updated",
                saved.application_id)
    return saved


@router.post("/principals", response_model=PrincipalOut)
async def create_principal(
    body: PrincipalIn, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> PrincipalOut:
    with _http_errors():
        return await asyncio.to_thread(_save_principal, db, user, body, None)


@router.put("/principals/{application_id}", response_model=PrincipalOut)
async def update_principal(
    application_id: str,
    body: PrincipalIn,
    db: Session = Depends(get_db),
    user: User = Depends(deps.get_current_user_or_principal),
) -> PrincipalOut:
    with _http_errors():
        return await asyncio.to_thread(_save_principal, db, user, body, application_id)


@router.delete("/principals/{application_id}", status_code=204)
async def delete_principal(
    application_id: str, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> None:
    with _http_errors():
        def run() -> None:
            actor = _admin(db, user, "manage system principals")
            principals.delete_principal(db, application_id, actor=actor.id)
            logger.info("Metadata: %s deleted system principal %s", actor.id, application_id)

        await asyncio.to_thread(run)


# --- Adopt and import --------------------------------------------------------------------


@router.post("/adopt", response_model=ChangeResult)
async def adopt(
    body: AdoptRequest, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ChangeResult:
    """Make the observed state of objects their desired state (the cutover step)."""
    with _http_errors():
        return await asyncio.to_thread(lambda: changes.adopt(db, body, _admin(db, user, "adopt current state")))


@router.post("/import/preview", response_model=ChangePreview)
async def preview_import(
    body: ImportRequest, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ChangePreview:
    with _http_errors():
        return await asyncio.to_thread(lambda: changes.preview_import(db, body, _admin(db, user, "import metadata")))


@router.post("/import", response_model=ChangeResult)
async def import_rows(
    body: ImportRequest, db: Session = Depends(get_db), user: User = Depends(deps.get_current_user_or_principal),
) -> ChangeResult:
    with _http_errors():
        return await asyncio.to_thread(lambda: changes.import_rows(db, body, _admin(db, user, "import metadata")))
