"""
Governance API routes: on-demand Enforcement Sentinel digest.

The daily digest is normally sent on an anchored schedule (see
``ENFORCEMENT_DIGEST_HOUR_LOCAL``). These endpoints let a Platform/Governance
Admin preview that schedule and send the current digest to an arbitrary
recipient on demand — useful for testing the layout or sharing a snapshot
without waiting for the scheduled send.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db import RequestModel
from app.db.session import get_db
from app.models.request import RequestStatus, RequestType
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_governance_admin(user: User) -> None:
    if not user.has_role("Platform Admin") and not user.has_role("Governance Admin"):
        raise HTTPException(status_code=403, detail="Not authorized to manage governance digests")


def _latest_completed_run(db: Session) -> Optional[RequestModel]:
    """Most recent completed Enforcement Sentinel run (the digest's data source)."""
    return (
        db.query(RequestModel)
        .filter(
            RequestModel.type == RequestType.ENFORCEMENT_SENTINEL.value,
            RequestModel.status == RequestStatus.COMPLETED.value,
        )
        .order_by(RequestModel.created_at.desc())
        .first()
    )


@router.get("/digest-info")
async def get_digest_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Schedule + default recipient + latest-run summary for the digest modal."""
    _require_governance_admin(current_user)
    from app.workflows.sentinel import _active_violations, digest_schedule_info
    from app.services.sentinel_findings import load_run_violations

    info = digest_schedule_info()
    run = _latest_completed_run(db)
    rows = _active_violations(load_run_violations(db, run)) if run else []
    return {
        **info,
        "default_recipient": (getattr(settings, "GOVERNANCE_EMAIL_GROUP", "") or ""),
        "latest_run_id": run.id if run else None,
        "latest_run_at": (run.created_at.isoformat() + "Z") if run and run.created_at else None,
        "active_violations": len(rows),
    }


class SendDigestRequest(BaseModel):
    email: str


@router.post("/digest/send")
async def send_digest_now(
    body: SendDigestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Build the digest from the latest completed run and email it now.

    Accepts one address or a comma-separated list. Bypasses the anchored
    once-per-day gate and never touches ``digest_emitted_at``, so a manual send
    can't suppress the next scheduled digest.
    """
    _require_governance_admin(current_user)
    from app.providers.notifications.client import NotificationProvider
    from app.workflows.sentinel import _active_violations, render_digest_html
    from app.services.sentinel_findings import load_run_violations

    email = (body.email or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required")

    run = _latest_completed_run(db)
    if not run:
        raise HTTPException(
            status_code=404,
            detail="No completed Sentinel run found to build a digest from. Run a scan first.",
        )

    rows = _active_violations(load_run_violations(db, run))
    brand_color = (getattr(settings, "BRAND_COLOR_PRIMARY", "") or "#2563eb").strip() or "#2563eb"
    app_url = (getattr(settings, "APP_BASE_URL", "") or "").strip()
    body_html = render_digest_html(rows, brand_color, app_url)

    notifier = NotificationProvider()
    try:
        await notifier.send_email(
            to=email,
            subject="[Enforcement] Daily governance digest (on demand)",
            body=body_html,
            is_html=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("On-demand digest send to %s failed: %s", email, e)
        raise HTTPException(status_code=500, detail=f"Failed to send digest: {e}")

    logger.info(
        "On-demand digest sent to %s by %s (violations=%d, source_run=%s)",
        email, current_user.email, len(rows), run.id,
    )
    return {
        "sent": True,
        "recipient": email,
        "violation_count": len(rows),
        "source_run_id": run.id,
    }


class PurgeSentinelRunsRequest(BaseModel):
    #: Keep the most recent N terminal runs; delete the older terminal ones.
    keep_last: int = 5
    #: Also delete hung/orphaned non-terminal runs (e.g. stuck in "discovering"
    #: because the worker died mid-scan). Uses the same staleness definition the
    #: poller does, so a genuinely in-progress scan is never touched.
    clear_stuck: bool = True
    #: A non-terminal run counts as "stuck" once it hasn't been updated for this
    #: many minutes AND holds no live lock. Defaults to the poller's
    #: ENFORCEMENT_SENTINEL_STALE_MINUTES so both agree on what's orphaned.
    stuck_after_minutes: Optional[int] = None


@router.post("/sentinel/runs/purge")
async def purge_sentinel_runs(
    body: PurgeSentinelRunsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete old and/or hung Enforcement Sentinel runs (and their findings).

    Two things get cleared, Platform Admin only:

    * **Old history** — keeps the most recent ``keep_last`` terminal runs and
      deletes the terminal ones older than that (sheds accumulated history that
      slows the runs list).
    * **Hung runs** — when ``clear_stuck`` is set, also deletes non-terminal runs
      that are orphaned (stuck in e.g. "discovering" because the worker died):
      not updated for ``stuck_after_minutes`` AND holding no live lock. This is
      the exact staleness the poller uses, so a genuinely in-progress scan is
      never deleted.

    Deletion is per-run via the ORM (so approvals/events/failures cascade) with
    the heavy ``sentinel_findings`` rows bulk-deleted first, committed in small
    batches so a large cleanup can't blow up one transaction.
    """
    if not current_user.has_role("Platform Admin"):
        raise HTTPException(status_code=403, detail="Not authorized to purge Sentinel runs")

    from app.db.sentinel_finding import SentinelFindingModel

    keep_last = max(0, body.keep_last)
    terminal = {
        RequestStatus.COMPLETED.value,
        RequestStatus.FAILED.value,
        RequestStatus.REJECTED.value,
    }

    now_naive = datetime.utcnow()  # updated_at / locked_until are naive UTC
    stale_minutes = body.stuck_after_minutes
    if stale_minutes is None:
        stale_minutes = getattr(settings, "ENFORCEMENT_SENTINEL_STALE_MINUTES", 45)
    stale_cutoff = now_naive - timedelta(minutes=max(0, stale_minutes))

    # Newest-first so we can keep the most recent N terminal runs. Pull the fields
    # needed to judge staleness in the same query.
    rows = (
        db.query(
            RequestModel.id,
            RequestModel.status,
            RequestModel.updated_at,
            RequestModel.locked_until,
        )
        .filter(RequestModel.type == RequestType.ENFORCEMENT_SENTINEL.value)
        .order_by(RequestModel.created_at.desc())
        .all()
    )
    keep_ids = {r.id for r in rows[:keep_last]}

    def _is_stuck(r) -> bool:
        """Non-terminal, not updated recently, and no live lock (orphaned)."""
        if r.status in terminal:
            return False
        recently_updated = r.updated_at is not None and r.updated_at >= stale_cutoff
        live_lock = r.locked_until is not None and r.locked_until > now_naive
        return not recently_updated and not live_lock

    to_delete: list[str] = []
    stuck_count = 0
    skipped_active = 0
    for idx, r in enumerate(rows):
        if r.id in keep_ids:
            continue
        if r.status in terminal:
            to_delete.append(r.id)
        elif body.clear_stuck and _is_stuck(r):
            to_delete.append(r.id)
            stuck_count += 1
        else:
            # Non-terminal and still being worked (fresh update or live lock).
            skipped_active += 1

    deleted = 0
    batch = 25
    for i in range(0, len(to_delete), batch):
        chunk = to_delete[i : i + batch]
        db.query(SentinelFindingModel).filter(
            SentinelFindingModel.request_id.in_(chunk)
        ).delete(synchronize_session=False)
        for rid in chunk:
            obj = db.get(RequestModel, rid)
            if obj is not None:
                db.delete(obj)
                deleted += 1
        db.commit()

    logger.info(
        "Purged %d Sentinel run(s) by %s (kept most recent %d, cleared %d hung, "
        "%d skipped as active).",
        deleted, current_user.email, len(keep_ids), stuck_count, skipped_active,
    )
    return {
        "deleted": deleted,
        "stuck_cleared": stuck_count,
        "kept": len(keep_ids),
        "skipped_active": skipped_active,
        "requested_keep_last": keep_last,
    }


@router.get("/target-workspaces")
async def list_target_workspaces(
    current_user: User = Depends(get_current_user),
):
    """List the target workspaces the Enforcement Sentinel scans.

    Returns non-secret metadata only (name / environment / host) so the Sentinel
    UI can offer a workspace picker for a scan. Credentials/secret key names are
    never included.
    """
    _require_governance_admin(current_user)
    from app.core.workspaces import get_target_workspaces

    workspaces = [
        {"name": w.name, "environment": w.environment, "host": w.host}
        for w in get_target_workspaces()
    ]
    cert_workspace = (getattr(settings, "SENTINEL_DATA_CERT_WORKSPACE", "") or "").strip()
    return {"workspaces": workspaces, "data_certification_workspace": cert_workspace}
