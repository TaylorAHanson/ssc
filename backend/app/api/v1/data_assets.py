from typing import List, Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from app.db.session import get_db
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
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    List cached data assets for discovery.
    """
    query = db.query(DataAssetModel)
    
    if domain:
        query = query.filter(DataAssetModel.domain == domain)
        
    if certified is not None:
        query = query.filter(DataAssetModel.certified == certified)
        
    assets = query.offset(offset).limit(limit).all()
    
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

@router.get("/databricks/catalogs")
def get_databricks_catalogs():
    """Fetch available catalogs from Databricks Unity Catalog."""
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        catalogs = provider.client.catalogs.list()
        return [{"name": c.name, "comment": c.comment} for c in catalogs]
    except Exception as e:
        logger.error(f"Failed to fetch catalogs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/schemas")
def get_databricks_schemas(catalog: str):
    """Fetch available schemas for a given catalog from Databricks."""
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        schemas = provider.client.schemas.list(catalog_name=catalog)
        return [{"name": s.name, "comment": s.comment} for s in schemas]
    except Exception as e:
        logger.error(f"Failed to fetch schemas for {catalog}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/tables")
def get_databricks_tables(catalog: str, schema: str):
    """Fetch available tables and views for a given catalog and schema from Databricks."""
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        tables = provider.client.tables.list(catalog_name=catalog, schema_name=schema)
        # Filter for actual tables or views (type is often 'MANAGED', 'EXTERNAL', 'VIEW')
        return [{"name": t.name, "type": t.table_type, "comment": t.comment} for t in tables]
    except Exception as e:
        logger.error(f"Failed to fetch tables for {catalog}.{schema}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
