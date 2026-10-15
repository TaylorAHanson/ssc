"""Tolerant readers for parsed ODCS data contract YAML.

Contracts are written by hand, so any level can be null, a scalar or the wrong
container: ``schema:`` with an empty ``-`` item loads as ``[None]``, and older
contracts map names to objects instead of listing them. These return only the
entries that are objects, so one malformed contract can't break a caller that
walks every contract.
"""
from typing import Any, Dict, List


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
