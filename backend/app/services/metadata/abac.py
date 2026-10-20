"""Access-policy (ABAC) impact for a metadata preview.

A column mask or row filter picks what it applies to with tag conditions, so
setting, changing or removing a tag it keys on changes who can see the data.
Policies are read live as the governance service principal, which needs READ
METADATA on the objects. Advisory: a preview never fails on it.
"""
import logging
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.core.config import settings
from app.services.metadata.types import ChangeItem, Finding, ObjectKey

logger = logging.getLogger(__name__)

# More tables or views than this are checked through their schemas: catalog and
# schema policies still count, policies defined on a single table aren't read.
RELATION_LIMIT = 50
TIMEOUT_SECONDS = 10.0
_SHOWN = 5
_CONTAINERS = ("catalog", "schema")


def _provider():
    if not settings.METADATA_ABAC_CHECK:
        return None
    try:
        from app.core.workspaces import get_governance_uc_provider

        return get_governance_uc_provider()
    except Exception as exc:  # noqa: BLE001 - advisory
        logger.info("Metadata: access policies not checked, no governance provider: %s", exc)
        return None


def _targets(items: Sequence[ChangeItem]) -> Tuple[List[str], Dict[str, str]]:
    """(names to read policies on, table -> the schema read for it when there are too many tables)."""
    containers: Set[str] = {i.name for i in items if i.securable_type in _CONTAINERS}
    relations: Set[str] = {i.name for i in items if i.securable_type not in _CONTAINERS}
    if len(relations) <= RELATION_LIMIT:
        return sorted(containers | relations), {}
    via: Dict[str, str] = {}
    for name in relations:
        key = ObjectKey.parse(name)
        via[name] = f"{key.catalog}.{key.schema_name}"
    return sorted(containers | set(via.values())), via


def _list(names: Sequence[str]) -> str:
    shown = ", ".join(names[:3])
    return f"{shown}, …" if len(names) > 3 else shown


def check(items: Sequence[ChangeItem], plan, *, provider=None) -> Tuple[List[Finding], List[str]]:
    """(findings, impact lines for the risk score) for the tag items of a change."""
    tag_items = [i for i in items if i.field == "tag"]
    if not tag_items or plan is None:
        return [], []
    provider = provider or _provider()
    if provider is None:
        return [], []
    from app.workflows.tag_abac import abac_impacts, fetch_abac_references
    from app.workflows.tag_plan import _normalize_fqn

    try:
        names, via = _targets(tag_items)
        refs = fetch_abac_references(provider, names, timeout=TIMEOUT_SECONDS)
        for table, schema in via.items():
            inherited = refs.by_table.get(_normalize_fqn(schema))
            if inherited:
                refs.by_table[_normalize_fqn(table)] = inherited
        impacts = abac_impacts(plan, refs)
    except Exception as exc:  # noqa: BLE001 - advisory
        logger.info("Metadata: access-policy check failed: %s", exc)
        return [Finding(code="abac_unchecked", severity="info", message=(
            "Couldn't check whether this change affects access policies (column masks or row filters)."
        ))], []

    findings: List[Finding] = []
    if impacts:
        lines = "; ".join(i.replace("`", "") for i in impacts[:_SHOWN])
        more = f"; and {len(impacts) - _SHOWN} more" if len(impacts) > _SHOWN else ""
        findings.append(Finding(code="abac_impact", severity="warn", message=(
            "This change touches tags that access policies (column masks or row filters) use, so it can "
            f"change who can see the data: {lines}{more}."
        )))
    if refs.unchecked:
        n = len(refs.unchecked)
        findings.append(Finding(code="abac_unchecked", severity="info", message=(
            f"Couldn't read the access policies on {n:,} object{'s' if n != 1 else ''} ({_list(refs.unchecked)}), "
            "so masking and row-filter impact wasn't checked there. The governance service principal needs "
            "READ METADATA on them."
        )))
    if via:
        findings.append(Finding(code="abac_schema_level", severity="info", message=(
            f"This change touches {len(via):,} tables or views, so only the access policies on their catalogs "
            "and schemas were checked, not ones defined on a single table."
        )))
    return findings, impacts
