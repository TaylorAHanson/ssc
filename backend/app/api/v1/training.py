"""Training / lightweight-LMS API.

Three surfaces:

* **Learner** — ``GET /me`` (tracks + courses + media + this user's
  consumption/status), ``GET /media/{id}/stream`` (Range-enabled media
  streaming), ``POST /consumption`` (playback progress heartbeats).
* **Admin** — CRUD for tracks/courses/media (media bytes go to a UC Volume),
  ``POST /catalog/sync`` (scrape the public catalog), ``GET
  /analytics/consumption``, and the legacy ``POST /upload`` (Academy CSV).
* **Workflow editor** — ``GET /courses`` (flat course list for the training-gate
  course picker).

Writes require Platform/Governance Admin; reads are available to any
authenticated user.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.providers.training.catalog_scraper import scrape_catalog
from app.providers.training.client import TrainingProvider
from app.providers.training.storage import TrainingMediaStorage
from app.services.training_service import TrainingService

logger = logging.getLogger(__name__)

router = APIRouter()

_WRITE_ROLES = ["Platform Admin", "Governance Admin"]

# Media kinds tracked for consumption vs. simple downloadable resources.
_VIDEO_KINDS = {"video"}


# --------------------------------------------------------------------- schemas

class TrackCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    persona: Optional[str] = None
    icon: Optional[str] = None
    status: str = "published"
    sort_order: Optional[int] = None


class TrackUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    persona: Optional[str] = None
    icon: Optional[str] = None
    status: Optional[str] = None
    sort_order: Optional[int] = None


class CourseCreate(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    course_code: Optional[str] = None
    external_url: Optional[str] = None
    section: Optional[str] = None
    course_type: Optional[str] = None
    duration: Optional[str] = None
    unlocks: Optional[str] = None
    status: str = "published"
    sort_order: Optional[int] = None


class CourseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    course_code: Optional[str] = None
    external_url: Optional[str] = None
    section: Optional[str] = None
    course_type: Optional[str] = None
    duration: Optional[str] = None
    unlocks: Optional[str] = None
    status: Optional[str] = None
    sort_order: Optional[int] = None
    track_id: Optional[str] = None


class MediaUpdate(BaseModel):
    title: Optional[str] = None
    kind: Optional[str] = None
    sort_order: Optional[int] = None
    duration_seconds: Optional[float] = None


class ConsumptionUpdate(BaseModel):
    media_id: str
    position_seconds: float = Field(..., ge=0)
    total_seconds: Optional[float] = Field(default=None, ge=0)


# --------------------------------------------------------------------- learner

@router.get("/me", response_model=Dict[str, Any])
async def get_my_training(
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """Tracks with courses, media, and the current user's status/consumption."""
    provider = TrainingProvider(db)
    completed_codes = set(provider.get_user_training_status(current_user.email))

    tracks = TrainingService.list_tracks(db, include_drafts=False)
    course_ids: List[str] = []
    track_payloads: List[Dict[str, Any]] = []
    for track in tracks:
        courses = [c for c in TrainingService.list_courses(db, track.id) if c.status == "published"]
        course_ids.extend(c.id for c in courses)
        track_payloads.append((track, courses))

    consumption = TrainingService.consumption_for_user(db, current_user.email, course_ids)

    result_tracks: List[Dict[str, Any]] = []
    for track, courses in track_payloads:
        course_dicts: List[Dict[str, Any]] = []
        completed_count = 0
        for course in courses:
            media = TrainingService.list_media(db, course.id)
            media_payload = []
            videos = [m for m in media if m.kind in _VIDEO_KINDS]
            pct_values: List[float] = []
            videos_completed = 0
            for m in media:
                cons = consumption.get(m.id)
                md = TrainingService.media_to_dict(m)
                md["consumption"] = (
                    TrainingService.consumption_to_dict(cons) if cons else None
                )
                media_payload.append(md)
                if m.kind in _VIDEO_KINDS:
                    pct_values.append(cons.percent_complete if cons else 0.0)
                    if cons and cons.completed:
                        videos_completed += 1

            in_app_complete = bool(videos) and videos_completed == len(videos)
            code_complete = bool(course.course_code) and course.course_code in completed_codes
            completed = in_app_complete or code_complete
            if completed:
                completed_count += 1
            progress = (sum(pct_values) / len(pct_values)) if pct_values else (1.0 if completed else 0.0)

            cd = TrainingService.course_to_dict(db, course)
            cd["media"] = media_payload
            cd["status"] = "completed" if completed else ("in_progress" if progress > 0 else "not_started")
            cd["progress"] = round(progress, 3)
            course_dicts.append(cd)

        td = TrainingService.track_to_dict(db, track)
        td["courses"] = course_dicts
        td["completed_count"] = completed_count
        td["total_count"] = len(course_dicts)
        result_tracks.append(td)

    return {
        "tracks": result_tracks,
        "completed_codes": sorted(completed_codes),
    }


@router.get("/courses", response_model=List[Dict[str, str]])
async def list_courses(
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """Flat, de-duplicated ``{code, name}`` list for the training-gate picker."""
    seen: Dict[str, str] = {}
    for course in TrainingService.list_courses(db):
        code = course.course_code
        if code and code not in seen:
            seen[code] = course.title or code
    return [{"code": code, "name": name} for code, name in sorted(seen.items(), key=lambda kv: kv[1].lower())]


@router.post("/consumption", response_model=Dict[str, Any])
async def record_consumption(
    body: ConsumptionUpdate,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """Record a playback heartbeat for the current user."""
    try:
        record = TrainingService.record_progress(
            db,
            user_email=current_user.email,
            media_id=body.media_id,
            position_seconds=body.position_seconds,
            total_seconds=body.total_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TrainingService.consumption_to_dict(record)


def _parse_range(range_header: Optional[str], size: int) -> Optional[tuple]:
    """Parse a single ``bytes=start-end`` range. Returns ``(start, end)`` or None."""
    if not range_header or not range_header.startswith("bytes="):
        return None
    spec = range_header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            # suffix range: last N bytes
            length = int(end_s)
            if length <= 0:
                return None
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start > end or start >= size:
        return None
    return start, min(end, size - 1)


@router.get("/media/{media_id}/stream")
async def stream_media(
    media_id: str,
    request: Request,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """Stream a media file with HTTP Range support (so video seeking works)."""
    media = TrainingService.get_media(db, media_id)
    if not media or not media.storage_path:
        raise HTTPException(status_code=404, detail="Media not found")

    storage = TrainingMediaStorage()
    size = media.size_bytes or storage.get_size(media.storage_path)
    if not size:
        raise HTTPException(status_code=404, detail="Media file unavailable")

    content_type = media.mime_type or "application/octet-stream"
    rng = _parse_range(request.headers.get("range"), size)

    if rng is None:
        start, end = 0, size - 1
        status_code = 200
    else:
        start, end = rng
        status_code = 206

    content_length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": f'inline; filename="{media.source_filename or media.title}"',
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    return StreamingResponse(
        storage.read_range(media.storage_path, start, end),
        status_code=status_code,
        media_type=content_type,
        headers=headers,
    )


# ----------------------------------------------------------------- admin: tracks

@router.get("/tracks", response_model=List[Dict[str, Any]])
async def admin_list_tracks(
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    return [
        TrainingService.track_to_dict(db, t, include_courses=True)
        for t in TrainingService.list_tracks(db, include_drafts=True)
    ]


@router.post("/tracks", response_model=Dict[str, Any])
async def admin_create_track(
    body: TrackCreate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    track = TrainingService.create_track(db, created_by=current_user.email, **body.model_dump())
    return TrainingService.track_to_dict(db, track, include_courses=True)


@router.patch("/tracks/{track_id}", response_model=Dict[str, Any])
async def admin_update_track(
    track_id: str,
    body: TrackUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    try:
        track = TrainingService.update_track(db, track_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TrainingService.track_to_dict(db, track, include_courses=True)


@router.delete("/tracks/{track_id}", response_model=Dict[str, Any])
async def admin_delete_track(
    track_id: str,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    try:
        storage_paths = TrainingService.delete_track(db, track_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    storage = TrainingMediaStorage()
    for path in storage_paths:
        storage.delete_media(path)
    return {"success": True}


# --------------------------------------------------------------- admin: courses

@router.post("/tracks/{track_id}/courses", response_model=Dict[str, Any])
async def admin_create_course(
    track_id: str,
    body: CourseCreate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    try:
        course = TrainingService.create_course(
            db, track_id=track_id, created_by=current_user.email, **body.model_dump()
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TrainingService.course_to_dict(db, course, include_media=True)


@router.patch("/courses/{course_id}", response_model=Dict[str, Any])
async def admin_update_course(
    course_id: str,
    body: CourseUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    try:
        course = TrainingService.update_course(db, course_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TrainingService.course_to_dict(db, course, include_media=True)


@router.delete("/courses/{course_id}", response_model=Dict[str, Any])
async def admin_delete_course(
    course_id: str,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    try:
        storage_paths = TrainingService.delete_course(db, course_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    storage = TrainingMediaStorage()
    for path in storage_paths:
        storage.delete_media(path)
    return {"success": True}


# ---------------------------------------------------------------- admin: media

@router.post("/courses/{course_id}/media", response_model=Dict[str, Any])
async def admin_upload_media(
    course_id: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    kind: str = Form("video"),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    """Upload a media/doc file for a course (bytes -> UC Volume, metadata -> DB)."""
    if not TrainingService.get_course(db, course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    content = await file.read()
    max_bytes = settings.TRAINING_MAX_UPLOAD_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.TRAINING_MAX_UPLOAD_MB} MB limit",
        )

    storage = TrainingMediaStorage()
    media_id = TrainingService.new_media_id()
    try:
        storage_path, size = storage.store_media(media_id, file.filename or "media", content)
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to store training media: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to store media: {e}")

    media = TrainingService.create_media(
        db,
        media_id=media_id,
        course_id=course_id,
        title=title or (file.filename or "Untitled"),
        kind=kind,
        source_filename=file.filename,
        storage_path=storage_path,
        mime_type=file.content_type,
        size_bytes=size,
        created_by=current_user.email,
    )
    return TrainingService.media_to_dict(media)


@router.patch("/media/{media_id}", response_model=Dict[str, Any])
async def admin_update_media(
    media_id: str,
    body: MediaUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    try:
        media = TrainingService.update_media(db, media_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TrainingService.media_to_dict(media)


@router.delete("/media/{media_id}", response_model=Dict[str, Any])
async def admin_delete_media(
    media_id: str,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    try:
        storage_path = TrainingService.delete_media(db, media_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    TrainingMediaStorage().delete_media(storage_path)
    return {"success": True}


# ------------------------------------------------------------- catalog + admin

@router.post("/catalog/sync", response_model=Dict[str, Any])
async def admin_sync_catalog(
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    """Scrape the public Databricks training catalog and import its courses."""
    result = await scrape_catalog(settings.TRAINING_CATALOG_URL)
    courses = result.get("courses", []) if result.get("ok") else []
    stats = TrainingService.sync_catalog_courses(db, courses, created_by=current_user.email)
    return {
        "ok": bool(result.get("ok")),
        "note": result.get("note"),
        "found": len(courses),
        "stats": stats,
    }


@router.get("/analytics/consumption", response_model=List[Dict[str, Any]])
async def admin_consumption_analytics(
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    db: Session = Depends(deps.get_db),
):
    return TrainingService.course_consumption_summary(db)


@router.post("/upload", response_model=Dict[str, Any])
async def upload_training_data(
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """Upload Academy training completion CSV (admin only)."""
    if not current_user.has_role("platform_admin"):
        raise HTTPException(status_code=403, detail="Not authorized")

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    try:
        content = await file.read()
        content_str = content.decode("utf-8")
        provider = TrainingProvider(db)
        stats = provider.ingest_training_csv(content_str)
        return {"message": "Training data processed successfully", "stats": stats}
    except Exception as e:
        logger.error(f"Error processing upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
