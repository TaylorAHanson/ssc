from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String

from app.db.base import Base
from app.db.data_asset import JSONType


class DiscoverDomainModel(Base):
    """A Databricks Discover domain or subdomain, cached by the data-asset sync.

    Domains are account-level objects bound to a key-only governed tag; a
    subdomain has a parent and a ``{parent tag}/{name}`` tag key. See
    ``app/services/discover_domains.py``.
    """

    __tablename__ = "discover_domains"

    id = Column(String, primary_key=True)  # Databricks domain_id
    tag_key = Column(String, nullable=False, index=True)
    parent_id = Column(String, nullable=True, index=True)
    subtitle = Column(String, nullable=True)
    description = Column(String, nullable=True)
    icon = Column(JSONType, nullable=True)  # {"name": "BANK", "color": "#1B5E20"}
    # [{"id": 123, "name": "Jane Doe"}]; names only for domains that have assets.
    technical_owners = Column(JSONType, nullable=True)
    business_owners = Column(JSONType, nullable=True)
    draft = Column(Boolean, default=False, nullable=False)
    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
