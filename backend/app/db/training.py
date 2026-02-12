from sqlalchemy import Column, Integer, String, DateTime, Index
from app.db.base import Base
from datetime import datetime

class TrainingCompletionModel(Base):
    __tablename__ = "training_completions"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, index=True, nullable=False)
    course_name = Column(String, nullable=True)
    course_code = Column(String, index=True, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default="completed")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Composite index for faster lookups and ensuring uniqueness if needed
    __table_args__ = (
        Index('idx_user_course', 'user_email', 'course_code'),
    )
