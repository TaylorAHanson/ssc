"""One-time, idempotent seed of training tracks from the legacy JSON file.

The Training page used to be driven by ``app/content/training.json`` (a list of
persona objects whose section keys mapped to course lists). The lightweight LMS
now stores tracks/courses in the DB. This seeder imports that JSON into DB rows
on first boot so existing tracks survive the migration, then becomes a no-op
once any track exists.
"""
import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.agents.content_registry import get_content
from app.db.training import TrainingTrackModel
from app.services.training_service import TrainingService

logger = logging.getLogger(__name__)

# Order matters: the old UI rendered these section buckets in this order.
_SECTION_KEYS = ["fundamentals", "optionalLanguages", "associate", "professional"]


def seed_training_from_json(db: Session) -> None:
    """Import legacy ``training.json`` tracks if the DB has none yet."""
    if db.query(TrainingTrackModel).count() > 0:
        logger.info("Training tracks already present; skipping JSON seed.")
        return

    data: List[Dict[str, Any]] = get_content("training.json") or []
    if not isinstance(data, list) or not data:
        logger.info("No legacy training.json content to seed.")
        return

    seeded_tracks = 0
    seeded_courses = 0
    for order, persona_obj in enumerate(data):
        if not isinstance(persona_obj, dict):
            continue
        persona = persona_obj.get("persona") or f"Track {order + 1}"
        track = TrainingService.create_track(
            db,
            name=persona,
            persona=persona,
            description=None,
            source="catalog",
            status="published",
            sort_order=order,
            created_by="system",
        )
        seeded_tracks += 1

        course_order = 0
        # Iterate known sections first (preserve ordering), then any extras.
        section_keys = _SECTION_KEYS + [
            k for k in persona_obj.keys()
            if k not in _SECTION_KEYS and k != "persona" and isinstance(persona_obj.get(k), list)
        ]
        for section in section_keys:
            courses = persona_obj.get(section)
            if not isinstance(courses, list):
                continue
            for course in courses:
                if not isinstance(course, dict):
                    continue
                code = course.get("id")
                TrainingService.create_course(
                    db,
                    track_id=track.id,
                    title=course.get("name") or code or "Untitled course",
                    course_code=code,
                    external_url=course.get("url"),
                    section=section,
                    course_type=course.get("type"),
                    duration=course.get("duration"),
                    unlocks=course.get("unlocks"),
                    source="catalog",
                    status="published",
                    sort_order=course_order,
                    created_by="system",
                )
                course_order += 1
                seeded_courses += 1

    logger.info(
        "Seeded %d training tracks and %d courses from training.json",
        seeded_tracks, seeded_courses,
    )
