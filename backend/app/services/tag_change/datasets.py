"""Governed datasets: which tables belong to one, and which datasets exist.

A dataset is a logical group of tables declared by an active data contract
(ODCS) and/or by tagging each table ``dataset=<id>`` in Unity Catalog.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.data_contract import DataContractModel

logger = logging.getLogger(__name__)


@dataclass
class DatasetSummary:
    dataset_id: str
    catalog: Optional[str] = None
    schema_name: Optional[str] = None


def extract_contract_info(contract: DataContractModel) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Extract default catalog, schema, and declared member tables from a Data Contract YAML."""
    if not contract or not contract.yaml_content:
        return None, None, []
    try:
        data = yaml.safe_load(contract.yaml_content) or {}
    except Exception as e:
        logger.warning(f"Could not parse contract YAML for {contract.dataset_id}: {e}")
        return None, None, []

    servers = data.get("servers", [])
    default_catalog = servers[0].get("catalog", "") if servers else ""
    default_schema = servers[0].get("schema", "") if servers else ""

    tables: List[str] = []
    schemas = data.get("schema", [])
    for s in schemas:
        physical_table = s.get("physicalName") or s.get("name")
        if not physical_table:
            continue
        table_catalog = s.get("catalog") or default_catalog
        table_schema = s.get("schema") or default_schema
        if "." in physical_table:
            parts = physical_table.split(".")
            if len(parts) == 3:
                tables.append(physical_table)
            elif len(parts) == 2 and table_catalog:
                tables.append(f"{table_catalog}.{physical_table}")
            elif table_catalog and table_schema:
                tables.append(f"{table_catalog}.{table_schema}.{physical_table}")
        elif table_catalog and table_schema:
            tables.append(f"{table_catalog}.{table_schema}.{physical_table}")

    return (default_catalog or None), (default_schema or None), tables


def list_datasets(db: Session) -> List[DatasetSummary]:
    """Datasets with an active data contract, sorted by id."""
    contracts = (
        db.query(DataContractModel)
        .filter(DataContractModel.is_active == True)  # noqa: E712
        .order_by(DataContractModel.version.desc())
        .all()
    )
    results: List[DatasetSummary] = []
    seen: Set[str] = set()
    for c in contracts:
        if c.dataset_id in seen:
            continue
        seen.add(c.dataset_id)
        catalog, schema_name, _ = extract_contract_info(c)
        if not catalog or not schema_name:
            parts = (c.dataset_id or "").split(".")
            if len(parts) >= 2:
                catalog = parts[0]
                schema_name = parts[1]
            elif len(parts) == 1 and not catalog:
                catalog = None
                schema_name = None
        results.append(DatasetSummary(dataset_id=c.dataset_id, catalog=catalog, schema_name=schema_name))
    results.sort(key=lambda d: d.dataset_id.lower())
    return results


def discover_dataset_tables(provider, dataset_id: str, db: Optional[Session] = None) -> List[str]:
    """
    Find the member tables of a dataset.
    Discovers tables declared in active data contracts (ODCS) as well as tables
    tagged with dataset='<dataset_id>' in Unity Catalog.
    """
    from app.core.workspaces import catalogs_to_scan

    discovered: Set[str] = set()

    # 1. Look up tables declared in DataContractModel (ODCS)
    if db:
        contract = (
            db.query(DataContractModel)
            .filter(DataContractModel.dataset_id == dataset_id, DataContractModel.is_active == True)  # noqa: E712
            .order_by(DataContractModel.version.desc())
            .first()
        )
        if contract:
            _, _, contract_tables = extract_contract_info(contract)
            for t in contract_tables:
                discovered.add(t)

    # 2. Query live Unity Catalog for any tables tagged with dataset='<dataset_id>'
    if provider:
        catalog_names, _missing = catalogs_to_scan(provider.client)
        safe_id = dataset_id.replace("'", "''")
        found = _tagged_members_metastore_wide(provider, catalog_names, safe_id)
        if found is not None:
            return sorted(discovered | found)
        for catalog_name in catalog_names:
            query = (
                f"SELECT catalog_name, schema_name, table_name "
                f"FROM {catalog_name}.information_schema.table_tags "
                f"WHERE (tag_name = 'dataset' OR tag_name = 'data_set') AND tag_value = '{safe_id}'"
            )
            try:
                response = provider.client.statement_execution.execute_statement(
                    statement=query,
                    warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout="30s",
                )
                if response.result and response.result.data_array:
                    for row in response.result.data_array:
                        discovered.add(f"{row[0]}.{row[1]}.{row[2]}")
            except Exception as e:
                logger.warning(f"Could not query information_schema for catalog {catalog_name}: {e}")

    return sorted(discovered)


def _tagged_members_metastore_wide(provider, catalog_names: List[str], safe_id: str) -> Optional[Set[str]]:
    """One ``system.information_schema`` query instead of one per catalog.

    Returns ``None`` if that view can't be read, so the caller falls back to the
    per-catalog queries.
    """
    if not catalog_names:
        return set()
    listed = ", ".join("'" + c.replace("'", "''") + "'" for c in catalog_names)
    query = (
        f"SELECT catalog_name, schema_name, table_name "
        f"FROM system.information_schema.table_tags "
        f"WHERE (tag_name = 'dataset' OR tag_name = 'data_set') AND tag_value = '{safe_id}' "
        f"AND catalog_name IN ({listed})"
    )
    try:
        response = provider.client.statement_execution.execute_statement(
            statement=query,
            warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
            wait_timeout="30s",
        )
    except Exception as e:
        logger.info(f"Metastore-wide dataset lookup unavailable, querying per catalog: {e}")
        return None
    state = getattr(getattr(response, "status", None), "state", None)
    if getattr(state, "value", None) == "FAILED":
        return None
    rows = response.result.data_array if response.result and response.result.data_array else []
    return {f"{r[0]}.{r[1]}.{r[2]}" for r in rows if len(r) >= 3}
