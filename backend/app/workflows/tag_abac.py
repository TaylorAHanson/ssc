"""Which tag changes would alter an ABAC policy's effect.

Unity Catalog attribute-based access control (ABAC) policies — column masks and
row filters — choose what they apply to with tag conditions: ``has_tag('pii')``
or ``has_tag_value('pii', 'ssn')`` in a policy's column matches (column tags)
or its ``when`` condition (table tags). Adding, changing or removing a tag a
policy keys on silently changes who sees what, so the preview flags it.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.workflows.tag_plan import TagPlan, _normalize_fqn

logger = logging.getLogger(__name__)

# Tables whose policies are read per change; more than this is checked partially
# and reported as unchecked rather than making the preview crawl.
MAX_TABLES = 200
_WORKERS = 8

_TAG_FN = re.compile(
    r"has_tag(?:_value)?\s*\(\s*'((?:[^'\\]|\\.)*)'\s*(?:,\s*'((?:[^'\\]|\\.)*)'\s*)?\)",
    re.IGNORECASE,
)

_POLICY_KIND = {
    "POLICY_TYPE_COLUMN_MASK": "column mask",
    "POLICY_TYPE_ROW_FILTER": "row filter",
}


@dataclass(frozen=True)
class TagReference:
    """One tag condition inside one ABAC policy."""

    policy: str
    kind: str  # "column mask" | "row filter" | "policy"
    defined_on: str
    key: str
    value: Optional[str]  # None: any value (has_tag)
    on_columns: bool  # True: a column match; False: the policy's table condition

    def matches(self, key: str, before: Optional[str], after: Optional[str]) -> bool:
        if self.key.lower() != key.lower():
            return False
        return self.value is None or self.value in (before, after)


@dataclass
class AbacReferences:
    by_table: Dict[str, List[TagReference]] = field(default_factory=dict)
    # Tables whose policies couldn't be read (usually missing READ METADATA).
    unchecked: List[str] = field(default_factory=list)


def parse_tag_conditions(condition: Optional[str]) -> List[Tuple[str, Optional[str]]]:
    """``(key, value)`` pairs referenced by ``has_tag`` / ``has_tag_value`` calls."""
    return [(k, v if v else None) for k, v in _TAG_FN.findall(condition or "")]


def _references(policy) -> List[TagReference]:
    kind = _POLICY_KIND.get(str(getattr(policy, "policy_type", "") or "").split(".")[-1], "policy")
    common = dict(
        policy=getattr(policy, "name", "") or "(unnamed)",
        kind=kind,
        defined_on=getattr(policy, "on_securable_fullname", "") or "",
    )
    refs: List[TagReference] = []
    for match in getattr(policy, "match_columns", None) or []:
        for key, value in parse_tag_conditions(getattr(match, "condition", None)):
            refs.append(TagReference(key=key, value=value, on_columns=True, **common))
    for key, value in parse_tag_conditions(getattr(policy, "when_condition", None)):
        refs.append(TagReference(key=key, value=value, on_columns=False, **common))
    return refs


def fetch_abac_references(provider, tables: List[str]) -> AbacReferences:
    """Tag conditions of every ABAC policy that applies to each table (incl. inherited)."""
    result = AbacReferences()
    unique = sorted({_normalize_fqn(t): t for t in tables}.items())
    checked, skipped = unique[:MAX_TABLES], unique[MAX_TABLES:]
    result.unchecked.extend(name for _, name in skipped)

    def load(item):
        norm, name = item
        try:
            policies = list(provider.client.policies.list_policies("table", name, include_inherited=True))
        except Exception as e:  # noqa: BLE001 - advisory; reported as unchecked
            logger.info(f"Could not read ABAC policies for {name}: {e}")
            return norm, name, None
        return norm, name, [r for p in policies for r in _references(p)]

    if checked:
        with ThreadPoolExecutor(max_workers=min(_WORKERS, len(checked))) as pool:
            for norm, name, refs in pool.map(load, checked):
                if refs is None:
                    result.unchecked.append(name)
                elif refs:
                    result.by_table[norm] = refs
    return result


def abac_impacts(plan: TagPlan, references: AbacReferences) -> List[str]:
    """One line per changed tag that an applicable ABAC policy keys on."""
    impacts: List[str] = []
    for diff in plan.diffs.values():
        refs = references.by_table.get(_normalize_fqn(diff.table)) or []
        if not refs:
            continue
        for key in diff.changed_keys:
            for ref in refs:
                if ref.on_columns != bool(diff.column):
                    continue
                if ref.matches(key, diff.before.get(key), diff.after.get(key)):
                    impacts.append(
                        f"`{diff.label}`.`{key}`: {ref.kind} '{ref.policy}'"
                        + (f" (on {ref.defined_on})" if ref.defined_on else "")
                    )
    return sorted(set(impacts))
