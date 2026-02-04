import csv
import io
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.training import TrainingCompletionModel
from app.agents.content_registry import get_content

logger = logging.getLogger(__name__)

class TrainingProvider:
    """
    Provider for managing training completion data.
    """
    
    def __init__(self, db: Session):
        self.db = db

    def get_user_training_status(self, user_email: str) -> List[str]:
        """
        Get list of completed course codes for a user.
        """
        completions = self.db.query(TrainingCompletionModel.course_code).filter(
            TrainingCompletionModel.user_email == user_email,
            TrainingCompletionModel.status == "completed"
        ).all()
        
        return [c.course_code for c in completions]

    def get_all_tracks(self) -> List[Dict[str, Any]]:
        """
        Get all training tracks from content registry.
        """
        return get_content("training.json")

    def ingest_training_csv(self, file_content: str) -> Dict[str, int]:
        """
        Ingest training completion data from CSV.
        Expected columns: updated_learner_email, Course_Name, Course_Code, completed_timestamp, Status
        """
        reader = csv.DictReader(io.StringIO(file_content))
        
        stats = {
            "processed": 0,
            "added": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0
        }
        
        for row in reader:
            try:
                stats["processed"] += 1
                
                # Extract fields with fallbacks
                email = row.get("updated_learner_email") or row.get("learner_email")
                if not email:
                    # Try to parse from first column if headers are wonky or just empty
                    stats["skipped"] += 1
                    continue
                    
                course_code = row.get("Course_Code")
                if not course_code:
                    stats["skipped"] += 1
                    continue
                    
                status = row.get("Status", "completed").lower()
                
                # Parse timestamp
                completed_at = None
                ts_str = row.get("completed_timestamp")
                if ts_str:
                    try:
                        # Try common formats
                        # Example: 2025-10-28 06:44:29
                        completed_at = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        try:
                            completed_at = datetime.strptime(ts_str, "%Y-%m-%d")
                        except ValueError:
                            pass
                
                # Check for existing record
                existing = self.db.query(TrainingCompletionModel).filter(
                    TrainingCompletionModel.user_email == email,
                    TrainingCompletionModel.course_code == course_code
                ).first()
                
                if existing:
                    # Update if changed
                    changed = False
                    if existing.status != status:
                        # logger.info(f"Status change for {email} {course_code}: {existing.status} -> {status}")
                        existing.status = status
                        changed = True
                    if completed_at and existing.completed_at != completed_at:
                        # logger.info(f"Date change for {email} {course_code}: {existing.completed_at} -> {completed_at}")
                        existing.completed_at = completed_at
                        changed = True
                        
                    if changed:
                        logger.info(f"Updated record for {email} - {course_code}. Status: {existing.status}, Date: {existing.completed_at}")
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1
                else:
                    # Create new
                    new_record = TrainingCompletionModel(
                        user_email=email,
                        course_name=row.get("Course_Name"),
                        course_code=course_code,
                        completed_at=completed_at,
                        status=status
                    )
                    self.db.add(new_record)
                    stats["added"] += 1
                    
            except Exception as e:
                logger.error(f"Error processing row {row}: {e}")
                stats["errors"] += 1
                
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error committing training data: {e}")
            raise e
            
        return stats

    def get_training_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top users by number of completed trainings.
        """
        from sqlalchemy import func, desc
        
        results = self.db.query(
            TrainingCompletionModel.user_email,
            func.count(TrainingCompletionModel.course_code).label('count')
        ).filter(
            TrainingCompletionModel.status == 'completed'
        ).group_by(
            TrainingCompletionModel.user_email
        ).order_by(
            desc('count')
        ).limit(limit).all()
        
        return [{"email": r.user_email, "count": r.count} for r in results]

    def get_recent_completions(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get completions in the last N days.
        """
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(days=days)
        
        results = self.db.query(TrainingCompletionModel).filter(
            TrainingCompletionModel.status == 'completed',
            TrainingCompletionModel.completed_at >= cutoff
        ).order_by(
            TrainingCompletionModel.completed_at.desc()
        ).all()
        
        return [{
            "email": r.user_email, 
            "course": r.course_name or r.course_code,
            "completed_at": r.completed_at.strftime("%Y-%m-%d") if r.completed_at else "Unknown"
        } for r in results]
