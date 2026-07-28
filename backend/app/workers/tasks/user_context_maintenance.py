"""
Background upkeep for the user model: pre-warming and retention.

Warming from the browser (app boot, chat mount) covers the common case, but it
cannot help someone who has no cached profile at all and types immediately — the
slow group lookup has nowhere to hide. This sweep closes that gap by refreshing
profiles for anyone seen recently, so a returning user is already warm before
they open the app.

Also prunes the footprint we keep per user (chat transcripts and long-abandoned
profiles) so neither table grows without bound.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List

from app.core.config import settings
from app.core.feature_flags import is_feature_enabled

if TYPE_CHECKING:
    from app.services.user_context import UserIdentity

logger = logging.getLogger(__name__)

# How many profiles to rebuild at once. The identity provider is the bottleneck
# and is shared with live traffic, so keep this small — the sweep is never urgent.
_MAX_CONCURRENT = 3
# Upper bound per sweep, so a large tenant can't turn one cycle into an hour of
# provider calls. Anything missed is picked up next time (ordered oldest-first).
_MAX_PER_SWEEP = 50

_last_prewarm: datetime | None = None
_last_prune: datetime | None = None

# Retention prune is cheap; daily is plenty.
_PRUNE_INTERVAL = timedelta(hours=24)


def _prewarm_interval() -> timedelta:
    """Sweep at half the TTL, so a warm profile never has time to expire."""
    ttl = max(1, int(settings.USER_CONTEXT_TTL_MINUTES or 30))
    return timedelta(minutes=max(5, ttl // 2))


async def user_context_maintenance_task() -> None:
    """Entry point called from the poll loop. Never raises."""
    try:
        await _prewarm_profiles()
    except Exception as e:  # noqa: BLE001 - upkeep must not break the poll cycle
        logger.error("User-context pre-warm failed: %s", e, exc_info=True)
    try:
        await asyncio.to_thread(_prune)
    except Exception as e:  # noqa: BLE001
        logger.error("User-context prune failed: %s", e, exc_info=True)


def _due(last: datetime | None, interval: timedelta) -> bool:
    return last is None or datetime.utcnow() - last >= interval


def _candidates(window_days: int, limit: int) -> List["UserIdentity"]:
    """Profiles worth rebuilding: seen recently, and actually due for a refresh.

    Returns detached identity snapshots rather than ORM rows so the refresh can
    run on its own session.
    """
    from app.db.session import get_lakebase_session
    from app.db.user_profile import UserProfileModel
    from app.services.user_context import UserIdentity, should_refresh

    cutoff = datetime.utcnow() - timedelta(days=window_days)
    db = get_lakebase_session()
    try:
        rows = (
            db.query(UserProfileModel)
            .filter(UserProfileModel.last_seen_at >= cutoff)
            # Oldest first, so a truncated sweep still makes progress on the
            # profiles that need it most.
            .order_by(UserProfileModel.refreshed_at.asc().nullsfirst())
            .limit(limit)
            .all()
        )
        return [UserIdentity.from_profile(row) for row in rows if should_refresh(row)]
    finally:
        db.close()


async def _prewarm_profiles() -> None:
    global _last_prewarm

    if not is_feature_enabled("user_context"):
        return
    window = int(settings.USER_CONTEXT_PREWARM_DAYS or 0)
    if window <= 0:
        return
    if not _due(_last_prewarm, _prewarm_interval()):
        return
    _last_prewarm = datetime.utcnow()

    from app.services.user_context import refresh_profile

    identities = await asyncio.to_thread(_candidates, window, _MAX_PER_SWEEP)
    if not identities:
        logger.debug("User-context pre-warm: nothing due.")
        return

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _one(identity: "UserIdentity") -> None:
        async with semaphore:
            await refresh_profile(identity)

    logger.info("User-context pre-warm: refreshing %d profile(s).", len(identities))
    # ``refresh_profile`` already swallows its own errors, but gather defensively
    # so one unexpected failure can't cancel the rest of the sweep.
    await asyncio.gather(*(_one(i) for i in identities), return_exceptions=True)


def _prune() -> None:
    """Drop expired chat transcripts and long-abandoned profiles.

    Both are scoped by the same retention setting: they are two halves of the
    same thing (what we retain about a user who stopped using the app), and one
    knob is easier to reason about than two. A pruned profile costs nothing — it
    is rebuilt from source the next time that person signs in.
    """
    global _last_prune

    if not _due(_last_prune, _PRUNE_INTERVAL):
        return
    _last_prune = datetime.utcnow()

    from app.db.session import get_lakebase_session
    from app.db.user_profile import UserProfileModel
    from app.services.chat_session_service import prune_sessions

    days = int(settings.CHAT_SESSION_RETENTION_DAYS or 0)
    db = get_lakebase_session()
    try:
        transcripts = prune_sessions(db)
        profiles = 0
        if days > 0:
            cutoff = datetime.utcnow() - timedelta(days=days)
            profiles = (
                db.query(UserProfileModel)
                .filter(UserProfileModel.last_seen_at < cutoff)
                .delete(synchronize_session=False)
            )
            db.commit()
        if transcripts or profiles:
            logger.info(
                "User-context prune: removed %d transcript(s) and %d profile(s) older than %d days.",
                transcripts, profiles or 0, days,
            )
    finally:
        db.close()
