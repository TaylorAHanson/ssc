from sqlalchemy import Column, String, Integer, DateTime
from sqlalchemy.sql import func
from app.db.base import Base

class RoleMappingModel(Base):
    """
    Maps an external SCIM identity (group, role, or user email) to an internal
    application role (e.g., 'Platform Admin', 'User').
    """
    __tablename__ = "role_mappings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    external_role = Column(String, index=True, nullable=False)
    internal_role = Column(String, nullable=False)  # e.g., "Platform Admin", "User", etc.
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
