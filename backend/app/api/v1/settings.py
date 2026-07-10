"""Admin runtime-settings API.

Exposes the curated, change-on-the-fly configuration overrides backed by the
``app_settings`` table. Platform Admin only. See ``core.settings_store`` for the
field spec and how overrides are applied to the live process.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import require_role
from app.models.user import User
from app.core import settings_store

router = APIRouter()


class SettingsUpdate(BaseModel):
    changes: Dict[str, Any]


@router.get("")
@router.get("/")
def get_settings(user: User = Depends(require_role("Platform Admin"))):
    """Return the editable settings spec + current values and read-only fields."""
    return settings_store.get_state()


@router.put("")
@router.put("/")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("Platform Admin")),
):
    """Apply and persist a batch of setting overrides (takes effect immediately)."""
    if not payload.changes:
        raise HTTPException(status_code=400, detail="No changes provided")
    try:
        return settings_store.set_many(db, payload.changes, updated_by=user.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
