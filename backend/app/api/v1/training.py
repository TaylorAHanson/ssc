from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.db.session import get_db
from app.api.deps import get_current_user
from app.db.user import UserModel
from app.providers.training.client import TrainingProvider
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/me", response_model=Dict[str, Any])
async def get_my_training(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get training tracks with status for the current user.
    """
    provider = TrainingProvider(db)
    
    # Get all tracks
    tracks = provider.get_all_tracks()
    
    # Get user completion status
    completed_codes = provider.get_user_training_status(current_user.email)
    
    # Return both (frontend will merge, or we returns completed_codes separately)
    return {
        "tracks": tracks,
        "completed_codes": completed_codes
    }

@router.post("/upload", response_model=Dict[str, Any])
async def upload_training_data(
    file: UploadFile = File(...),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload training completion CSV.
    Only available to admins.
    """
    if not current_user.has_role("platform_admin"):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
        
    try:
        content = await file.read()
        content_str = content.decode('utf-8')
        
        provider = TrainingProvider(db)
        stats = provider.ingest_training_csv(content_str)
        
        return {
            "message": "Training data processed successfully",
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error processing upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
