"""What the assistant may tell a user about training and community links.

The Training and Community Links pages are switched on and off, and edited, under
Admin -> Settings. The assistant used to carry a static copy of both in its prompt on
every turn, which went stale (training moved to the database, the links to live
settings) and kept recommending pages an admin had turned off.

This module is the single answer to "is it on, and what is in it right now", read at
the moment it is asked, for both the prompt (whether to mention the pages at all) and
the ``get_learning_resources`` tool (the content on demand).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TRAINING_PAGE = "/community/training"
COMMUNITY_LINKS_PAGE = "/community/links"

# A tool result is not standing context, but a long list still crowds the answer.
_MAX_COURSES = 80


def _tab_enabled(name: str) -> bool:
    from app.core import settings_store

    return bool(settings_store.effective_ui_tabs().get(name, True))


def training_enabled() -> bool:
    """The Training page is switched on (so pointing a user at it is not a dead end)."""
    return _tab_enabled("training")


def _normalise_link(link: Any) -> Optional[Dict[str, str]]:
    """A link as ``{title, url, description}``; accepts the compact ``Title | URL | icon | text``."""
    if isinstance(link, str):
        parts = [p.strip() for p in link.split("|")]
        link = {
            "title": parts[0] if len(parts) > 0 else "",
            "url": parts[1] if len(parts) > 1 else "",
            "description": parts[3] if len(parts) > 3 else "",
        }
    if not isinstance(link, dict):
        return None
    title = str(link.get("title") or "").strip()
    url = str(link.get("url") or "").strip()
    if not title or not url:
        return None
    out = {"title": title, "url": url}
    description = str(link.get("description") or "").strip()
    if description:
        out["description"] = description
    return out


def community_links() -> List[Dict[str, Any]]:
    """The configured link categories, empty when the page is off or has no links."""
    from app.core.config import _yaml_config

    if not _tab_enabled("community_links"):
        return []
    raw = _yaml_config.get("community_links") or {}
    if not isinstance(raw, dict) or not raw.get("enabled", True):
        return []
    categories: List[Dict[str, Any]] = []
    for cat in raw.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        links = [l for l in (_normalise_link(x) for x in cat.get("links") or []) if l]
        name = str(cat.get("name") or "").strip()
        if name and links:
            categories.append({"name": name, "links": links})
    return categories


def community_links_enabled() -> bool:
    """The Community Links page is on and has something on it to point to."""
    return bool(community_links())


def learning_available() -> bool:
    return training_enabled() or community_links_enabled()


def training_tracks(*, persona: Optional[str] = None, topic: Optional[str] = None) -> List[Dict[str, Any]]:
    """Published tracks with their published courses, as compact lines.

    ``persona`` narrows to tracks whose name or audience contains it; ``topic`` to
    courses (or tracks) mentioning it. Anything unpublished stays out, exactly as on
    the learner page.
    """
    from app.db.session import get_lakebase_session
    from app.services.training_service import TrainingService

    persona_q = (persona or "").strip().lower()
    topic_q = (topic or "").strip().lower()
    db = get_lakebase_session()
    try:
        tracks = TrainingService.list_tracks(db, include_drafts=False)
        by_track = TrainingService.courses_by_track(db, [t.id for t in tracks])
        out: List[Dict[str, Any]] = []
        budget = _MAX_COURSES
        for track in tracks:
            if persona_q and persona_q not in f"{track.name} {track.persona or ''}".lower():
                continue
            track_hit = topic_q and topic_q in f"{track.name} {track.description or ''}".lower()
            lines: List[str] = []
            seen: set = set()
            for course in by_track.get(track.id, []):
                if course.status != "published" or course.title in seen:
                    continue
                if topic_q and not track_hit and topic_q not in f"{course.title} {course.description or ''}".lower():
                    continue
                seen.add(course.title)
                detail = ", ".join(x for x in (course.section, course.duration, course.course_type) if x)
                line = f"{course.title} ({detail})" if detail else course.title
                if course.external_url:
                    line += f" {course.external_url}"
                lines.append(line)
            if not lines:
                continue
            lines = lines[:budget]
            budget -= len(lines)
            entry: Dict[str, Any] = {"track": track.name, "courses": lines}
            if track.description:
                entry["about"] = track.description
            out.append(entry)
            if budget <= 0:
                break
        return out
    finally:
        db.close()


def learning_resources(
    *,
    kind: str = "all",
    persona: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """The tool's answer: only the sections that are switched on, with their pages."""
    result: Dict[str, Any] = {}
    if kind in ("all", "training") and training_enabled():
        try:
            tracks = training_tracks(persona=persona, topic=topic)
        except Exception as e:  # noqa: BLE001 - a broken lookup must not break the chat
            logger.error("Could not read training tracks: %s", e, exc_info=True)
            tracks = []
        result["training"] = {
            "page": TRAINING_PAGE,
            "tracks": tracks,
            "note": (
                "These are the published training tracks. To see which the user has "
                "finished, use check_training_status. Link the page as "
                f"[Training]({TRAINING_PAGE})."
            ),
        }
    if kind in ("all", "community") and community_links_enabled():
        categories = community_links()
        if topic:
            q = topic.strip().lower()
            categories = [
                {**c, "links": [l for l in c["links"] if q in f"{l['title']} {l.get('description', '')}".lower()]}
                for c in categories
            ]
            categories = [c for c in categories if c["links"]]
        result["community_links"] = {"page": COMMUNITY_LINKS_PAGE, "categories": categories}
    if not result:
        result["message"] = (
            "Nothing matched, or those pages are not available here. Say so plainly; "
            "do not invent a course, link or channel."
        )
    return result
