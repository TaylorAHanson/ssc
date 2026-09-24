from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.orm import Mapped

from app.db.base import Base


class LegacyDashboardMappingModel(Base):
    """
    A legacy (e.g. Tableau) dashboard and the governed metric view that replaces it.

    Temporary: bridges users from the old BI estate to metric views while the
    migration runs. Entries are curated by admins; the dashboard's domain and
    subdomain come from the metric view it maps to, so they aren't stored here.
    """
    __tablename__ = "legacy_dashboard_mappings"

    id: Mapped[str] = Column(String, primary_key=True, comment="UUID")
    dashboard: Mapped[str] = Column(String, nullable=False, comment="Legacy dashboard name as users know it")
    description: Mapped[Optional[str]] = Column(Text, nullable=True)
    status: Mapped[str] = Column(String, nullable=False, default="Active", index=True, comment="Active, Migrating, Deprecated")
    owner: Mapped[Optional[str]] = Column(String, nullable=True, comment="Owning team")
    url: Mapped[Optional[str]] = Column(String, nullable=True, comment="Link to the legacy dashboard")
    metric_view_id: Mapped[str] = Column(String, nullable=False, index=True, comment="data_assets.id of the replacing metric view")
    updated_by: Mapped[Optional[str]] = Column(String, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
