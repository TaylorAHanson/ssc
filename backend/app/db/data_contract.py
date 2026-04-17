from sqlalchemy import Column, String, Boolean, DateTime, Integer
from datetime import datetime
from app.db.base import Base

class DataContractModel(Base):
    """
    Model for storing Data Contracts (ODCS YAML) for datasets.
    We append rows to version them, but don't delete.
    """
    __tablename__ = "data_contracts"

    id = Column(String, primary_key=True, index=True) # UUID
    dataset_id = Column(String, nullable=False, index=True) # catalog.schema.table
    yaml_content = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, nullable=True)
