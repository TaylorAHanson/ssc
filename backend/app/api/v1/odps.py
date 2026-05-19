import logging
from typing import List, Optional
import uuid
import yaml
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.db.odps import OdpsModel
from app.api.deps import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

class OdpsResponse(BaseModel):
    id: str
    name: str
    yaml_content: str
    version: int
    is_active: bool
    created_at: datetime
    created_by: Optional[str]

    model_config = {"from_attributes": True}

class OdpsDraftRequest(BaseModel):
    dataset_ids: List[str]
    openapi_urls: Optional[List[str]] = None
    name: str

class OdpsCreate(BaseModel):
    name: str
    yaml_content: str

@router.get("", response_model=List[OdpsResponse])
@router.get("/", response_model=List[OdpsResponse])
def list_odps(db: Session = Depends(get_db)):
    """List all active ODPS documents."""
    return db.query(OdpsModel).filter(OdpsModel.is_active == True).all()

@router.get("/{odps_id}", response_model=List[OdpsResponse])
def get_odps_history(odps_id: str, db: Session = Depends(get_db)):
    """Get version history for a specific ODPS document by ID."""
    return db.query(OdpsModel).filter(
        OdpsModel.id == odps_id
    ).order_by(OdpsModel.version.desc()).all()

@router.post("", response_model=OdpsResponse)
@router.post("/", response_model=OdpsResponse)
def save_odps(odps: OdpsCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Save a new version of an ODPS document."""
    # Validate YAML
    try:
        yaml.safe_load(odps.yaml_content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(e)}")

    # Find the latest version by name
    latest = db.query(OdpsModel).filter(
        OdpsModel.name == odps.name
    ).order_by(OdpsModel.version.desc()).first()

    new_version = (latest.version + 1) if latest else 1
    new_id = latest.id if latest else str(uuid.uuid4())

    # Deactivate the old version
    if latest:
        latest.is_active = False
        db.add(latest)

    new_odps = OdpsModel(
        id=new_id,
        name=odps.name,
        yaml_content=odps.yaml_content,
        version=new_version,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        created_by=current_user.email
    )

    db.add(new_odps)
    db.commit()
    db.refresh(new_odps)
    return new_odps

@router.post("/draft")
async def draft_odps(
    request: OdpsDraftRequest, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Draft a new ODPS document using LLM based on existing Data Contracts and optional OpenAPI spec."""
    from app.tools.governance.draft_odps import draft_odps_document
    
    try:
        # Pass the dataset IDs to the tool to fetch ODCS YAMLs
        from app.db.data_contract import DataContractModel
        odcs_docs = db.query(DataContractModel).filter(
            DataContractModel.dataset_id.in_(request.dataset_ids), 
            DataContractModel.is_active == True
        ).all()
        odcs_yamls = [doc.yaml_content for doc in odcs_docs]
        
        odps_yaml = await draft_odps_document._func(
            odcs_yamls=odcs_yamls,
            openapi_urls=request.openapi_urls,
            product_name=request.name
        )
        
        return {"status": "success", "yaml_content": odps_yaml}
    except Exception as e:
        logger.error(f"Failed to draft ODPS: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{odps_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_odps(
    odps_id: str, 
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete all versions of an ODPS document."""
    if not current_user.has_role("platform_admin") and not current_user.has_role("governance_admin"):
        raise HTTPException(status_code=403, detail="Not authorized to delete ODPS documents")
        
    docs = db.query(OdpsModel).filter(OdpsModel.id == odps_id).all()
    
    if not docs:
        raise HTTPException(status_code=404, detail="ODPS document not found")
        
    for doc in docs:
        db.delete(doc)
        
    db.commit()
    return None
