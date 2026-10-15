from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.db.base import Base


class CachedWorkspaceModel(Base):
    """One account workspace as last read from ``system.access.workspaces_latest``.

    Written by the data-asset sync as the governance service principal; every
    sync replaces the whole table in one transaction, so all rows share one
    ``synced_at``. ``region`` / ``cloud`` stay null when the system table
    doesn't expose them.
    """
    __tablename__ = "cached_workspaces"

    workspace_id = Column(String, primary_key=True)
    account_id = Column(String, nullable=True)
    workspace_name = Column(String, nullable=True, index=True)
    workspace_url = Column(String, nullable=True)
    status = Column(String, nullable=True, index=True)
    create_time = Column(DateTime, nullable=True)
    region = Column(String, nullable=True)
    cloud = Column(String, nullable=True)
    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
