from sqlalchemy import Column, String, Boolean, DateTime, Integer
from datetime import datetime
from app.db.base import Base

class OdpsModel(Base):
    """
    Model for storing Open Data Product Specification (ODPS) YAML documents.
    We append rows to version them, but don't delete.
    """
    __tablename__ = "odps"

    id = Column(String, primary_key=True, index=True) # UUID
    name = Column(String, nullable=False, index=True) # e.g. Product Name
    yaml_content = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, nullable=True)
