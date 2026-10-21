"""
Persistence for a user's pinned Discover assets.

Every lookup filters on the owner, so a pin is never addressable by asset id
alone. Owners are keyed on the lowercased email, like profiles and chat
transcripts, so one person never ends up with two pin lists.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.data_asset import DataAssetModel
from app.db.legacy_dashboard import LegacyDashboardMappingModel
from app.db.user_pin import UserPinModel
from app.services.user_context import normalize_email

logger = logging.getLogger(__name__)



def max_pins_per_user() -> int:
    return max(1, min(int(settings.DISCOVER_MAX_PINS or 50), 500))

# Assets that live outside the data_assets cache. They come from workspace and
# ODPS listings we don't persist, so whether they still exist is unknown.
EXTERNAL_TYPES = frozenset({"dashboard", "app", "genie_space", "job", "data_product"})
LEGACY_DASHBOARD_TYPE = "legacy_dashboard"


class PinLimitError(ValueError):
    """The user already has the maximum number of pins."""


def normalize_type(asset_type: Optional[str]) -> str:
    return (asset_type or "").strip().lower() or "table"


def list_pins(db: Session, user_email: str) -> List[UserPinModel]:
    """The user's pins, newest first."""
    return (
        db.query(UserPinModel)
        .filter(UserPinModel.user_email == normalize_email(user_email))
        .order_by(UserPinModel.created_at.desc(), UserPinModel.id.desc())
        .all()
    )


def _get_pin(db: Session, owner: str, asset_id: str) -> Optional[UserPinModel]:
    return (
        db.query(UserPinModel)
        .filter(UserPinModel.user_email == owner, UserPinModel.asset_id == asset_id)
        .first()
    )


def pin_asset(
    db: Session,
    user_email: str,
    *,
    asset_id: str,
    asset_type: Optional[str],
    title: str,
    subtitle: Optional[str] = None,
    url: Optional[str] = None,
    max_pins: Optional[int] = None,
) -> UserPinModel:
    """Pin an asset, or refresh the display fields of an existing pin.

    Re-pinning keeps the original ``created_at`` so the strip's order doesn't
    jump when a card re-sends a pin it already holds.
    """
    owner = normalize_email(user_email)
    existing = _get_pin(db, owner, asset_id)
    if existing is not None:
        existing.asset_type = normalize_type(asset_type)
        existing.title = title
        existing.subtitle = subtitle
        existing.url = url
        db.commit()
        db.refresh(existing)
        return existing

    max_pins = max_pins or max_pins_per_user()
    count = db.query(UserPinModel).filter(UserPinModel.user_email == owner).count()
    if count >= max_pins:
        raise PinLimitError(
            f"You can pin up to {max_pins} assets. Unpin one before pinning another."
        )

    pin = UserPinModel(
        user_email=owner,
        asset_id=asset_id,
        asset_type=normalize_type(asset_type),
        title=title,
        subtitle=subtitle,
        url=url,
    )
    db.add(pin)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request (e.g. a double click in two tabs) won the insert.
        db.rollback()
        existing = _get_pin(db, owner, asset_id)
        if existing is None:
            raise
        return existing
    db.refresh(pin)
    return pin


def unpin_asset(db: Session, user_email: str, asset_id: str) -> bool:
    """Remove one of the user's pins. Returns False when it wasn't pinned."""
    deleted = (
        db.query(UserPinModel)
        .filter(
            UserPinModel.user_email == normalize_email(user_email),
            UserPinModel.asset_id == asset_id,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return bool(deleted)


def availability(db: Session, pins: List[UserPinModel]) -> Dict[str, Optional[bool]]:
    """Whether each pinned asset is still in the catalog, keyed by asset id.

    One IN query per backing table. ``None`` means unknown (dashboards and apps
    aren't cached locally), so the UI should treat those as available.
    """
    catalog_ids = {p.asset_id for p in pins if p.asset_type not in EXTERNAL_TYPES and p.asset_type != LEGACY_DASHBOARD_TYPE}
    legacy_ids = {p.asset_id for p in pins if p.asset_type == LEGACY_DASHBOARD_TYPE}

    found: set = set()
    if catalog_ids:
        found.update(
            row[0]
            for row in db.query(DataAssetModel.id).filter(DataAssetModel.id.in_(catalog_ids)).all()
        )
    if legacy_ids:
        found.update(
            row[0]
            for row in db.query(LegacyDashboardMappingModel.id)
            .filter(LegacyDashboardMappingModel.id.in_(legacy_ids))
            .all()
        )

    return {
        p.asset_id: (None if p.asset_type in EXTERNAL_TYPES else p.asset_id in found)
        for p in pins
    }


def to_dict(pin: UserPinModel, available: Optional[bool] = None) -> Dict[str, Any]:
    return {
        "asset_id": pin.asset_id,
        "asset_type": pin.asset_type,
        "title": pin.title,
        "subtitle": pin.subtitle,
        "url": pin.url,
        "created_at": pin.created_at.isoformat() if pin.created_at else None,
        "available": available,
    }
