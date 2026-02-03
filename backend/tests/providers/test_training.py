import pytest
from unittest.mock import MagicMock
from app.providers.training.client import TrainingProvider
from app.models.training import TrainingCompletionModel
from datetime import datetime

def test_ingest_training_csv_success():
    # Mock DB session
    db = MagicMock()
    # Mock query to return None (no existing records)
    db.query.return_value.filter.return_value.first.return_value = None

    provider = TrainingProvider(db)
    
    csv_content = """updated_learner_email,Course_Name,Course_Code,completed_timestamp,Status
test@example.com,Test Course,TEST-101,2025-10-28 10:00:00,completed
"""
    
    stats = provider.ingest_training_csv(csv_content)
    
    assert stats["processed"] == 1
    assert stats["added"] == 1
    assert stats["updated"] == 0
    assert stats["skipped"] == 0
    
    # Verify DB add was called
    db.add.assert_called_once()
    db.commit.assert_called_once()

def test_ingest_training_csv_update():
    # Mock DB session and existing record
    db = MagicMock()
    existing_record = TrainingCompletionModel(
        user_email="test@example.com",
        course_code="TEST-101",
        status="pending"
    )
    db.query.return_value.filter.return_value.first.return_value = existing_record

    provider = TrainingProvider(db)
    
    csv_content = """updated_learner_email,Course_Name,Course_Code,completed_timestamp,Status
test@example.com,Test Course,TEST-101,2025-10-28 10:00:00,completed
"""
    
    stats = provider.ingest_training_csv(csv_content)
    
    assert stats["processed"] == 1
    assert stats["added"] == 0
    assert stats["updated"] == 1
    
    # Verify record was updated
    assert existing_record.status == "completed"
    db.commit.assert_called_once()

def test_get_user_training_status():
    db = MagicMock()
    # Mock return values
    mock_completions = [
        TrainingCompletionModel(course_code="TEST-101"),
        TrainingCompletionModel(course_code="TEST-102")
    ]
    db.query.return_value.filter.return_value.all.return_value = mock_completions
    
    provider = TrainingProvider(db)
    codes = provider.get_user_training_status("test@example.com")
    
    assert len(codes) == 2
    assert "TEST-101" in codes
    assert "TEST-102" in codes
