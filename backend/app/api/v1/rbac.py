"""Access Rules API (``/api/v1/rbac``): the ledger grid, history, import and export.

- ``GET  /permissions``                    the caller's rights and each type's approver group
- ``GET  /sheets``                         managed sheets with layout, row count and export path
- ``GET  /rules``                          rules (filter by sheet, plane, environment, search)
- ``POST /changes/validate``               dry run of a direct edit
- ``POST /changes``                        commit a direct edit (or file a request under the
                                           two-person rule)
- ``GET  /history``, ``GET /history/{id}`` change sets and their diffs
- ``POST /history/{id}/revert/validate``   dry run of a revert
- ``POST /history/{id}/revert``            record the inverse change set (or file a request
                                           under the two-person rule)
- ``GET  /imports``, ``POST /imports/preview``, ``POST /imports``
                                           workbook import (Platform Admin)
- ``GET  /exports``, ``POST /exports``     export runs, the schedule and targets; export now
- ``GET  /exports/{id}/files/{file_id}``   download an archived CSV (by id, never by path)
- ``POST /exports/test-path``              write and delete a probe file at a target's path
- ``POST /exports/compare``                compare a sheet with a manually exported CSV

Every endpoint is gated by ``features.rbac`` and authorized through
``app.services.rbac.approvers`` (Platform Admin, viewer roles, or a type's
approver group). Uploads are parsed in memory and capped; nothing a client
sends is used as a filesystem path. Endpoints are async and run every database
or service call in a worker thread.
"""
import asyncio
import logging
import re
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import AsyncIterator, Iterable, Iterator, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from python_multipart.exceptions import FormParserError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.deps import get_current_user
from app.core.feature_flags import is_feature_enabled
from app.db.rbac import PLANES, SOURCE_IMPORT, RbacChangeSetModel, RbacRuleEventModel, RbacRuleModel
from app.db.session import get_db
from app.models.rbac import (
    ChangeSetDetail,
    ChangeSetIn,
    ChangeSetPreview,
    ChangeSetResult,
    CompareResult,
    ExportRun,
    ExportRunIn,
    ExportStatus,
    HistoryPage,
    ImportPreview,
    ImportResult,
    ImportSummary,
    PathCheckIn,
    PathCheckResult,
    Plane,
    RbacPermissions,
    RevertIn,
    RulesPage,
    SheetInfo,
)
from app.models.user import User
from app.services.rbac import approvers, csv_export, layout, ledger, workbook_import
from app.services.rbac.approvers import RbacForbidden
from app.services.rbac.csv_export import ExportBusy
from app.services.rbac.ledger import ChangeBlocked, LedgerError, VersionConflict
from app.services.rbac.workbook_import import ImportFailed

logger = logging.getLogger(__name__)

MAX_WORKBOOK_BYTES = 25 * 1024 * 1024
MAX_COMPARE_BYTES = 20 * 1024 * 1024
# Room for multipart boundaries, part headers and the small text fields.
_FORM_OVERHEAD_BYTES = 64 * 1024
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


def _require_feature() -> None:
    if not is_feature_enabled("rbac"):
        raise HTTPException(status_code=404, detail="Access Rules are turned off")


router = APIRouter(dependencies=[Depends(_require_feature)])


# --- Authorization and error mapping ---------------------------------------------


def _is_platform_admin(user: User) -> bool:
    return user.has_role(approvers.PLATFORM_ADMIN)


def _require_platform_admin(user: User, action: str) -> None:
    if not _is_platform_admin(user):
        raise RbacForbidden(f"Only Platform Admins can {action}.")


def _require_export(user: User) -> None:
    """Platform Admin or a member of any type's approver group (``permissions().can_export``)."""
    if not (_is_platform_admin(user) or approvers.editable_planes(user)):
        raise RbacForbidden("Only Platform Admins and approver group members can run or test exports.")


def _ordered(planes: Iterable[str]) -> List[str]:
    return [p for p in PLANES if p in set(planes)]


def _labels(planes: List[str]) -> str:
    return " and ".join(layout.PLANE_LABELS.get(p, p) for p in planes)


@contextmanager
def _http_errors() -> Iterator[None]:
    """Map the services' exceptions to the status codes and detail shapes the UI expects."""
    try:
        yield
    except RbacForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except VersionConflict as exc:
        current = exc.current.model_dump(mode="json") if exc.current is not None else None
        raise HTTPException(
            status_code=409, detail={"message": str(exc), "rule_id": exc.rule_id, "current": current},
        ) from exc
    except ChangeBlocked as exc:
        raise HTTPException(
            status_code=422, detail={"message": str(exc), "preview": exc.preview.model_dump(mode="json")},
        ) from exc
    except (LedgerError, ImportFailed) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExportBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --- Uploads ---------------------------------------------------------------------


class _UploadTooLarge(MultiPartException):
    pass


class _InMemoryMultiPartParser(MultiPartParser):
    """Starlette's parser with a spool limit above the upload cap, so a file never rolls over to disk."""

    def __init__(self, request: Request, stream: AsyncIterator[bytes], *, spool_max_size: int):
        super().__init__(request.headers, stream, max_files=1, max_fields=10)
        self.spool_max_size = spool_max_size


def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=f"The file is larger than the {limit // (1024 * 1024)} MB limit.")


async def _read_form(request: Request, *, limit: int) -> FormData:
    """Parse a multipart form holding one file of at most ``limit`` bytes, entirely in memory."""
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise HTTPException(status_code=400, detail="Upload the file as multipart/form-data.")
    budget = limit + _FORM_OVERHEAD_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > budget:
        raise _too_large(limit)

    async def capped() -> AsyncIterator[bytes]:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > budget:
                raise _UploadTooLarge("upload too large")
            yield chunk

    parser = _InMemoryMultiPartParser(request, capped(), spool_max_size=budget + 1)
    try:
        return await parser.parse()
    except _UploadTooLarge:
        raise _too_large(limit) from None
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail=f"The upload couldn't be read: {exc.message}") from exc
    except FormParserError as exc:
        raise HTTPException(status_code=400, detail="The upload couldn't be read as a multipart form.") from exc


async def _form_file(form: FormData, *, limit: int) -> Tuple[str, bytes]:
    """``(base filename, content)`` of the form's ``file`` field."""
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail='Attach the file in the "file" field.')
    data = await upload.read()
    if len(data) > limit:
        raise _too_large(limit)
    name = PurePosixPath((upload.filename or "").replace("\\", "/")).name[:255]
    return name, data


def _form_text(form: FormData, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


async def _workbook_upload(request: Request) -> Tuple[FormData, str, bytes]:
    form = await _read_form(request, limit=MAX_WORKBOOK_BYTES)
    filename, data = await _form_file(form, limit=MAX_WORKBOOK_BYTES)
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx workbooks can be imported.")
    return form, filename, data


# --- Blocking work (run in a worker thread) ------------------------------------------


def _authorize_change(db: Session, user: User, change: ChangeSetIn) -> List[str]:
    """The planes a direct edit touches, after checking the user may edit every one of them."""
    approvers.require_view(user)
    planes = _ordered(ledger.planes_in_change(db, change))
    for plane in planes:
        approvers.require_edit(user, plane)
    return planes


def _validate_change(db: Session, user: User, change: ChangeSetIn) -> ChangeSetPreview:
    """Dry run. The reason is judged only once given; committing always requires it."""
    _authorize_change(db, user, change)
    if not change.reason.strip():
        return ledger.preview_change_set_without_reason(db, change, actor=user.email)
    return ledger.preview_change_set(db, change, actor=user.email)


def _commit_change(db: Session, user: User, change: ChangeSetIn) -> ChangeSetResult:
    planes = _authorize_change(db, user, change)
    preview = ledger.preview_change_set(db, change, actor=user.email)
    if not preview.two_person_required:
        result = ledger.commit_change_set(db, change, actor=user.email)
        logger.info("Access Rules: %s committed change set %s (%d rows) from the grid",
                    user.email, result.change_set_id, len(result.diffs))
        return result

    if len(planes) != 1:
        raise HTTPException(status_code=400, detail=(
            "Split the change by type. The two-person rule files one request per type, and this change "
            f"touches {_labels(planes)} rules."
        ))
    if preview.blocked:
        raise ChangeBlocked(preview)
    from app.services.rbac import requests as rbac_requests

    request_id = rbac_requests.submit_row_changes(db, change, plane=planes[0], requester_email=user.email)
    logger.info("Access Rules: %s filed request %s for a %s edit (%d rows) under the two-person rule",
                user.email, request_id, planes[0], len(preview.diffs))
    return ChangeSetResult(request_id=request_id, diffs=preview.diffs, findings=preview.findings)


def _change_set_planes(db: Session, cs: RbacChangeSetModel) -> List[str]:
    planes = set((cs.summary or {}).get("planes") or [])
    if not planes:
        planes = {
            plane for (plane,) in db.query(RbacRuleModel.plane)
            .join(RbacRuleEventModel, RbacRuleEventModel.rule_id == RbacRuleModel.id)
            .filter(RbacRuleEventModel.change_set_id == cs.id)
            .distinct()
        }
    return _ordered(planes)


def _authorize_revert(db: Session, user: User, change_set_id: str) -> None:
    approvers.require_view(user)
    cs = db.get(RbacChangeSetModel, change_set_id) if change_set_id else None
    if cs is None:
        raise HTTPException(status_code=404, detail="That change set doesn't exist.")
    if cs.source == SOURCE_IMPORT:
        _require_platform_admin(user, "revert a workbook import")
    for plane in _change_set_planes(db, cs):
        approvers.require_edit(user, plane)


def _validate_revert(db: Session, user: User, change_set_id: str) -> ChangeSetPreview:
    _authorize_revert(db, user, change_set_id)
    return ledger.revert_preview(db, change_set_id, actor=user.email)


def _revert(db: Session, user: User, change_set_id: str, reason: str) -> ChangeSetResult:
    """Revert now, or file a request when a touched environment has the two-person rule.

    A revert can't be split by type, so one spanning several types is filed as a
    single request that Platform Admins approve.
    """
    _authorize_revert(db, user, change_set_id)
    preview = ledger.revert_preview(db, change_set_id, actor=user.email, reason=reason)
    if not preview.two_person_required:
        result = ledger.revert_change_set(db, change_set_id, actor=user.email, reason=reason)
        logger.info("Access Rules: %s reverted change set %s as %s", user.email, change_set_id,
                    result.change_set_id)
        return result

    if preview.blocked:
        raise ChangeBlocked(preview)
    from app.services.rbac import requests as rbac_requests

    planes = _ordered({d.plane for d in preview.diffs})
    request_id = rbac_requests.submit_revert(
        db, change_set_id, plane=planes[0] if len(planes) == 1 else None, requester_email=user.email,
        reason=reason,
    )
    logger.info("Access Rules: %s filed request %s to revert change set %s (%s, %d rows) under the "
                "two-person rule", user.email, request_id, change_set_id, ", ".join(planes), len(preview.diffs))
    return ChangeSetResult(request_id=request_id, diffs=preview.diffs, findings=preview.findings)


def _change_set(db: Session, user: User, change_set_id: str) -> ChangeSetDetail:
    approvers.require_view(user)
    detail = ledger.change_set_detail(db, change_set_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="That change set doesn't exist.")
    return detail


def _test_path(user: User, target: str) -> PathCheckResult:
    result = csv_export.test_path(target)
    logger.info("Access Rules: %s tested the export path for %s: %s", user.email, target,
                "ok" if result.ok else "failed")
    return result


def _export_file(db: Session, user: User, export_id: str, file_id: str) -> Tuple[str, bytes]:
    approvers.require_view(user)
    found = csv_export.get_file(db, export_id, file_id)
    if found is None:
        raise HTTPException(status_code=404, detail="That export file isn't available.")
    return found


# --- Permissions, sheets and rules ---------------------------------------------------


@router.get("/permissions", response_model=RbacPermissions)
async def get_permissions(user: User = Depends(get_current_user)) -> RbacPermissions:
    """The caller's rights. Never 403: the sidebar asks for every user and hides the page on ``can_view``."""
    return await approvers.permissions(user)


@router.get("/sheets", response_model=List[SheetInfo])
async def list_sheets(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> List[SheetInfo]:
    with _http_errors():
        approvers.require_view(user)
        return await asyncio.to_thread(ledger.sheet_infos, db, set(approvers.editable_planes(user)))


@router.get("/rules", response_model=RulesPage)
async def list_rules(
    sheet: Optional[str] = None,
    plane: Optional[Plane] = None,
    environment: Optional[str] = None,
    q: Optional[str] = Query(None, max_length=200),
    include_deleted: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RulesPage:
    with _http_errors():
        approvers.require_view(user)
        return await asyncio.to_thread(
            ledger.list_rules, db, sheet=sheet, plane=plane, environment=environment, q=q,
            include_deleted=include_deleted, offset=offset, limit=limit,
        )


# --- Direct edits ------------------------------------------------------------------------


@router.post("/changes/validate", response_model=ChangeSetPreview)
async def validate_changes(
    change: ChangeSetIn, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> ChangeSetPreview:
    with _http_errors():
        return await asyncio.to_thread(_validate_change, db, user, change)


@router.post("/changes", response_model=ChangeSetResult)
async def commit_changes(
    change: ChangeSetIn, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> ChangeSetResult:
    """Commit all-or-nothing, or file a request when a touched environment has the two-person rule."""
    with _http_errors():
        return await asyncio.to_thread(_commit_change, db, user, change)


# --- History and revert ------------------------------------------------------------------


@router.get("/history", response_model=HistoryPage)
async def list_history(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HistoryPage:
    with _http_errors():
        approvers.require_view(user)
        return await asyncio.to_thread(ledger.history, db, offset=offset, limit=limit)


@router.get("/history/{change_set_id}", response_model=ChangeSetDetail)
async def get_change_set(
    change_set_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> ChangeSetDetail:
    with _http_errors():
        return await asyncio.to_thread(_change_set, db, user, change_set_id)


@router.post("/history/{change_set_id}/revert/validate", response_model=ChangeSetPreview)
async def validate_revert(
    change_set_id: str,
    body: Optional[RevertIn] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChangeSetPreview:
    with _http_errors():
        return await asyncio.to_thread(_validate_revert, db, user, change_set_id)


@router.post("/history/{change_set_id}/revert", response_model=ChangeSetResult)
async def revert_change_set(
    change_set_id: str, body: RevertIn, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> ChangeSetResult:
    with _http_errors():
        return await asyncio.to_thread(_revert, db, user, change_set_id, body.reason)


# --- Import (Platform Admin) ---------------------------------------------------------------


@router.get("/imports", response_model=List[ImportSummary])
async def list_imports(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> List[ImportSummary]:
    with _http_errors():
        _require_platform_admin(user, "import the workbook")
        return await asyncio.to_thread(workbook_import.list_imports, db)


@router.post("/imports/preview", response_model=ImportPreview)
async def preview_import(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> ImportPreview:
    """Multipart ``file`` (.xlsx, at most 25 MB)."""
    with _http_errors():
        _require_platform_admin(user, "import the workbook")
        _, filename, data = await _workbook_upload(request)
        preview = await asyncio.to_thread(workbook_import.preview, db, data, filename, actor=user.email)
    logger.info("Access Rules: %s previewed workbook %s as import %s (mode %s)",
                user.email, filename, preview.import_id, preview.mode)
    return preview


@router.post("/imports", response_model=ImportResult)
async def run_import(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> ImportResult:
    """Multipart ``file`` (the previewed workbook), ``preview_id``, ``mode`` and ``confirmation``."""
    with _http_errors():
        _require_platform_admin(user, "import the workbook")
        form, filename, data = await _workbook_upload(request)
        preview_id = _form_text(form, "preview_id")
        if not preview_id:
            raise HTTPException(status_code=400, detail="Preview the workbook before importing it.")
        result = await asyncio.to_thread(
            workbook_import.run, db, data, filename, preview_id=preview_id, mode=_form_text(form, "mode"),
            confirmation=_form_text(form, "confirmation"), actor=user.email,
        )
    logger.info("Access Rules: %s ran import %s (change set %s, %d created, %d deleted)",
                user.email, result.import_id, result.change_set_id, result.created, result.deleted)
    return result


# --- Export ------------------------------------------------------------------------------


@router.get("/exports", response_model=ExportStatus)
async def get_exports(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ExportStatus:
    with _http_errors():
        approvers.require_view(user)
        return await asyncio.to_thread(csv_export.status, db)


@router.post("/exports", response_model=ExportRun)
async def run_export(
    body: Optional[ExportRunIn] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> ExportRun:
    force = bool(body and body.force)
    with _http_errors():
        _require_export(user)
        run = await asyncio.to_thread(csv_export.run_export, db, trigger="manual", actor=user.email, force=force)
    logger.info("Access Rules: %s ran export %s (force=%s): %s", user.email, run.id or "-", force, run.status)
    return run


@router.get("/exports/{export_id}/files/{file_id}")
async def download_export_file(
    export_id: str, file_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> Response:
    """An archived export file, looked up by id; the stored path is never read."""
    with _http_errors():
        filename, content = await asyncio.to_thread(_export_file, db, user, export_id, file_id)
    safe_name = _FILENAME_UNSAFE.sub("_", filename) or "export.csv"
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/exports/test-path", response_model=PathCheckResult)
async def check_export_path(body: PathCheckIn, user: User = Depends(get_current_user)) -> PathCheckResult:
    """``target`` is an environment key or ``metastore``; the path comes from settings, never the client."""
    with _http_errors():
        _require_export(user)
        targets = [key for key, _, _, _ in layout.export_targets()]
        if body.target not in targets:
            raise HTTPException(status_code=400, detail=(
                f'"{body.target}" isn\'t an export target. Use one of: {", ".join(targets)}.'
            ))
        return await asyncio.to_thread(_test_path, user, body.target)


@router.post("/exports/compare", response_model=CompareResult)
async def compare_export(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> CompareResult:
    """Multipart ``sheet`` and ``file`` (a manually exported CSV, at most 20 MB)."""
    with _http_errors():
        approvers.require_view(user)
        form = await _read_form(request, limit=MAX_COMPARE_BYTES)
        sheet = _form_text(form, "sheet")
        spec = layout.sheet_spec(sheet)
        if spec is None:
            raise HTTPException(status_code=400, detail=f'"{sheet}" isn\'t one of the managed sheets.')
        _, data = await _form_file(form, limit=MAX_COMPARE_BYTES)
        try:
            return await asyncio.to_thread(csv_export.compare, db, spec.name, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
