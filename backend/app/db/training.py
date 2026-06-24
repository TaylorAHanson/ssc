"""Training / lightweight-LMS database models.

Two halves live here:

* ``TrainingCompletionModel`` — the long-standing record of *Academy* course
  completions, ingested from a CSV export and used as the authority for
  workflow ``training`` gates (matched by ``course_code``).
* The lightweight LMS (``TrainingTrackModel`` / ``TrainingCourseModel`` /
  ``TrainingMediaModel`` / ``TrainingConsumptionModel``) — admin-authored
  learning tracks and courses, the media/docs that back each course (stored as
  bytes on a Unity Catalog Volume, *not* in the DB), and per-learner
  consumption of that material. This replaces the static ``training.json`` file
  that previously drove the Training page.

Media bytes never live in the database: a ``TrainingMediaModel`` row only
records the UC Volume ``storage_path`` (plus metadata); the actual file is read
back through the Files API and streamed with HTTP Range support.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.db.base import Base


class TrainingCompletionModel(Base):
    __tablename__ = "training_completions"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, index=True, nullable=False)
    course_name = Column(String, nullable=True)
    course_code = Column(String, index=True, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default="completed")
    # Provenance of the completion: "academy" (CSV import) or "in_app"
    # (learner finished the in-app media for a course). Lets analytics tell the
    # two apart while both still satisfy course-code-pinned training gates.
    source = Column(String, nullable=False, default="academy", server_default="academy")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Composite index for faster lookups and ensuring uniqueness if needed
    __table_args__ = (
        Index("idx_user_course", "user_email", "course_code"),
    )


class TrainingTrackModel(Base):
    """A learning track — the top-level grouping a learner picks (e.g. a persona
    path like "Data Engineer"). Replaces a top-level entry in training.json."""

    __tablename__ = "training_tracks"

    id = Column(String, primary_key=True, comment="Unique UUID for the track")
    slug = Column(String, nullable=False, unique=True, index=True, comment="URL-safe unique identifier")
    name = Column(String, nullable=False, comment="Human-readable track name")
    description = Column(Text, nullable=True, comment="What this track is for")
    # Free-form audience/persona label kept for back-compat with the old
    # persona-driven UI and the workflow course picker.
    persona = Column(String, nullable=True, index=True, comment="Audience/persona label")
    icon = Column(String, nullable=True, comment="Optional lucide icon name for the UI")
    # "custom" (admin authored) or "catalog" (imported from the public catalog scrape).
    source = Column(String, nullable=False, default="custom", index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="published", index=True, comment="draft | published")
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TrainingCourseModel(Base):
    """A course inside a track. Carries the ``course_code`` used to match
    completions/gates and an optional ``external_url`` deeplink into the public
    Databricks training catalog."""

    __tablename__ = "training_courses"

    id = Column(String, primary_key=True, comment="Unique UUID for the course")
    track_id = Column(String, ForeignKey("training_tracks.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    # Maps to TrainingCompletionModel.course_code; also what workflow training
    # gates pin. May be null for purely-internal custom courses.
    course_code = Column(String, nullable=True, index=True)
    # Deeplink to the public catalog course-detail page, when known. The UI
    # links the course title to this when present.
    external_url = Column(String, nullable=True)
    # Grouping/level within the track (e.g. "fundamentals", "associate"). Keeps
    # the sectioned layout the old UI had without hardcoding the buckets.
    section = Column(String, nullable=True)
    course_type = Column(String, nullable=True, comment="eLearning | SelfPaced | Certification | ...")
    duration = Column(String, nullable=True, comment="Human-readable duration, e.g. '3 hrs'")
    unlocks = Column(String, nullable=True, comment="What completing this course unlocks (display only)")
    source = Column(String, nullable=False, default="custom", index=True, comment="custom | catalog")
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="published", index=True, comment="draft | published")
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TrainingMediaModel(Base):
    """A media/doc asset backing a course. Bytes are stored on a UC Volume; this
    row only holds metadata + the ``storage_path``."""

    __tablename__ = "training_media"

    id = Column(String, primary_key=True, comment="Unique UUID for the media item")
    course_id = Column(String, ForeignKey("training_courses.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    # "video" items are tracked for consumption/progress; "pdf"/"slides"/"doc"
    # are downloadable/viewable resources.
    kind = Column(String, nullable=False, default="video", index=True, comment="video | pdf | slides | doc")
    source_filename = Column(String, nullable=True, comment="Original uploaded filename")
    storage_path = Column(String, nullable=True, comment="UC Volume path of the stored bytes")
    mime_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    # Authoritative duration for videos (seconds). Used to compute completion %
    # server-side rather than trusting the client alone.
    duration_seconds = Column(Float, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TrainingConsumptionModel(Base):
    """Per-learner consumption of a media item — the "show consumption" signal.

    One row per (user_email, media_id). Updated by playback heartbeats from the
    learner UI. When a video crosses the completion threshold we also write a
    ``TrainingCompletionModel`` row so in-app consumption can satisfy gates.
    """

    __tablename__ = "training_consumption"

    id = Column(String, primary_key=True, comment="Unique UUID for the consumption record")
    user_email = Column(String, nullable=False, index=True)
    media_id = Column(String, ForeignKey("training_media.id"), nullable=False, index=True)
    course_id = Column(String, ForeignKey("training_courses.id"), nullable=False, index=True)
    # Furthest position reached (seconds) and the highest watched fraction (0..1).
    position_seconds = Column(Float, nullable=False, default=0.0)
    percent_complete = Column(Float, nullable=False, default=0.0)
    completed = Column(Boolean, nullable=False, default=False, index=True)
    view_count = Column(Integer, nullable=False, default=0)
    first_viewed_at = Column(DateTime, nullable=True)
    last_viewed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_email", "media_id", name="uq_consumption_user_media"),
        Index("idx_consumption_user_course", "user_email", "course_id"),
    )
