import logging
from typing import List, Optional
import uuid
import yaml

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime

from app.db.session import get_db
from app.db.data_contract import DataContractModel
from app.db.data_asset import DataAssetModel
from pydantic import BaseModel
from app.api.deps import get_current_user

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
        from_attributes = True

class DataContractCreate(BaseModel):
    dataset_id: str
    yaml_content: str

@router.post("/sync")
async def sync_contracts(
    background_tasks: BackgroundTasks,
    force: bool = False,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.tools.governance.draft_odcs import draft_odcs_contract
    from app.tools.execute_workflow import execute_workflow
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    
    try:
        # 1. Query Databricks for tables with the 'dataset' tag
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        # Fetch all catalogs the SP has access to
        catalogs = provider.client.catalogs.list()
        
        dataset_groups = {}
        
        for catalog in catalogs:
            if catalog.name in ("system", "samples"):
                continue
                
            # Query the local information_schema for each catalog
            query = f"SELECT catalog_name, schema_name, table_name, tag_value FROM {catalog.name}.information_schema.table_tags WHERE tag_name = 'dataset'"
            logger.info(f"Querying information_schema for catalog {catalog.name}")
            
            try:
                response = provider.client.statement_execution.execute_statement(
                    statement=query,
                    warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout="30s"
                )
                
                if response.result and response.result.data_array:
                    logger.info(f"Found {len(response.result.data_array)} tagged tables in catalog {catalog.name}")
                    for row in response.result.data_array:
                        catalog_name, schema_name, table_name, dataset_name = row
                        full_name = f"{catalog_name}.{schema_name}.{table_name}"
                        if dataset_name not in dataset_groups:
                            dataset_groups[dataset_name] = []
                        dataset_groups[dataset_name].append(full_name)
                else:
                    logger.debug(f"No tables found with 'dataset' tag in catalog {catalog.name}")
            except Exception as e:
                logger.warning(f"Could not query information_schema for catalog {catalog.name}: {e}")
                
        logger.info(f"Discovery complete. Found {len(dataset_groups)} unique data sets across all catalogs.")
        
        if not dataset_groups:
            return {"status": "success", "message": "No tables found with 'dataset' tag."}

        background_tasks.add_task(run_sync_contracts_background, dataset_groups, force)
        return {"status": "success", "message": f"Sync started in the background for {len(dataset_groups)} data sets. This may take a few minutes."}
        
    except Exception as e:
        logger.error(f"Failed to sync contracts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def run_sync_contracts_background(dataset_groups: dict, force: bool):
    from app.db.session import get_lakebase_session
    import hashlib
    import json
    from app.tools.governance.draft_odcs import fetch_datasets_metadata
    from app.tools.governance.draft_odcs import draft_odcs_contract
    
    db = get_lakebase_session()
    try:
        requests_created = 0

        for dataset_name, table_ids in dataset_groups.items():
            # Check if we have an existing contract
            existing_contract = db.query(DataContractModel).filter(
                DataContractModel.dataset_id == dataset_name,
                DataContractModel.is_active == True
            ).first()
            
            # Fetch current metadata for the tables
            try:
                datasets_metadata = await fetch_datasets_metadata(table_ids)
            except Exception as e:
                logger.error(f"Failed to fetch metadata for {dataset_name}: {e}")
                continue
            
            # Update the DataAsset catalog text (e.g. "3 tables, 1 view") unconditionally
            asset = db.query(DataAssetModel).filter(DataAssetModel.id == dataset_name).first()
            
            tables_count = sum(1 for m in datasets_metadata if "VIEW" not in str(m.get("table_type", "")).upper())
            views_count = sum(1 for m in datasets_metadata if "VIEW" in str(m.get("table_type", "")).upper())
            
            parts = []
            if tables_count > 0:
                parts.append(f"{tables_count} table{'s' if tables_count > 1 else ''}")
            if views_count > 0:
                parts.append(f"{views_count} view{'s' if views_count > 1 else ''}")
            
            summary_str = ", ".join(parts) if parts else "Multiple datasets"
            
            if asset:
                asset.contract_url = f"/governance/certification?dataset={dataset_name}"
                if asset.catalog == "multiple":
                    asset.catalog = summary_str
                    asset.schema = ""
                db.add(asset)
            else:
                asset = DataAssetModel(
                    id=dataset_name,
                    catalog=summary_str,
                    schema="",
                    table_name=dataset_name,
                    type="DATA_PRODUCT",
                    description=f"Data contract for {dataset_name}",
                    domain="unknown",
                    contract_url=f"/governance/certification?dataset={dataset_name}"
                )
                db.add(asset)
            db.commit()
            
            # Calculate metadata hash
            metadata_hash = hashlib.md5(json.dumps(datasets_metadata, sort_keys=True).encode()).hexdigest()
            
            if existing_contract and hasattr(existing_contract, 'metadata_hash') and existing_contract.metadata_hash == metadata_hash and not force:
                logger.info(f"No metadata changes detected for {dataset_name}, skipping LLM call.")
                continue
            
            existing_yaml = existing_contract.yaml_content if existing_contract else None
            
            # Draft or update the ODCS
            try:
                logger.info(f"Starting LLM ODCS drafting for {dataset_name} (tables: {table_ids})...")
                odcs_yaml = await draft_odcs_contract._func(
                    dataset_ids=table_ids, 
                    existing_odcs_yaml=existing_yaml,
                    pre_fetched_metadata=datasets_metadata
                )
                logger.info(f"Completed LLM ODCS drafting for {dataset_name}. Received {len(odcs_yaml) if odcs_yaml else 0} chars.")
            except Exception as e:
                logger.error(f"Error drafting ODCS for dataset {dataset_name}: {e}")
                continue
                
            if not odcs_yaml or "apiVersion:" not in odcs_yaml or "kind: DataContract" not in odcs_yaml:
                logger.error(f"LLM returned an invalid ODCS format for {dataset_name}. It may have timed out or hit rate limits.")
                continue
            
            # Check if the contract actually changed
            if existing_yaml and existing_yaml.strip() == odcs_yaml.strip():
                logger.info(f"No YAML changes detected for contract {dataset_name}, skipping update.")
                if hasattr(existing_contract, 'metadata_hash'):
                    existing_contract.metadata_hash = metadata_hash
                    db.commit()
                continue
            
            # Save directly to database
            new_version = (existing_contract.version + 1) if existing_contract else 1
            if existing_contract:
                existing_contract.is_active = False
                db.add(existing_contract)
                
            new_contract = DataContractModel(
                id=str(uuid.uuid4()),
                dataset_id=dataset_name,
                yaml_content=odcs_yaml,
                version=new_version,
                is_active=True,
                created_at=datetime.utcnow(),
                metadata_hash=metadata_hash
            )
            db.add(new_contract)
            
            db.commit()
            requests_created += 1

        logger.info(f"Background sync complete. Synced {len(dataset_groups)} data sets. Updated {requests_created} data contracts.")
        
    except Exception as e:
        logger.error(f"Background sync failed: {e}")
    finally:
        db.close()

@router.get("", response_model=List[DataContractResponse])
@router.get("/", response_model=List[DataContractResponse])
def list_contracts(db: Session = Depends(get_db)):
    """List all active data contracts."""
    return db.query(DataContractModel).filter(DataContractModel.is_active == True).all()

@router.get("/{dataset_id}", response_model=List[DataContractResponse])
def get_contract_history(dataset_id: str, db: Session = Depends(get_db)):
    """Get the version history for a specific dataset contract."""
    return db.query(DataContractModel).filter(
        DataContractModel.dataset_id == dataset_id
    ).order_by(DataContractModel.version.desc()).all()

@router.post("", response_model=DataContractResponse)
@router.post("/", response_model=DataContractResponse)
def create_contract(contract: DataContractCreate, db: Session = Depends(get_db)):
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

@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract(
    dataset_id: str, 
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete all versions of a data contract and unset the asset contract_url."""
    # Check permissions - usually only admins or owners should delete contracts
    if not current_user.has_role("platform_admin") and not current_user.has_role("governance_admin"):
        raise HTTPException(status_code=403, detail="Not authorized to delete data contracts")
        
    contracts = db.query(DataContractModel).filter(DataContractModel.dataset_id == dataset_id).all()
    asset = db.query(DataAssetModel).filter(DataAssetModel.id == dataset_id).first()
    
    if not contracts and not asset:
        raise HTTPException(status_code=404, detail="Data contract not found")
        
    for contract in contracts:
        db.delete(contract)
        
    # Clear the contract_url from the DataAsset if it exists
    if asset:
        asset.contract_url = None
        asset.certified = False
        db.add(asset)
        
    db.commit()
    return None
