import logging
from typing import List, Optional
import uuid
import yaml

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime

from app.db.session import get_lakebase_session
from app.db.data_contract import DataContractModel
from app.db.data_asset import DataAssetModel
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

class DataContractResponse(BaseModel):
    id: str
    dataset_id: str
    yaml_content: str
    version: int
    is_active: bool
    created_at: datetime
    created_by: Optional[str]

    class Config:
        orm_mode = True

class DataContractCreate(BaseModel):
    dataset_id: str
    yaml_content: str

@router.get("", response_model=List[DataContractResponse])
@router.get("/", response_model=List[DataContractResponse])
def list_contracts(db: Session = Depends(get_lakebase_session)):
    """List all active data contracts."""
    return db.query(DataContractModel).filter(DataContractModel.is_active == True).all()

@router.get("/{dataset_id}", response_model=List[DataContractResponse])
def get_contract_history(dataset_id: str, db: Session = Depends(get_lakebase_session)):
    """Get the version history for a specific dataset contract."""
    return db.query(DataContractModel).filter(
        DataContractModel.dataset_id == dataset_id
    ).order_by(DataContractModel.version.desc()).all()

@router.post("", response_model=DataContractResponse)
@router.post("/", response_model=DataContractResponse)
def create_contract(contract: DataContractCreate, db: Session = Depends(get_lakebase_session)):
    """Create a new version of a data contract."""
    # Validate YAML
    try:
        yaml.safe_load(contract.yaml_content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(e)}")

    # Find the latest version
    latest = db.query(DataContractModel).filter(
        DataContractModel.dataset_id == contract.dataset_id
    ).order_by(DataContractModel.version.desc()).first()

    new_version = (latest.version + 1) if latest else 1

    # Deactivate the old version
    if latest:
        latest.is_active = False
        db.add(latest)

    new_contract = DataContractModel(
        id=str(uuid.uuid4()),
        dataset_id=contract.dataset_id,
        yaml_content=contract.yaml_content,
        version=new_version,
        is_active=True,
        created_at=datetime.utcnow()
        # created_by could be pulled from auth context if needed
    )

    db.add(new_contract)
    
    # Also update the DataAsset to mark it as having a contract
    asset = db.query(DataAssetModel).filter(DataAssetModel.id == contract.dataset_id).first()
    if asset:
        asset.contract_url = f"/governance/certification?dataset={contract.dataset_id}"
        db.add(asset)
    else:
        # Create a mock asset so it shows up in the UI right away
        parts = contract.dataset_id.split(".")
        catalog = parts[0]
        schema = parts[1] if len(parts) > 1 else "default"
        table = parts[2] if len(parts) > 2 else "unknown"
        
        # Parse yaml to get some info
        try:
            parsed = yaml.safe_load(contract.yaml_content)
            description = parsed.get("description", {}).get("purpose", f"Data contract for {contract.dataset_id}")
            domain = parsed.get("domain", "unknown")
        except:
            description = f"Data contract for {contract.dataset_id}"
            domain = "unknown"

        asset = DataAssetModel(
            id=contract.dataset_id,
            catalog=catalog,
            schema=schema,
            table_name=table,
            type="TABLE",
            description=description,
            domain=domain,
            contract_url=f"/governance/certification?dataset={contract.dataset_id}"
        )
        db.add(asset)

    db.commit()
    db.refresh(new_contract)
    return new_contract
