"""The preview-features sync: discover, observe, link docs, archive and re-verify.

One run:

1. For each target workspace, page through the Settings v2 metadata and read
   the value of every preview it lists (rate-limited per workspace).
2. Upsert features and per-workspace targets, recording what each workspace
   lists and its effective value. Rows marked implemented that are now off are
   flagged as drift.
3. Read the docs feed: attach announcements and docs links, mark account-console
   previews as account scope, and add feed-only account previews.
4. Archive features that went GA (or that no workspace has listed for a few
   syncs), closing any in-flight request so it doesn't wait forever.
5. Fill missing docs links from the docs sitemap ("suggested").
6. Re-verify targets whose request is approved but not yet verified.

Network calls happen outside the DB transaction; all writes are one commit.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from croniter import CroniterBadCronError, croniter
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.workspaces import WorkspaceConfig, get_target_workspaces
from app.db.preview_feature import ACCOUNT_TARGET, PreviewFeatureModel, PreviewFeatureTargetModel
from app.services.preview_features import feed as feed_mod
from app.services.preview_features import settings_api
from app.services.preview_features.docs import exact_doc, suggest_doc
from app.services.preview_features.status import close_inflight_requests, record_verification

logger = logging.getLogger(__name__)

_DOCS_DOMAINS = ["docs.databricks.com"]

# Scheduler state (reset by settings_store when the cron is edited).
_next_preview_sync_time: Optional[datetime] = None
_boot_checked = False
# Last run, for the tab's "Last synced" line. In memory: a restart shows the
# newest last_seen_at instead.
_last_run: Dict[str, Any] = {}
_lock = asyncio.Lock()


@dataclass
class WorkspaceScan:
    name: str
    ok: bool = False
    error: Optional[str] = None
    previews: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    ga: Set[str] = field(default_factory=set)
    listed: Set[str] = field(default_factory=set)
    values: Dict[str, settings_api.ObservedValue] = field(default_factory=dict)
    value_errors: Dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0


def _scan_workspace(ws: WorkspaceConfig, concurrency: int) -> WorkspaceScan:
    """Blocking: metadata plus every preview's value for one workspace."""
    started = time.monotonic()
    scan = WorkspaceScan(name=ws.name)
    try:
        client = settings_api.build_client(ws)
        metas = settings_api.list_metadata(client)
    except Exception as e:  # noqa: BLE001 - one bad workspace must not stop the sync
        scan.error = str(e)[:500]
        scan.seconds = time.monotonic() - started
        logger.warning("Preview sync: listing settings on '%s' failed: %s", ws.name, e)
        return scan
    for meta in metas:
        name = meta.get("name")
        if not name:
            continue
        scan.listed.add(name)
        phase = meta.get("preview_phase")
        if phase in settings_api.PREVIEW_PHASES:
            scan.previews[name] = meta
        elif phase == "GA":
            scan.ga.add(name)

    def _read(name: str):
        try:
            return name, settings_api.get_value(client, name), None
        except settings_api.SettingsApiError as e:
            return name, None, str(e)[:300]

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for name, value, err in pool.map(_read, list(scan.previews)):
            if value is not None:
                scan.values[name] = value
            else:
                scan.value_errors[name] = err or "unknown error"
    scan.ok = True
    scan.seconds = time.monotonic() - started
    logger.info(
        "Preview sync: '%s' lists %d settings, %d previews (%d unreadable) in %.1fs",
        ws.name, len(scan.listed), len(scan.previews), len(scan.value_errors), scan.seconds,
    )
    return scan


async def _fetch_text(url: str) -> Optional[str]:
    if not url:
        return None
    from app.tools.web._common import safe_fetch

    res = await safe_fetch(url, allowed_domains=_DOCS_DOMAINS, extract=False, timeout=30)
    if not res.get("ok"):
        logger.warning("Preview sync: fetching %s failed: %s", url, res.get("error"))
        return None
    return str(res.get("raw") or "")


def _sitemap_urls(xml_text: Optional[str]) -> List[str]:
    import re

    if not xml_text:
        return []
    return [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text) if not u.endswith(".xml")]


def _now() -> datetime:
    return datetime.utcnow()


def _target_row(db: Session, feature: PreviewFeatureModel, target: str,
                cache: Dict[tuple, PreviewFeatureTargetModel]) -> PreviewFeatureTargetModel:
    key = (feature.id, target)
    row = cache.get(key)
    if row is None:
        row = PreviewFeatureTargetModel(
            id=str(uuid.uuid4()), feature_id=feature.id, target=target,
            available=False, status="not_requested", drift=False, verify_failures=0,
        )
        db.add(row)
        cache[key] = row
    return row


def _archive(db: Session, feature: PreviewFeatureModel, reason: str, now: datetime) -> None:
    if feature.archived_at:
        return
    feature.archived_at = now
    feature.archived_reason = reason
    note = ("now generally available" if reason == "ga"
            else "no longer listed by any workspace")
    close_inflight_requests(db, feature, note)
    logger.info("Preview sync: archived '%s' (%s)", feature.display_name, reason)


def apply_sync(
    db: Session,
    scans: List[WorkspaceScan],
    feed_items: Optional[List[feed_mod.FeedItem]],
    sitemap: List[str],
) -> Dict[str, Any]:
    """Write one sync's observations. Pure DB work; the caller commits."""
    now = _now()
    ok_scans = [s for s in scans if s.ok]
    features = db.query(PreviewFeatureModel).all()
    by_setting = {f.setting_name: f for f in features if f.setting_name}
    by_feed_key = {f.feed_key: f for f in features if f.feed_key}
    targets: Dict[tuple, PreviewFeatureTargetModel] = {
        (t.feature_id, t.target): t for t in db.query(PreviewFeatureTargetModel).all()
    }
    counts = {"new": 0, "archived_ga": 0, "archived_retired": 0, "verified": 0}

    # --- 1-2. API features and per-workspace observations -------------------
    seen_previews: Dict[str, Dict[str, Any]] = {}
    for scan in ok_scans:
        for name, meta in scan.previews.items():
            seen_previews.setdefault(name, meta)
    ga_everywhere = set().union(*(s.ga for s in ok_scans)) - set(seen_previews) if ok_scans else set()

    for name, meta in seen_previews.items():
        feature = by_setting.get(name)
        if feature is None:
            feature = PreviewFeatureModel(
                id=str(uuid.uuid4()), setting_name=name, scope="workspace", scope_source="api",
                first_seen_at=now, missing_syncs=0,
            )
            db.add(feature)
            by_setting[name] = feature
            counts["new"] += 1
        phase = meta.get("preview_phase")
        if feature.phase and feature.phase != phase:
            feature.phase_changed_at = now
        feature.phase = phase
        feature.display_name = meta.get("display_name") or name
        feature.description = meta.get("description") or feature.description
        feature.value_type = settings_api.value_type_of(meta.get("type"))
        feature.raw = meta
        feature.last_seen_at = now
        feature.missing_syncs = 0
        if feature.archived_at:
            # Listed as a preview again (e.g. a retired row came back).
            feature.archived_at = None
            feature.archived_reason = None
        if meta.get("docs_link") and feature.docs_link_source != "admin":
            feature.docs_link = meta["docs_link"]
            feature.docs_link_source = "api"

    for feature in list(by_setting.values()):
        for scan in ok_scans:
            listed = feature.setting_name in scan.previews
            row = targets.get((feature.id, scan.name))
            if row is None and not listed:
                continue
            row = _target_row(db, feature, scan.name, targets)
            row.available = listed
            if not listed:
                continue
            value = scan.values.get(feature.setting_name)
            if value is not None:
                row.observed_value = value.to_dict()
                row.observe_error = None
            else:
                row.observe_error = scan.value_errors.get(feature.setting_name)
            row.observed_at = now
            if row.status == "implemented" and value is not None and value.effective is not None:
                wanted = row.action != "disable"
                row.drift = value.effective != wanted

    # --- 4a. GA / retired API features --------------------------------------
    retire_after = max(1, int(getattr(settings, "PREVIEW_FEATURE_RETIRE_AFTER_SYNCS", 3) or 3))
    for name, feature in by_setting.items():
        if feature.archived_at or name in seen_previews or not ok_scans:
            continue
        if name in ga_everywhere:
            feature.phase = "GA"
            feature.phase_changed_at = now
            _archive(db, feature, "ga", now)
            counts["archived_ga"] += 1
            continue
        feature.missing_syncs = (feature.missing_syncs or 0) + 1
        if feature.missing_syncs >= retire_after:
            _archive(db, feature, "retired", now)
            counts["archived_retired"] += 1

    # --- 3. Docs feed -------------------------------------------------------
    if feed_items is not None:
        account = feed_mod.account_previews(feed_items)
        active_api = [f for f in by_setting.values() if not f.archived_at]
        for feature in active_api:
            match = feed_mod.match_feature(feature.display_name, feed_items)
            if match is None:
                if feature.docs_link_source == "feed":
                    # An earlier, looser match no longer holds: let the sitemap retry.
                    feature.docs_link = None
                    feature.docs_link_source = None
                feature.announcement_text = feature.announcement_url = feature.announced_at = None
                continue
            feature.announcement_text = match.announcement_text[:4000]
            feature.announcement_url = match.item.link
            feature.announced_at = match.item.published
            if match.docs_link and feature.docs_link_source not in ("admin", "api"):
                feature.docs_link = match.docs_link
                feature.docs_link_source = "feed"

        for preview in account:
            api_feature = next(
                (f for f in active_api if feed_mod.normalize(f.display_name) == preview.key
                 or feed_mod.mentions(f.display_name, preview.key)),
                None,
            )
            if api_feature is not None:
                # An account preview that also shows up in workspace metadata:
                # implemented in the account console, verified per workspace.
                if api_feature.scope_source != "admin":
                    api_feature.scope = "account"
                    api_feature.scope_source = "inferred"
                _target_row(db, api_feature, ACCOUNT_TARGET, targets)
                continue
            feature = by_feed_key.get(preview.key)
            if feature is None:
                feature = PreviewFeatureModel(
                    id=str(uuid.uuid4()), feed_key=preview.key, scope="account", scope_source="inferred",
                    first_seen_at=now, missing_syncs=0, value_type="other",
                )
                db.add(feature)
                by_feed_key[preview.key] = feature
                counts["new"] += 1
            if feature.archived_reason == "ga":
                continue
            if feature.phase and feature.phase != preview.phase:
                feature.phase_changed_at = now
            feature.display_name = preview.name
            feature.phase = preview.phase
            if feature.scope_source != "admin":
                feature.scope = "account"
            text = preview.match.item.text
            feature.description = feature.description or text[:600]
            feature.announcement_text = text[:4000]
            feature.announcement_url = preview.match.item.link
            feature.announced_at = preview.match.item.published
            feature.last_seen_at = now
            if preview.match.docs_link and feature.docs_link_source not in ("admin",):
                feature.docs_link = preview.match.docs_link
                feature.docs_link_source = "feed"
            _target_row(db, feature, ACCOUNT_TARGET, targets)

        # 4b. Feed-only previews: the feed is their only GA signal.
        for feature in by_feed_key.values():
            if feature.archived_at:
                continue
            match = feed_mod.match_feature(feature.display_name, feed_items)
            if (match and match.phase == "GA" and match.item.published and feature.announced_at
                    and match.item.published > feature.announced_at):
                feature.phase = "GA"
                feature.phase_changed_at = now
                _archive(db, feature, "ga", now)
                counts["archived_ga"] += 1

    # --- 5. Sitemap fallback for docs links ---------------------------------
    if sitemap:
        for feature in list(by_setting.values()) + list(by_feed_key.values()):
            if feature.archived_at:
                continue
            if feature.docs_link_source in (None, "feed", "sitemap"):
                exact = exact_doc(feature.setting_name, sitemap)
                if exact:
                    feature.docs_link = exact
                    feature.docs_link_source = "docs"
                    continue
            if feature.docs_link:
                continue
            url = suggest_doc(feature.display_name, sitemap)
            if url:
                feature.docs_link = url
                feature.docs_link_source = "sitemap"

    # --- 6. Account-target observations and re-verification -----------------
    db.flush()
    for (feature_id, target), row in targets.items():
        if target != ACCOUNT_TARGET:
            continue
        feature = db.get(PreviewFeatureModel, feature_id)
        if feature is None or not feature.setting_name:
            continue
        ws_rows = [t for (fid, tg), t in targets.items()
                   if fid == feature_id and tg != ACCOUNT_TARGET and t.available and t.observed_value]
        if ws_rows:
            on = sum(1 for t in ws_rows if (t.observed_value or {}).get("effective"))
            row.available = True
            row.observed_value = {"effective": on == len(ws_rows) if on in (0, len(ws_rows)) else None,
                                  "workspaces_on": on, "workspaces_listed": len(ws_rows)}
            row.observed_at = now
            if row.status == "implemented":
                wanted = row.action != "disable"
                eff = row.observed_value.get("effective")
                row.drift = eff is not None and eff != wanted

    for row in targets.values():
        if row.status != "approved" or not row.request_id:
            continue
        feature = db.get(PreviewFeatureModel, row.feature_id)
        if feature is None:
            continue
        if record_verification(db, feature, row, actor="sync", now=now):
            counts["verified"] += 1

    return counts


async def run_sync() -> Dict[str, Any]:
    """Run one full sync. Concurrent calls share a lock; the second waits."""
    global _last_run
    async with _lock:
        started = datetime.now(timezone.utc)
        _last_run = {**_last_run, "running": True, "started_at": started.isoformat()}
        try:
            workspaces = get_target_workspaces()
            concurrency = int(getattr(settings, "PREVIEW_FEATURE_SYNC_CONCURRENCY", 8) or 8)
            scans = await asyncio.gather(*(
                asyncio.to_thread(_scan_workspace, ws, concurrency) for ws in workspaces
            ))
            feed_xml = await _fetch_text(getattr(settings, "PREVIEW_FEATURE_FEED_URL", ""))
            feed_items = feed_mod.parse_feed(feed_xml) if feed_xml else None
            sitemap = _sitemap_urls(await _fetch_text(getattr(settings, "PREVIEW_FEATURE_SITEMAP_URL", "")))

            def _write() -> Dict[str, Any]:
                from app.db.session import get_lakebase_session

                db = get_lakebase_session()
                try:
                    counts = apply_sync(db, list(scans), feed_items, sitemap)
                    db.commit()
                    return counts
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

            counts = await asyncio.to_thread(_write)
            summary = {
                "running": False,
                "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "workspaces": [
                    {"name": s.name, "ok": s.ok, "message": s.error, "previews": len(s.previews),
                     "unreadable": len(s.value_errors), "seconds": round(s.seconds, 1)}
                    for s in scans
                ],
                "feed_items": len(feed_items) if feed_items is not None else None,
                "sitemap_urls": len(sitemap),
                **counts,
            }
            _last_run = summary
            logger.info("Preview sync finished: %s", {k: v for k, v in summary.items() if k != "workspaces"})
            return summary
        except Exception as e:
            logger.error("Preview sync failed: %s", e, exc_info=True)
            _last_run = {
                "running": False, "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(), "message": str(e)[:500],
            }
            raise


def last_run() -> Dict[str, Any]:
    return dict(_last_run)


def next_run() -> Optional[str]:
    cron_expr = (getattr(settings, "PREVIEW_FEATURE_SYNC_CRON", "") or "").strip()
    if not cron_expr:
        return None
    try:
        return croniter(cron_expr, datetime.now(timezone.utc)).get_next(datetime).isoformat()
    except (CroniterBadCronError, ValueError):
        return None


def _has_features() -> bool:
    from app.db.session import get_lakebase_session

    db = get_lakebase_session()
    try:
        return db.query(PreviewFeatureModel.id).first() is not None
    finally:
        db.close()


async def preview_feature_sync_task() -> None:
    """Poller hook: run the sync when the cron says so (and once at first boot)."""
    global _next_preview_sync_time, _boot_checked
    now = datetime.now(timezone.utc)
    cron_expr = (getattr(settings, "PREVIEW_FEATURE_SYNC_CRON", "") or "").strip()
    force = False
    if not _boot_checked:
        _boot_checked = True
        if cron_expr and not await asyncio.to_thread(_has_features):
            force = True
            logger.info("Preview features table is empty at boot; syncing now.")
    if not force:
        if not cron_expr:
            return
        if _next_preview_sync_time is None:
            try:
                _next_preview_sync_time = croniter(cron_expr, now).get_next(datetime)
            except (CroniterBadCronError, ValueError):
                logger.error("Invalid PREVIEW_FEATURE_SYNC_CRON expression: %s", cron_expr)
                return
        if now < _next_preview_sync_time:
            return
    if cron_expr:
        try:
            _next_preview_sync_time = croniter(cron_expr, now).get_next(datetime)
        except (CroniterBadCronError, ValueError):
            pass
    await run_sync()
