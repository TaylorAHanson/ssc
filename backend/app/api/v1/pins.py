"""
Pinned Discover assets: the caller's own list of tables, views, metric views,
dashboards, apps and legacy dashboards they want one click away.

Every handler scopes to ``current_user``; there is no way to read or change
another user's pins.
"""
import logging
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api import deps
from app.models.user import User
from app.services import user_pin_service as pins

logger = logging.getLogger(__name__)

router = APIRouter()


class PinCreate(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=1024)
    asset_type: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=500)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    url: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("asset_id", "title")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def _http_url_only(cls, value: Optional[str]) -> Optional[str]:
        # The strip opens this link on click, so only plain web links are stored.
        if not value or not value.strip():
            return None
        value = value.strip()
        if urlparse(value).scheme.lower() not in ("http", "https"):
            raise ValueError("must be an http(s) URL")
        return value


@router.get("")
def list_pins(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """The caller's pins, newest first, flagged when the asset has left the catalog.

    Pins of assets hidden from Discover by tag are left out (and come back if
    the tag or setting is removed).
    """
    rows = pins.list_pins(db, current_user.email)
    hidden = pins.hidden_pin_ids(db, rows)
    rows = [p for p in rows if p.asset_id not in hidden]
    available = pins.availability(db, rows)
    return {
        "pins": [pins.to_dict(p, available.get(p.asset_id)) for p in rows],
        "count": len(rows),
        "max": pins.max_pins_per_user(),
    }


@router.post("")
def pin_asset(
    body: PinCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Pin an asset. Pinning one that's already pinned just refreshes its labels."""
    try:
        pin = pins.pin_asset(
            db,
            current_user.email,
            asset_id=body.asset_id,
            asset_type=body.asset_type,
            title=body.title,
            subtitle=body.subtitle,
            url=body.url,
        )
    except pins.PinLimitError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if pin.asset_id in pins.hidden_pin_ids(db, [pin]):
        return pins.to_dict(pin, False)
    available = pins.availability(db, [pin])
    return pins.to_dict(pin, available.get(pin.asset_id))


@router.delete("/{asset_id:path}")
def unpin_asset(
    asset_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Unpin an asset. Idempotent: unpinning something not pinned is not an error."""
    return {"deleted": 1 if pins.unpin_asset(db, current_user.email, asset_id) else 0}
