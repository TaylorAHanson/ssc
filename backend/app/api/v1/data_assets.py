from typing import List, Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from app.db.session import get_lakebase_session
from app.db.data_asset import DataAssetModel
from datetime import datetime
import json

router = APIRouter()

class DataQualitySchema(BaseModel):
    freshness: Optional[str] = None
    completeness: Optional[str] = None
    accuracy: Optional[str] = None

class DataAssetResponse(BaseModel):
    id: str
    catalog: str
    schema_name: str
    table_name: str
    type: str
    description: Optional[str] = None
    owner: Optional[str] = None
    domain: Optional[str] = None
    tags: List[str] = []
    certified: bool = False
    contract_url: Optional[str] = None
    data_quality: Optional[dict] = None
    certification_violations: Optional[List[str]] = None
    sla: Optional[str] = None
    created_at: Optional[datetime] = None
    last_synced_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

@router.get("", response_model=List[DataAssetResponse])
@router.get("/", response_model=List[DataAssetResponse])
def list_data_assets(
    domain: Optional[str] = None,
    certified: Optional[bool] = None,
    db: Session = Depends(get_lakebase_session)
):
    """
    List cached data assets for discovery.
    """
    query = db.query(DataAssetModel)
    
    if domain:
        query = query.filter(DataAssetModel.domain == domain)
        
    if certified is not None:
        query = query.filter(DataAssetModel.certified == certified)
        
    assets = query.all()
    
    # Map 'schema' column to 'schema_name' for the Pydantic model since 'schema' is a reserved field name in BaseModel in pydantic sometimes,
    # actually let's just construct the response properly.
    result = []
    for asset in assets:
        result.append({
            "id": asset.id,
            "catalog": asset.catalog,
            "schema_name": asset.schema,
            "table_name": asset.table_name,
            "type": asset.type,
            "description": asset.description,
            "owner": asset.owner,
            "domain": asset.domain,
            "tags": asset.tags if asset.tags else [],
            "certified": asset.certified,
            "contract_url": asset.contract_url,
            "data_quality": asset.data_quality,
            "certification_violations": asset.certification_violations if isinstance(asset.certification_violations, list) else (json.loads(asset.certification_violations) if isinstance(asset.certification_violations, str) else []),
            "sla": asset.sla,
            "created_at": asset.created_at,
            "last_synced_at": asset.last_synced_at
        })
        
    return result

@router.post("/sync")
async def trigger_data_sync():
    """
    Manually trigger a force sync of data assets from Databricks.
    """
    from app.workers.tasks.sync_data_assets import sync_data_assets_task
    try:
        await sync_data_assets_task(force=True)
        return {"status": "success", "message": "Data assets sync completed successfully."}
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Manual sync failed: {e}", exc_info=True)
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))
