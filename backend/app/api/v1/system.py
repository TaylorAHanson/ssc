"""
System API routes for configuration and schedules.
"""
from fastapi import APIRouter
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

class ScheduleInfo(BaseModel):
    cron: str
    next_run: Optional[str] = None

class SystemSchedules(BaseModel):
    enforcement_sentinel: ScheduleInfo
    data_asset_sync: ScheduleInfo
    contract_sync: ScheduleInfo
    event_sync: ScheduleInfo

def get_next_run(cron_expr: str) -> Optional[str]:
    if not cron_expr:
        return None
    try:
        from croniter import croniter
        now = datetime.now(timezone.utc)
        iter = croniter(cron_expr, now)
        # croniter returns a tz-aware (UTC) datetime here, whose isoformat()
        # already carries a "+00:00" offset. Appending "Z" on top of that yields
        # a malformed "...+00:00Z" string that the frontend parses as an Invalid
        # Date. Normalize to a clean naive-UTC ISO string with a single trailing
        # "Z", matching how every other timestamp is emitted to the UI.
        next_run = iter.get_next(datetime).astimezone(timezone.utc).replace(tzinfo=None)
        return next_run.isoformat() + "Z"
    except Exception:
        return None

@router.get("/schedules", response_model=SystemSchedules)
async def get_schedules():
    from app.core.config import settings
    return SystemSchedules(
        enforcement_sentinel=ScheduleInfo(
            cron=getattr(settings, 'ENFORCEMENT_SENTINEL_CRON', ''),
            next_run=get_next_run(getattr(settings, 'ENFORCEMENT_SENTINEL_CRON', ''))
        ),
        data_asset_sync=ScheduleInfo(
            cron=getattr(settings, 'DATA_ASSET_SYNC_CRON', ''),
            next_run=get_next_run(getattr(settings, 'DATA_ASSET_SYNC_CRON', ''))
        ),
        contract_sync=ScheduleInfo(
            cron=getattr(settings, 'CONTRACT_SYNC_CRON', ''),
            next_run=get_next_run(getattr(settings, 'CONTRACT_SYNC_CRON', ''))
        ),
        event_sync=ScheduleInfo(
            cron=getattr(settings, 'EVENT_SYNC_CRON', ''),
            next_run=get_next_run(getattr(settings, 'EVENT_SYNC_CRON', ''))
        )
    )
