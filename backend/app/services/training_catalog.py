"""Databricks Academy catalog sync, course linking and match suggestions.

The sync copies the Academy catalog (see ``providers.training.academy``) into
``training_catalog_courses``. A training course linked to a catalog course
(``academy_course_id``) then follows it: every sync refreshes its code, link,
type and duration, and its title too unless an admin has changed it. When
Academy replaces a course with a new version, the old entry drops out of the
catalog; linked courses move to the newest active version of the same course
(same acronym, language and delivery) automatically. If there is none, the
course keeps its last details and is reported as retired.

Unlinked courses get suggestions: a catalog course with the same code, the
same acronym, or a closely matching title. Linking is always an admin's call.
"""
import asyncio
import difflib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from croniter import CroniterBadCronError, croniter
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_lakebase_session
from app.db.training import (
    TrainingCatalogCourseModel,
    TrainingCatalogSyncModel,
    TrainingCourseModel,
    TrainingTrackModel,
)
from app.providers.training import academy

logger = logging.getLogger(__name__)

TYPE_LABELS = {"elearning": "Self-paced", "classroom": "Instructor-led"}
_TITLE_MATCH_MIN = 0.82
_VERSION_RE = re.compile(r"-V(\d+)\b", re.IGNORECASE)

_sync_lock = asyncio.Lock()
_next_sync_time: Optional[datetime] = None
_boot_checked = False


def _now() -> datetime:
    return datetime.utcnow()


# ------------------------------------------------------------------ matching

def _version(code: Optional[str]) -> int:
    found = _VERSION_RE.findall(code or "")
    return max((int(v) for v in found), default=0)


def _for_everyone(code: Optional[str]) -> bool:
    return "-ALL-" in (code or "").upper()


def _variant_rank(row: TrainingCatalogCourseModel, course_type: Optional[str]) -> Tuple:
    """Sort key: same delivery first, then free, open to everyone, newest version."""
    return (
        course_type is not None and row.course_type != course_type,
        not row.is_free,
        not _for_everyone(row.code),
        -_version(row.code),
        row.id,
    )


def best_variant(rows: Iterable[TrainingCatalogCourseModel], stem: Optional[str],
                 language: Optional[str] = None,
                 course_type: Optional[str] = None) -> Optional[TrainingCatalogCourseModel]:
    """The active catalog course to use for ``stem``, preferring ``course_type`` and ``language``."""
    if not stem:
        return None
    pool = [r for r in rows if r.active and r.stem == stem]
    if language:
        pool = [r for r in pool if r.language == language] or pool
    if not pool:
        return None
    return min(pool, key=lambda r: _variant_rank(r, course_type))


def _norm_title(title: str) -> str:
    t = (title or "").lower().replace("&", " and ")
    t = re.sub(r"\bw/\s*", "with ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _title_match(title: str, rows: List[TrainingCatalogCourseModel]) -> Optional[Tuple[TrainingCatalogCourseModel, float]]:
    target = _norm_title(title)
    if len(target) < 8:
        return None
    scored: List[Tuple[float, TrainingCatalogCourseModel]] = []
    for r in rows:
        if not r.active:
            continue
        score = difflib.SequenceMatcher(None, target, _norm_title(r.name)).ratio()
        if score >= _TITLE_MATCH_MIN:
            scored.append((score, r))
    if not scored:
        return None
    top = max(s for s, _ in scored)
    # Among equally good title matches (one per delivery), pick the usual variant.
    best = min((r for s, r in scored if s >= top - 0.01), key=lambda r: _variant_rank(r, "elearning"))
    return best, top


def suggest_for(course: TrainingCourseModel,
                rows: List[TrainingCatalogCourseModel]) -> Optional[Dict[str, Any]]:
    """A catalog course this training course most likely is, with the reason."""
    active = [r for r in rows if r.active]
    code = (course.course_code or "").strip()
    if code:
        exact = next((r for r in active if r.code == code), None)
        if exact:
            return {"match": exact, "reason": "same_code"}
        stem = academy.course_stem(code)
        variant = best_variant(active, stem, language="english", course_type="elearning")
        if variant:
            return {"match": variant, "reason": "newer_version"}
    by_title = _title_match(course.title or "", active)
    if by_title:
        row, score = by_title
        return {"match": row, "reason": "similar_title", "score": round(score, 2)}
    return None


# ------------------------------------------------------------------ linking

def catalog_to_dict(row: Optional[TrainingCatalogCourseModel]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "course_type": row.course_type,
        "type_label": TYPE_LABELS.get(row.course_type or "", row.course_type),
        "duration_seconds": row.duration_seconds,
        "duration": academy.format_duration(row.duration_seconds),
        "language": row.language,
        "is_free": bool(row.is_free),
        "url": row.url,
        "active": bool(row.active),
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


def apply_catalog(course: TrainingCourseModel, row: TrainingCatalogCourseModel) -> bool:
    """Copy the catalog's code, link, type and duration onto ``course``. True if anything changed."""
    updates = {
        "course_code": row.code or course.course_code,
        "external_url": row.url or course.external_url,
        "course_type": TYPE_LABELS.get(row.course_type or "", course.course_type),
        "duration": academy.format_duration(row.duration_seconds) or course.duration,
    }
    changed = False
    for key, value in updates.items():
        if getattr(course, key) != value:
            setattr(course, key, value)
            changed = True
    return changed


def link_course(db: Session, course: TrainingCourseModel, academy_course_id: Optional[int],
                adopt_title: bool = True) -> TrainingCourseModel:
    """Link ``course`` to a catalog course and copy its details, or unlink it (None).

    ``adopt_title`` also replaces the course title with the catalog's; the
    course editor passes False because the admin has just typed the title.
    """
    if academy_course_id is None:
        course.academy_course_id = None
    else:
        row = db.get(TrainingCatalogCourseModel, academy_course_id)
        if row is None:
            raise ValueError("That Academy course isn't in the synced catalog.")
        course.academy_course_id = row.id
        if adopt_title or not course.title:
            course.title = row.name
        if not course.description and row.description:
            course.description = row.description
        apply_catalog(course, row)
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


def catalog_rows_by_id(db: Session, ids: Iterable[Optional[int]]) -> Dict[int, TrainingCatalogCourseModel]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = db.query(TrainingCatalogCourseModel).filter(TrainingCatalogCourseModel.id.in_(wanted)).all()
    return {r.id: r for r in rows}


# ------------------------------------------------------------------ sync

def save_catalog(db: Session, courses: List[Dict[str, Any]]) -> Dict[str, int]:
    """Upsert a successful fetch, retire what's missing, and refresh linked courses."""
    now = _now()
    stats = {"courses": len(courses), "added": 0, "retired": 0, "linked_updated": 0, "relinked": 0}
    existing = {r.id: r for r in db.query(TrainingCatalogCourseModel).all()}
    renamed: Dict[int, str] = {}
    seen = set()
    for c in courses:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        row = existing.get(c["id"])
        if row is None:
            row = TrainingCatalogCourseModel(id=c["id"], first_seen_at=now)
            db.add(row)
            existing[c["id"]] = row
            stats["added"] += 1
        elif row.name != c["name"]:
            renamed[row.id] = row.name
        row.code = c["code"]
        row.stem = c["stem"]
        row.name = c["name"]
        row.description = c["description"]
        row.course_type = c["course_type"]
        row.duration_seconds = c["duration_seconds"]
        row.language = c["language"]
        row.is_free = c["is_free"]
        row.category = c["category"]
        row.slug = c["slug"]
        row.url = c["url"]
        row.image_url = c["image_url"]
        row.academy_updated_at = c["updated_at"]
        row.active = True
        row.last_seen_at = now
    for row in existing.values():
        if row.id not in seen and row.active:
            row.active = False
            stats["retired"] += 1
    db.flush()

    rows = list(existing.values())
    by_code = {r.code: r for r in rows if r.active and r.code}
    for course in db.query(TrainingCourseModel).filter(TrainingCourseModel.academy_course_id.is_(None)).all():
        exact = by_code.get((course.course_code or "").strip())
        if exact is not None:
            course.academy_course_id = exact.id
            apply_catalog(course, exact)
            stats["linked_updated"] += 1
    db.flush()

    linked = db.query(TrainingCourseModel).filter(TrainingCourseModel.academy_course_id.isnot(None)).all()
    for course in linked:
        changed = False
        row = existing.get(course.academy_course_id)
        if row is None or not row.active:
            replacement = best_variant(
                rows,
                row.stem if row else academy.course_stem(course.course_code),
                language=row.language if row else None,
                course_type=row.course_type if row else None,
            )
            if replacement is None:
                continue
            logger.info("Training course %s now follows Academy course %s (%s)",
                        course.id, replacement.id, replacement.code)
            course.academy_course_id = replacement.id
            stats["relinked"] += 1
            if row is not None and course.title == row.name:
                course.title = replacement.name
            row = replacement
        elif course.title == renamed.get(row.id):
            course.title = row.name
            changed = True
        if apply_catalog(course, row) or changed:
            stats["linked_updated"] += 1
    db.commit()
    return stats


def _start_sync_record(trigger: str) -> int:
    db = get_lakebase_session()
    try:
        record = TrainingCatalogSyncModel(started_at=_now(), trigger=trigger, ok=False)
        db.add(record)
        db.commit()
        return record.id
    finally:
        db.close()


def _finish_sync(record_id: int, courses: Optional[List[Dict[str, Any]]], error: str) -> Dict[str, Any]:
    db = get_lakebase_session()
    try:
        stats: Dict[str, int] = {}
        if courses is not None:
            try:
                stats = save_catalog(db, courses)
            except Exception as e:  # noqa: BLE001 - recorded on the sync row instead
                db.rollback()
                logger.error("Academy catalog: saving the sync failed: %s", e, exc_info=True)
                error = f"Saving the catalog failed: {e}"
        record = db.get(TrainingCatalogSyncModel, record_id)
        record.finished_at = _now()
        record.ok = not error
        record.error = error or None
        for key, value in stats.items():
            setattr(record, key, value)
        db.commit()
        return sync_record_to_dict(record)
    finally:
        db.close()


async def run_sync(trigger: str = "manual") -> Dict[str, Any]:
    """Fetch the catalog and save it. Never raises; failures come back on the record."""
    async with _sync_lock:
        record_id = await asyncio.to_thread(_start_sync_record, trigger)
        result = await academy.fetch_catalog(settings.TRAINING_ACADEMY_URL, settings.TRAINING_ACADEMY_LANGUAGES)
        courses = result["courses"] if result.get("ok") else None
        record = await asyncio.to_thread(_finish_sync, record_id, courses, result.get("error") or "")
    if record["ok"]:
        logger.info("Academy catalog sync (%s): %s", trigger, {k: record[k] for k in (
            "courses", "added", "retired", "linked_updated", "relinked")})
    else:
        logger.warning("Academy catalog sync (%s) failed: %s", trigger, record["error"])
    return record


def _catalog_is_empty() -> bool:
    db = get_lakebase_session()
    try:
        return db.query(TrainingCatalogCourseModel.id).first() is None
    finally:
        db.close()


async def academy_sync_task() -> None:
    """Poller hook: sync on TRAINING_ACADEMY_SYNC_CRON, and once at boot if the catalog is empty."""
    global _next_sync_time, _boot_checked
    if _sync_lock.locked():
        return
    now = datetime.now(timezone.utc)
    cron_expr = (getattr(settings, "TRAINING_ACADEMY_SYNC_CRON", "") or "").strip()
    trigger = "schedule"
    if not _boot_checked:
        _boot_checked = True
        if cron_expr and await asyncio.to_thread(_catalog_is_empty):
            trigger = "boot"
    if trigger != "boot":
        if not cron_expr:
            return
        if _next_sync_time is None:
            try:
                _next_sync_time = croniter(cron_expr, now).get_next(datetime)
            except (CroniterBadCronError, ValueError):
                logger.error("Invalid TRAINING_ACADEMY_SYNC_CRON expression: %s", cron_expr)
                return
        if now < _next_sync_time:
            return
    if cron_expr:
        try:
            _next_sync_time = croniter(cron_expr, now).get_next(datetime)
        except (CroniterBadCronError, ValueError):
            pass
    await run_sync(trigger)


# ------------------------------------------------------------------ admin views

def sync_record_to_dict(record: Optional[TrainingCatalogSyncModel]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    return {
        "id": record.id,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "ok": bool(record.ok),
        "trigger": record.trigger,
        "error": record.error,
        "courses": record.courses or 0,
        "added": record.added or 0,
        "retired": record.retired or 0,
        "linked_updated": record.linked_updated or 0,
        "relinked": record.relinked or 0,
    }


def search(db: Session, query: str = "", limit: int = 25) -> List[Dict[str, Any]]:
    """Active catalog courses whose name or code contains every word of ``query``."""
    q = db.query(TrainingCatalogCourseModel).filter(TrainingCatalogCourseModel.active.is_(True))
    for word in (query or "").split()[:6]:
        like = f"%{word}%"
        q = q.filter(or_(TrainingCatalogCourseModel.name.ilike(like),
                         TrainingCatalogCourseModel.code.ilike(like)))
    rows = q.all()
    rows.sort(key=lambda r: (r.name.lower(), _variant_rank(r, "elearning")))
    return [catalog_to_dict(r) for r in rows[: max(1, min(limit, 100))]]


def status(db: Session) -> Dict[str, Any]:
    """Last sync, catalog size, and how many training courses need attention."""
    last = db.query(TrainingCatalogSyncModel).order_by(TrainingCatalogSyncModel.id.desc()).first()
    last_ok = (
        db.query(TrainingCatalogSyncModel)
        .filter(TrainingCatalogSyncModel.ok.is_(True))
        .order_by(TrainingCatalogSyncModel.id.desc())
        .first()
    )
    active = db.query(TrainingCatalogCourseModel).filter(TrainingCatalogCourseModel.active.is_(True)).count()
    courses = db.query(TrainingCourseModel).all()
    rows = catalog_rows_by_id(db, (c.academy_course_id for c in courses))
    linked = [c for c in courses if c.academy_course_id is not None]
    retired = [c for c in linked if not (rows.get(c.academy_course_id) and rows[c.academy_course_id].active)]
    return {
        "academy_url": settings.TRAINING_ACADEMY_URL,
        "sync_cron": settings.TRAINING_ACADEMY_SYNC_CRON,
        "catalog_courses": active,
        "last_sync": sync_record_to_dict(last),
        "last_successful_sync": sync_record_to_dict(last_ok),
        "syncing": _sync_lock.locked(),
        "courses_total": len(courses),
        "courses_linked": len(linked),
        "courses_retired": len(retired),
        "courses_unlinked": len(courses) - len(linked),
    }


def suggestions(db: Session) -> Dict[str, Any]:
    """Unlinked or retired courses, each with the catalog course it most likely is."""
    rows = db.query(TrainingCatalogCourseModel).all()
    by_id = {r.id: r for r in rows}
    track_names = {t.id: t.name for t in db.query(TrainingTrackModel.id, TrainingTrackModel.name).all()}
    matched: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for course in db.query(TrainingCourseModel).order_by(TrainingCourseModel.title).all():
        current = by_id.get(course.academy_course_id) if course.academy_course_id is not None else None
        if current is not None and current.active:
            continue
        entry = {
            "course_id": course.id,
            "course_title": course.title,
            "course_code": course.course_code,
            "track_id": course.track_id,
            "track_name": track_names.get(course.track_id),
            "retired": current is not None,
        }
        hit = suggest_for(course, rows)
        if hit:
            matched.append({**entry, "reason": hit["reason"], "score": hit.get("score"),
                            "match": catalog_to_dict(hit["match"])})
        else:
            unmatched.append(entry)
    return {"suggestions": matched, "unmatched": unmatched}
