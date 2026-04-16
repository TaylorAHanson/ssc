from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.base import Base

class EnforcementAuditModel(Base):
    __tablename__ = "enforcement_audit"

    id = Column(String, primary_key=True, index=True)
    request_id = Column(String, ForeignKey("requests.id", ondelete="CASCADE"), index=True, nullable=False)
    resource_id = Column(String, index=True, nullable=False)
    resource_type = Column(String, nullable=False)
    policy_name = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    intended_action = Column(String, nullable=False)
    executed_action = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
