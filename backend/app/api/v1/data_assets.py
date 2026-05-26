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
    certification_only: Optional[bool] = None,
    limit: Optional[int] = None,
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
        
    if certification_only:
        from sqlalchemy import or_
        from app.db.data_contract import DataContractModel
        query = query.filter(
            or_(
                DataAssetModel.contract_url.isnot(None),
                DataAssetModel.certified == True,
                DataAssetModel.data_quality.isnot(None),
                DataAssetModel.id.in_(db.query(DataContractModel.dataset_id))
            )
        )
        
    if offset > 0:
        query = query.offset(offset)
        
    if limit is not None:
        query = query.limit(limit)
        
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
            "certification_violations": asset.certification_violations if isinstance(asset.certification_violations, list) else (json.loads(asset.certification_violations) if isinstance(asset.certification_violations, str) else None),
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

@router.get("/databricks/dashboards")
def get_databricks_dashboards():
    """Fetch available Lakeview dashboards from Databricks."""
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
        dashboards = provider.client.lakeview.list()
        return [{"id": d.dashboard_id, "name": d.display_name, "type": "dashboard", "path": d.parent_path} for d in dashboards]
    except Exception as e:
        logger.error(f"Failed to fetch dashboards: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/jobs")
def get_databricks_jobs():
    """Fetch available jobs from Databricks."""
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
        jobs = provider.client.jobs.list()
        return [{"id": str(j.job_id), "name": j.settings.name, "type": "job", "creator": j.creator_user_name} for j in jobs]
    except Exception as e:
        logger.error(f"Failed to fetch jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/apps")
def get_databricks_apps():
    """Fetch available apps from Databricks."""
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
        apps = provider.client.apps.list()
        return [{"id": a.name, "name": a.name, "type": "app", "creator": a.creator} for a in apps]
    except Exception as e:
        logger.error(f"Failed to fetch apps: {e}")
        # Return empty list if apps aren't supported in this workspace/SDK yet
        return []

@router.get("/databricks/lineage")
def get_databricks_table_lineage(table_name: str):
    """Return immediate (1-hop) upstream/downstream tables for a UC table.

    `table_name` must be a fully qualified name like ``catalog.schema.table``.
    Used by the Discover page Lineage tab to render a click-to-expand graph
    similar to Databricks Catalog Explorer's lineage view.
    """
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging

    logger = logging.getLogger(__name__)

    if not table_name or table_name.count(".") != 2:
        raise HTTPException(
            status_code=400,
            detail="table_name must be a fully qualified name (catalog.schema.table)",
        )

    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
        )
        resp = provider.client.api_client.do(
            "GET",
            f"/api/2.0/lineage-tracking/table-lineage?table_name={table_name}&include_entity_lineage=true",
        ) or {}

        def _extract(entries):
            results = []
            seen = set()
            for entry in entries or []:
                info = entry.get("tableInfo") or {}
                fqn = info.get("name")
                if not fqn or fqn in seen:
                    continue
                seen.add(fqn)
                results.append(
                    {
                        "name": fqn,
                        "catalog_name": info.get("catalog_name"),
                        "schema_name": info.get("schema_name"),
                        "table_name": info.get("table_name"),
                        "table_type": info.get("table_type"),
                    }
                )
            return results

        return {
            "table_name": table_name,
            "upstreams": _extract(resp.get("upstreams")),
            "downstreams": _extract(resp.get("downstreams")),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to fetch lineage for {table_name}: {e}")
        # Return an empty result so the graph just shows the center node.
        return {"table_name": table_name, "upstreams": [], "downstreams": []}


@router.get("/databricks/genie_spaces")
def get_databricks_genie_spaces():
    """Fetch available Genie Spaces from Databricks."""
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
        # Handle potential absence of genie attribute in older SDKs or if not configured
        if hasattr(provider.client, 'genie'):
            spaces = provider.client.genie.list()
            return [{"id": s.id, "name": s.name, "type": "genie_space", "description": s.description} for s in spaces]
        return []
    except Exception as e:
        logger.error(f"Failed to fetch genie spaces: {e}")
        return []
