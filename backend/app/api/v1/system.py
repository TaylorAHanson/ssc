"""
System API routes for configuration and schedules.
"""
from fastapi import APIRouter
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

class ScheduleInfo(BaseModel):
    cron: str
    next_run: Optional[str] = None

class SystemSchedules(BaseModel):
    enforcement_sentinel: ScheduleInfo
    data_asset_sync: ScheduleInfo
    event_sync: ScheduleInfo

def get_next_run(cron_expr: str) -> Optional[str]:
    if not cron_expr:
        return None
    try:
        from croniter import croniter
        now = datetime.utcnow()
        iter = croniter(cron_expr, now)
        return iter.get_next(datetime).isoformat() + "Z"
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
        event_sync=ScheduleInfo(
            cron=getattr(settings, 'EVENT_SYNC_CRON', ''),
            next_run=get_next_run(getattr(settings, 'EVENT_SYNC_CRON', ''))
        )
    )
