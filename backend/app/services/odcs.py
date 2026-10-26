"""Tolerant readers for parsed ODCS data contract YAML.

Contracts are written by hand, so any level can be null, a scalar or the wrong
container: ``schema:`` with an empty ``-`` item loads as ``[None]``, and older
contracts map names to objects instead of listing them. These return only the
entries that are objects, so one malformed contract can't break a caller that
walks every contract.
"""
from typing import Any, Dict, Iterable, List, Optional

import yaml

from app.core.config import get_scan_catalogs

# Never described to users when SCAN_CATALOGS is blank: platform catalogs, not data
# anyone governs here.
UNGOVERNED_CATALOGS = frozenset({"system", "samples", "__databricks_internal"})

LINEAGE_PROPERTIES = ("upstream_tables", "downstream_tables")


def _objects(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def contract_servers(contract: Any) -> List[Dict[str, Any]]:
    """The contract's ``servers`` entries that are objects."""
    return _objects(contract.get("servers")) if isinstance(contract, dict) else []


def contract_schema_objects(contract: Any) -> List[Dict[str, Any]]:
    """The contract's ``schema`` entries (its tables) that are objects."""
    return _objects(contract.get("schema")) if isinstance(contract, dict) else []


def field_text(value: Any) -> str:
    """A contract field as a stripped string; ``""`` when missing or not a string."""
    return value.strip() if isinstance(value, str) else ""


def _norm_catalog(name: Any) -> str:
    return str(name or "").replace("`", "").strip().lower()


def catalog_in_scope(catalog: Any) -> bool:
    """Whether contracts may describe objects in ``catalog``.

    The ``SCAN_CATALOGS`` allowlist when set; otherwise every catalog except
    :data:`UNGOVERNED_CATALOGS`. Read at call time so an Admin -> Settings change
    applies at once.
    """
    name = _norm_catalog(catalog)
    if not name:
        return False
    allowlist = {_norm_catalog(c) for c in get_scan_catalogs()}
    if allowlist:
        return name in allowlist
    return name not in UNGOVERNED_CATALOGS


def table_in_scope(full_name: Any, catalog: Optional[str] = None) -> bool:
    """Whether a ``catalog.schema.table`` name is in scope.

    ``catalog`` overrides the name's first part (lineage entries carry it
    separately). A name whose catalog can't be determined is out of scope.
    """
    if catalog:
        return catalog_in_scope(catalog)
    if not isinstance(full_name, str):
        return False
    parts = full_name.replace("`", "").strip().split(".")
    return len(parts) == 3 and all(parts) and catalog_in_scope(parts[0])


def scoped_table_names(names: Iterable[Any]) -> List[str]:
    """``names`` minus the ones outside the governed catalogs."""
    return [n for n in names if isinstance(n, str) and table_in_scope(n)]


def scope_contract_lineage(contract: Any) -> bool:
    """Drop out-of-scope tables from each table's lineage custom properties, in place.

    A comma-separated string value becomes a list. Returns whether anything changed.
    """
    changed = False
    for table_def in contract_schema_objects(contract):
        for prop in _objects(table_def.get("customProperties")):
            if field_text(prop.get("property")).lower() not in LINEAGE_PROPERTIES:
                continue
            value = prop.get("value")
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            elif not isinstance(value, list):
                continue
            kept = scoped_table_names(value)
            if kept != prop.get("value"):
                prop["value"] = kept
                changed = True
    return changed


def scope_contract_lineage_yaml(yaml_content: Optional[str]) -> Optional[str]:
    """:func:`scope_contract_lineage` for contract YAML text.

    The text comes back untouched unless the lineage changed, so the usual case
    keeps its formatting and comments.
    """
    if not yaml_content or not any(p in yaml_content for p in LINEAGE_PROPERTIES):
        return yaml_content
    try:
        contract = yaml.safe_load(yaml_content)
    except yaml.YAMLError:
        return yaml_content
    if not scope_contract_lineage(contract):
        return yaml_content
    return yaml.dump(contract, sort_keys=False, default_flow_style=False)
