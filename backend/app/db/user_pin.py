"""
Discover assets a user has pinned for quick access.

Pins are server-side so they follow the user across browsers and devices. Each
row carries the display fields (title, subtitle, link) captured at pin time, so
the pinned strip renders without loading the whole catalog first.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.db.base import Base


class UserPinModel(Base):
    """One asset pinned by one user."""

    __tablename__ = "user_pins"
    __table_args__ = (
        UniqueConstraint("user_email", "asset_id", name="uq_user_pins_user_asset"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    user_email: str = Column(
        String, nullable=False, index=True,
        comment="Owner's lowercased email. Every query filters on this.",
    )
    asset_id: str = Column(
        String, nullable=False,
        comment="Discover asset id: data_assets.id, a legacy mapping id, or a dashboard/app id",
    )
    asset_type: str = Column(
        String, nullable=False,
        comment="Lowercased asset type, e.g. metric_view, view, managed, dashboard, app, legacy_dashboard",
    )
    title: str = Column(String, nullable=False)
    subtitle: Optional[str] = Column(String, nullable=True, comment="e.g. catalog.schema or domain › subdomain")
    url: Optional[str] = Column(
        String, nullable=True,
        comment="External link for assets that open outside Discover (dashboards, apps)",
    )
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
