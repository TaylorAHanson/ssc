"""
Governance API routes: on-demand Enforcement Sentinel digest.

The daily digest is normally sent on an anchored schedule (see
``ENFORCEMENT_DIGEST_HOUR_LOCAL``). These endpoints let a Platform/Governance
Admin preview that schedule and send the current digest to an arbitrary
recipient on demand — useful for testing the layout or sharing a snapshot
without waiting for the scheduled send.
"""
import logging
from datetime import datetime
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
    #: Keep the most recent N **terminal** runs; delete older terminal ones. This
    #: never protects a non-terminal (e.g. stuck "discovering") run — a hung run
    #: is not history worth keeping.
    keep_last: int = 5
    #: Delete orphaned non-terminal runs — ones no worker is currently holding
    #: (no live lock). Covers the common case of a run stuck in "discovering"
    #: because the worker died mid-scan.
    clear_stuck: bool = True
    #: Also delete non-terminal runs that STILL hold a live lock. A genuinely
    #: hung run whose worker keeps heartbeating the lock (so it never looks
    #: orphaned) can only be cleared this way. Use when a run is wedged in
    #: "discovering" and ``clear_stuck`` alone didn't remove it.
    force: bool = False
    #: Retained for API compatibility; no longer used for the stuck decision
    #: (lock liveness is the signal, not update recency).
    stuck_after_minutes: Optional[int] = None


@router.post("/sentinel/runs/purge")
async def purge_sentinel_runs(
    body: PurgeSentinelRunsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete old and/or hung Enforcement Sentinel runs (and their findings).

    Platform Admin only. What gets cleared:

    * **Old history** — keeps the most recent ``keep_last`` *terminal* runs and
      deletes older terminal ones (sheds history that slows the runs list).
      ``keep_last`` never protects a non-terminal run.
    * **Orphaned runs** — with ``clear_stuck`` (default), non-terminal runs that
      no worker currently holds (no live lock) are deleted. This covers a run
      stuck in "discovering" after its worker died.
    * **Wedged runs** — with ``force``, non-terminal runs that STILL hold a live
      lock are deleted too. A run whose worker keeps heartbeating the lock never
      looks orphaned, so ``force`` is the only way to shed it.

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

    now_naive = datetime.utcnow()  # locked_until is naive UTC

    rows = (
        db.query(
            RequestModel.id,
            RequestModel.status,
            RequestModel.locked_until,
        )
        .filter(RequestModel.type == RequestType.ENFORCEMENT_SENTINEL.value)
        .order_by(RequestModel.created_at.desc())
        .all()
    )
    # keep_last protects only the most recent TERMINAL runs — a hung, non-terminal
    # run (the thing we're usually trying to shed) is never kept as "history".
    terminal_ids_newest_first = [r.id for r in rows if r.status in terminal]
    keep_ids = set(terminal_ids_newest_first[:keep_last])

    to_delete: list[str] = []
    stuck_count = 0
    skipped_active = 0
    for r in rows:
        if r.id in keep_ids:
            continue
        if r.status in terminal:
            to_delete.append(r.id)
            continue
        # Non-terminal run: decide whether it's safe/desired to remove.
        live_lock = r.locked_until is not None and r.locked_until > now_naive
        if body.force:
            to_delete.append(r.id)
            stuck_count += 1
        elif body.clear_stuck and not live_lock:
            # Orphaned: no worker is holding it right now.
            to_delete.append(r.id)
            stuck_count += 1
        else:
            # A worker currently holds a live lock and force wasn't requested.
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
