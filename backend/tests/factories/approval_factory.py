from app.db.request import ApprovalModel
from datetime import datetime
import uuid
import random

class ApprovalFactory:
    @staticmethod
    def create(session, request_id, **kwargs):
        """Creates and persists an ApprovalModel."""
        
        approval = ApprovalModel(
            id=kwargs.pop("id", f"app-{uuid.uuid4()}"),
            request_id=request_id,
            approval_type=kwargs.pop("approval_type", "manager"),
            requested_by=kwargs.pop("requested_by", "user@example.com"),
            requested_by_email=kwargs.pop("requested_by_email", "user@example.com"),
            status=kwargs.pop("status", "pending"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            **kwargs
        )
        session.add(approval)
        session.commit()
        return approval
