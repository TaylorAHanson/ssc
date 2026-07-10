"""
Governance API routes: on-demand Enforcement Sentinel digest.

The daily digest is normally sent on an anchored schedule (see
``ENFORCEMENT_DIGEST_HOUR_LOCAL``). These endpoints let a Platform/Governance
Admin preview that schedule and send the current digest to an arbitrary
recipient on demand — useful for testing the layout or sharing a snapshot
without waiting for the scheduled send.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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

    info = digest_schedule_info()
    run = _latest_completed_run(db)
    rows = _active_violations(run.state_context or {}) if run else []
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

    email = (body.email or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required")

    run = _latest_completed_run(db)
    if not run:
        raise HTTPException(
            status_code=404,
            detail="No completed Sentinel run found to build a digest from. Run a scan first.",
        )

    rows = _active_violations(run.state_context or {})
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
