"""Admin runtime-settings API.

Exposes the curated, change-on-the-fly configuration overrides backed by the
``app_settings`` table. Needs ``settings.manage``. See ``core.settings_store`` for the
field spec and how overrides are applied to the live process.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import require_capability
from app.models.user import User
from app.core import settings_store

router = APIRouter()


class SettingsUpdate(BaseModel):
    changes: Dict[str, Any]


@router.get("")
@router.get("/")
def get_settings(user: User = Depends(require_capability("settings.manage"))):
    """Return the editable settings spec + current values and read-only fields."""
    return settings_store.get_state()


@router.put("")
@router.put("/")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_capability("settings.manage")),
):
    """Apply and persist a batch of setting overrides (takes effect immediately)."""
    if not payload.changes:
        raise HTTPException(status_code=400, detail="No changes provided")
    try:
        return settings_store.set_many(db, payload.changes, updated_by=user.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Service principals ----------------------------------------------------------

class ServicePrincipalCheckIn(BaseModel):
    name: str


@router.get("/service-principals/usage")
def service_principal_usage(user: User = Depends(require_capability("settings.manage"))):
    """Which identity each part of the app runs as, from the saved settings (no secrets are read)."""
    from app.core import service_principals

    return {"rows": service_principals.usage(), "names": service_principals.names()}


@router.post("/service-principals/check")
def check_service_principal(
    body: ServicePrincipalCheckIn, user: User = Depends(require_capability("settings.manage")),
):
    """Read a named SP's secrets and sign in as it on the home workspace. Saved settings only."""
    from dataclasses import asdict

    from app.core import service_principals

    if service_principals.get(body.name) is None:
        raise HTTPException(status_code=404, detail=(
            f'There\'s no saved service principal named "{body.name}". Save the list first.'))
    return asdict(service_principals.check(body.name))
