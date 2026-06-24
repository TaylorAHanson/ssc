"""Training / lightweight-LMS service.

Business logic for admin-authored learning tracks + courses, the media that
backs each course (bytes on a UC Volume), per-learner consumption tracking, and
the catalog-scrape import. Kept separate from the API layer so the same logic
is reusable by tools/jobs.

Consumption → completion: when a learner watches every video in a course past
``TRAINING_COMPLETION_THRESHOLD`` the course is considered consumed; if the
course has a ``course_code`` we also write a ``TrainingCompletionModel`` row
(``source="in_app"``) so in-app consumption satisfies workflow training gates,
exactly like an Academy CSV completion.
"""
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.training import (
    TrainingCompletionModel,
    TrainingConsumptionModel,
    TrainingCourseModel,
    TrainingMediaModel,
    TrainingTrackModel,
)

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")
    return slug or "track"


class TrainingService:
    """CRUD + consumption + catalog sync for the training LMS."""

    # ------------------------------------------------------------------- tracks

    @staticmethod
    def _unique_slug(db: Session, name: str, exclude_id: Optional[str] = None) -> str:
        base = _slugify(name)
        slug = base
        suffix = 2
        while True:
            q = db.query(TrainingTrackModel).filter(TrainingTrackModel.slug == slug)
            if exclude_id:
                q = q.filter(TrainingTrackModel.id != exclude_id)
            if not q.first():
                return slug
            slug = f"{base}-{suffix}"
            suffix += 1

    @staticmethod
    def create_track(db: Session, *, name: str, description: Optional[str] = None,
                     persona: Optional[str] = None, icon: Optional[str] = None,
                     source: str = "custom", status: str = "published",
                     sort_order: Optional[int] = None,
                     created_by: Optional[str] = None) -> TrainingTrackModel:
        if sort_order is None:
            sort_order = (
                db.query(TrainingTrackModel).count()
            )
        track = TrainingTrackModel(
            id=str(uuid.uuid4()),
            slug=TrainingService._unique_slug(db, name),
            name=name,
            description=description,
            persona=persona or name,
            icon=icon,
            source=source or "custom",
            status=status or "published",
            sort_order=sort_order,
            created_by=created_by,
        )
        db.add(track)
        db.commit()
        db.refresh(track)
        return track

    @staticmethod
    def list_tracks(db: Session, include_drafts: bool = True) -> List[TrainingTrackModel]:
        q = db.query(TrainingTrackModel)
        if not include_drafts:
            q = q.filter(TrainingTrackModel.status == "published")
        return q.order_by(TrainingTrackModel.sort_order, TrainingTrackModel.name).all()

    @staticmethod
    def get_track(db: Session, track_id: str) -> Optional[TrainingTrackModel]:
        return db.query(TrainingTrackModel).filter(TrainingTrackModel.id == track_id).first()

    @staticmethod
    def get_track_by_slug(db: Session, slug: str) -> Optional[TrainingTrackModel]:
        return db.query(TrainingTrackModel).filter(TrainingTrackModel.slug == slug).first()

    @staticmethod
    def update_track(db: Session, track_id: str, **fields) -> TrainingTrackModel:
        track = TrainingService.get_track(db, track_id)
        if not track:
            raise ValueError("Track not found")
        if fields.get("name") and fields["name"] != track.name:
            track.name = fields["name"]
        for key in ("description", "persona", "icon", "source", "status", "sort_order"):
            if key in fields and fields[key] is not None:
                setattr(track, key, fields[key])
        db.add(track)
        db.commit()
        db.refresh(track)
        return track

    @staticmethod
    def delete_track(db: Session, track_id: str) -> List[str]:
        """Delete a track and all its courses/media/consumption.

        Returns the list of UC Volume storage paths to clean up (the API layer
        deletes the bytes so the service stays storage-agnostic).
        """
        track = TrainingService.get_track(db, track_id)
        if not track:
            raise ValueError("Track not found")
        course_ids = [c.id for c in db.query(TrainingCourseModel.id)
                      .filter(TrainingCourseModel.track_id == track_id).all()]
        storage_paths: List[str] = []
        if course_ids:
            media = db.query(TrainingMediaModel).filter(
                TrainingMediaModel.course_id.in_(course_ids)
            ).all()
            storage_paths = [m.storage_path for m in media if m.storage_path]
            db.query(TrainingConsumptionModel).filter(
                TrainingConsumptionModel.course_id.in_(course_ids)
            ).delete(synchronize_session=False)
            db.query(TrainingMediaModel).filter(
                TrainingMediaModel.course_id.in_(course_ids)
            ).delete(synchronize_session=False)
            db.query(TrainingCourseModel).filter(
                TrainingCourseModel.track_id == track_id
            ).delete(synchronize_session=False)
        db.query(TrainingTrackModel).filter(TrainingTrackModel.id == track_id).delete(
            synchronize_session=False
        )
        db.commit()
        return storage_paths

    # ------------------------------------------------------------------ courses

    @staticmethod
    def create_course(db: Session, *, track_id: str, title: str,
                      description: Optional[str] = None, course_code: Optional[str] = None,
                      external_url: Optional[str] = None, section: Optional[str] = None,
                      course_type: Optional[str] = None, duration: Optional[str] = None,
                      unlocks: Optional[str] = None, source: str = "custom",
                      status: str = "published", sort_order: Optional[int] = None,
                      created_by: Optional[str] = None) -> TrainingCourseModel:
        if not TrainingService.get_track(db, track_id):
            raise ValueError("Track not found")
        if sort_order is None:
            sort_order = (
                db.query(TrainingCourseModel)
                .filter(TrainingCourseModel.track_id == track_id)
                .count()
            )
        course = TrainingCourseModel(
            id=str(uuid.uuid4()),
            track_id=track_id,
            title=title,
            description=description,
            course_code=course_code,
            external_url=external_url,
            section=section,
            course_type=course_type,
            duration=duration,
            unlocks=unlocks,
            source=source or "custom",
            status=status or "published",
            sort_order=sort_order,
            created_by=created_by,
        )
        db.add(course)
        db.commit()
        db.refresh(course)
        return course

    @staticmethod
    def list_courses(db: Session, track_id: Optional[str] = None) -> List[TrainingCourseModel]:
        q = db.query(TrainingCourseModel)
        if track_id:
            q = q.filter(TrainingCourseModel.track_id == track_id)
        return q.order_by(TrainingCourseModel.sort_order, TrainingCourseModel.title).all()

    @staticmethod
    def get_course(db: Session, course_id: str) -> Optional[TrainingCourseModel]:
        return db.query(TrainingCourseModel).filter(TrainingCourseModel.id == course_id).first()

    @staticmethod
    def update_course(db: Session, course_id: str, **fields) -> TrainingCourseModel:
        course = TrainingService.get_course(db, course_id)
        if not course:
            raise ValueError("Course not found")
        for key in ("title", "description", "course_code", "external_url", "section",
                    "course_type", "duration", "unlocks", "source", "status",
                    "sort_order", "track_id"):
            if key in fields and fields[key] is not None:
                setattr(course, key, fields[key])
        if fields.get("track_id") and not TrainingService.get_track(db, fields["track_id"]):
            raise ValueError("Track not found")
        db.add(course)
        db.commit()
        db.refresh(course)
        return course

    @staticmethod
    def delete_course(db: Session, course_id: str) -> List[str]:
        course = TrainingService.get_course(db, course_id)
        if not course:
            raise ValueError("Course not found")
        media = db.query(TrainingMediaModel).filter(
            TrainingMediaModel.course_id == course_id
        ).all()
        storage_paths = [m.storage_path for m in media if m.storage_path]
        db.query(TrainingConsumptionModel).filter(
            TrainingConsumptionModel.course_id == course_id
        ).delete(synchronize_session=False)
        db.query(TrainingMediaModel).filter(
            TrainingMediaModel.course_id == course_id
        ).delete(synchronize_session=False)
        db.query(TrainingCourseModel).filter(
            TrainingCourseModel.id == course_id
        ).delete(synchronize_session=False)
        db.commit()
        return storage_paths

    # ------------------------------------------------------------------- media

    @staticmethod
    def create_media(db: Session, *, course_id: str, title: str, kind: str,
                     source_filename: Optional[str], storage_path: Optional[str],
                     mime_type: Optional[str] = None, size_bytes: Optional[int] = None,
                     duration_seconds: Optional[float] = None,
                     sort_order: Optional[int] = None, media_id: Optional[str] = None,
                     created_by: Optional[str] = None) -> TrainingMediaModel:
        if not TrainingService.get_course(db, course_id):
            raise ValueError("Course not found")
        if sort_order is None:
            sort_order = (
                db.query(TrainingMediaModel)
                .filter(TrainingMediaModel.course_id == course_id)
                .count()
            )
        media = TrainingMediaModel(
            id=media_id or str(uuid.uuid4()),
            course_id=course_id,
            title=title,
            kind=kind or "video",
            source_filename=source_filename,
            storage_path=storage_path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
            sort_order=sort_order,
            created_by=created_by,
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return media

    @staticmethod
    def new_media_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def list_media(db: Session, course_id: str) -> List[TrainingMediaModel]:
        return (
            db.query(TrainingMediaModel)
            .filter(TrainingMediaModel.course_id == course_id)
            .order_by(TrainingMediaModel.sort_order, TrainingMediaModel.title)
            .all()
        )

    @staticmethod
    def get_media(db: Session, media_id: str) -> Optional[TrainingMediaModel]:
        return db.query(TrainingMediaModel).filter(TrainingMediaModel.id == media_id).first()

    @staticmethod
    def update_media(db: Session, media_id: str, **fields) -> TrainingMediaModel:
        media = TrainingService.get_media(db, media_id)
        if not media:
            raise ValueError("Media not found")
        for key in ("title", "kind", "sort_order", "duration_seconds"):
            if key in fields and fields[key] is not None:
                setattr(media, key, fields[key])
        db.add(media)
        db.commit()
        db.refresh(media)
        return media

    @staticmethod
    def delete_media(db: Session, media_id: str) -> Optional[str]:
        media = TrainingService.get_media(db, media_id)
        if not media:
            raise ValueError("Media not found")
        storage_path = media.storage_path
        db.query(TrainingConsumptionModel).filter(
            TrainingConsumptionModel.media_id == media_id
        ).delete(synchronize_session=False)
        db.query(TrainingMediaModel).filter(
            TrainingMediaModel.id == media_id
        ).delete(synchronize_session=False)
        db.commit()
        return storage_path

    # -------------------------------------------------------------- consumption

    @staticmethod
    def record_progress(db: Session, *, user_email: str, media_id: str,
                        position_seconds: float,
                        total_seconds: Optional[float] = None) -> TrainingConsumptionModel:
        """Upsert a learner's progress on a media item from a playback heartbeat.

        ``position_seconds`` is the furthest point reached; ``total_seconds`` is
        the player-reported duration (used as a fallback when the media row has
        no authoritative duration). Crossing the completion threshold flips the
        record to completed and may finalize the course.
        """
        media = TrainingService.get_media(db, media_id)
        if not media:
            raise ValueError("Media not found")

        now = datetime.utcnow()
        duration = media.duration_seconds or total_seconds or 0.0
        # Persist a player-reported duration if we didn't have one (best-effort).
        if not media.duration_seconds and total_seconds:
            media.duration_seconds = float(total_seconds)
            db.add(media)

        record = (
            db.query(TrainingConsumptionModel)
            .filter(
                TrainingConsumptionModel.user_email == user_email,
                TrainingConsumptionModel.media_id == media_id,
            )
            .first()
        )
        if not record:
            record = TrainingConsumptionModel(
                id=str(uuid.uuid4()),
                user_email=user_email,
                media_id=media_id,
                course_id=media.course_id,
                first_viewed_at=now,
                view_count=0,
            )
            db.add(record)

        record.position_seconds = max(record.position_seconds or 0.0, float(position_seconds))
        record.last_viewed_at = now
        record.view_count = (record.view_count or 0) + 1
        if duration > 0:
            pct = min(1.0, record.position_seconds / duration)
            record.percent_complete = max(record.percent_complete or 0.0, pct)
        if (
            not record.completed
            and record.percent_complete >= settings.TRAINING_COMPLETION_THRESHOLD
        ):
            record.completed = True
            record.completed_at = now

        db.commit()
        db.refresh(record)

        if record.completed:
            TrainingService._maybe_complete_course(db, user_email, media.course_id)

        return record

    @staticmethod
    def _maybe_complete_course(db: Session, user_email: str, course_id: str) -> None:
        """If every video in the course is consumed, record a course completion.

        Mirrors an Academy completion (keyed by ``course_code``) so it satisfies
        the same workflow training gates. No-op for courses without a
        ``course_code`` (nothing to match a gate against).
        """
        course = TrainingService.get_course(db, course_id)
        if not course or not course.course_code:
            return
        videos = (
            db.query(TrainingMediaModel)
            .filter(
                TrainingMediaModel.course_id == course_id,
                TrainingMediaModel.kind == "video",
            )
            .all()
        )
        if not videos:
            return
        completed_media = {
            r.media_id
            for r in db.query(TrainingConsumptionModel.media_id)
            .filter(
                TrainingConsumptionModel.user_email == user_email,
                TrainingConsumptionModel.course_id == course_id,
                TrainingConsumptionModel.completed.is_(True),
            )
            .all()
        }
        if not all(v.id in completed_media for v in videos):
            return

        existing = (
            db.query(TrainingCompletionModel)
            .filter(
                TrainingCompletionModel.user_email == user_email,
                TrainingCompletionModel.course_code == course.course_code,
            )
            .first()
        )
        if existing:
            if existing.status != "completed":
                existing.status = "completed"
                existing.completed_at = datetime.utcnow()
                db.add(existing)
                db.commit()
            return
        db.add(TrainingCompletionModel(
            user_email=user_email,
            course_name=course.title,
            course_code=course.course_code,
            completed_at=datetime.utcnow(),
            status="completed",
            source="in_app",
        ))
        db.commit()
        logger.info(
            "In-app consumption completed course %s for %s", course.course_code, user_email
        )

    @staticmethod
    def consumption_for_user(db: Session, user_email: str,
                             course_ids: List[str]) -> Dict[str, TrainingConsumptionModel]:
        """Return ``{media_id: consumption}`` for a user across the given courses."""
        if not course_ids:
            return {}
        rows = (
            db.query(TrainingConsumptionModel)
            .filter(
                TrainingConsumptionModel.user_email == user_email,
                TrainingConsumptionModel.course_id.in_(course_ids),
            )
            .all()
        )
        return {r.media_id: r for r in rows}

    # --------------------------------------------------------------- analytics

    @staticmethod
    def course_consumption_summary(db: Session) -> List[Dict[str, Any]]:
        """Per-course consumption rollup for the admin dashboard."""
        from sqlalchemy import func, Integer as SAInteger

        rows = (
            db.query(
                TrainingConsumptionModel.course_id,
                func.count(func.distinct(TrainingConsumptionModel.user_email)).label("learners"),
                func.avg(TrainingConsumptionModel.percent_complete).label("avg_pct"),
                func.sum(
                    func.cast(TrainingConsumptionModel.completed, SAInteger)
                ).label("completions"),
            )
            .group_by(TrainingConsumptionModel.course_id)
            .all()
        )
        # Map course_id -> title for readability.
        course_titles = {
            c.id: c.title for c in db.query(TrainingCourseModel.id, TrainingCourseModel.title).all()
        }
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "course_id": r.course_id,
                "course_title": course_titles.get(r.course_id, r.course_id),
                "learners": int(r.learners or 0),
                "avg_percent": round(float(r.avg_pct or 0.0), 3),
                "media_completions": int(r.completions or 0),
            })
        out.sort(key=lambda x: x["learners"], reverse=True)
        return out

    # ------------------------------------------------------------ catalog sync

    CATALOG_TRACK_NAME = "Databricks Training Catalog"

    @staticmethod
    def sync_catalog_courses(db: Session, scraped: List[Dict[str, str]],
                             created_by: Optional[str] = None) -> Dict[str, int]:
        """Upsert scraped ``{title, url}`` courses into the catalog track.

        Matches existing catalog courses by ``external_url`` so a re-sync
        updates titles rather than duplicating. Returns counts.
        """
        stats = {"added": 0, "updated": 0, "skipped": 0}
        if not scraped:
            return stats

        track = (
            db.query(TrainingTrackModel)
            .filter(TrainingTrackModel.name == TrainingService.CATALOG_TRACK_NAME)
            .first()
        )
        if not track:
            track = TrainingService.create_track(
                db,
                name=TrainingService.CATALOG_TRACK_NAME,
                description="Courses imported from the public Databricks training catalog.",
                persona="Catalog",
                source="catalog",
                status="published",
                created_by=created_by,
            )

        existing = {
            c.external_url: c
            for c in db.query(TrainingCourseModel)
            .filter(TrainingCourseModel.track_id == track.id)
            .all()
            if c.external_url
        }
        for item in scraped:
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not url or not title:
                stats["skipped"] += 1
                continue
            current = existing.get(url)
            if current:
                if current.title != title:
                    current.title = title
                    db.add(current)
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                TrainingService.create_course(
                    db,
                    track_id=track.id,
                    title=title,
                    external_url=url,
                    source="catalog",
                    status="published",
                    created_by=created_by,
                )
                stats["added"] += 1
        db.commit()
        return stats

    # ------------------------------------------------------------ serialization

    @staticmethod
    def track_to_dict(db: Session, track: TrainingTrackModel,
                      include_courses: bool = False) -> Dict[str, Any]:
        data = {
            "id": track.id,
            "slug": track.slug,
            "name": track.name,
            "description": track.description,
            "persona": track.persona,
            "icon": track.icon,
            "source": track.source,
            "sort_order": track.sort_order,
            "status": track.status,
            "created_by": track.created_by,
            "created_at": track.created_at.isoformat() if track.created_at else None,
            "updated_at": track.updated_at.isoformat() if track.updated_at else None,
        }
        data["course_count"] = (
            db.query(TrainingCourseModel)
            .filter(TrainingCourseModel.track_id == track.id)
            .count()
        )
        if include_courses:
            data["courses"] = [
                TrainingService.course_to_dict(db, c, include_media=True)
                for c in TrainingService.list_courses(db, track.id)
            ]
        return data

    @staticmethod
    def course_to_dict(db: Session, course: TrainingCourseModel,
                       include_media: bool = False) -> Dict[str, Any]:
        data = {
            "id": course.id,
            "track_id": course.track_id,
            "title": course.title,
            "description": course.description,
            "course_code": course.course_code,
            "external_url": course.external_url,
            "section": course.section,
            "course_type": course.course_type,
            "duration": course.duration,
            "unlocks": course.unlocks,
            "source": course.source,
            "sort_order": course.sort_order,
            "status": course.status,
            "created_at": course.created_at.isoformat() if course.created_at else None,
            "updated_at": course.updated_at.isoformat() if course.updated_at else None,
        }
        if include_media:
            data["media"] = [
                TrainingService.media_to_dict(m) for m in TrainingService.list_media(db, course.id)
            ]
        return data

    @staticmethod
    def media_to_dict(media: TrainingMediaModel) -> Dict[str, Any]:
        return {
            "id": media.id,
            "course_id": media.course_id,
            "title": media.title,
            "kind": media.kind,
            "source_filename": media.source_filename,
            "mime_type": media.mime_type,
            "size_bytes": media.size_bytes,
            "duration_seconds": media.duration_seconds,
            "sort_order": media.sort_order,
            "has_file": bool(media.storage_path),
            "created_at": media.created_at.isoformat() if media.created_at else None,
        }

    @staticmethod
    def consumption_to_dict(record: TrainingConsumptionModel) -> Dict[str, Any]:
        return {
            "media_id": record.media_id,
            "course_id": record.course_id,
            "position_seconds": record.position_seconds,
            "percent_complete": record.percent_complete,
            "completed": record.completed,
            "view_count": record.view_count,
            "last_viewed_at": record.last_viewed_at.isoformat() if record.last_viewed_at else None,
        }
