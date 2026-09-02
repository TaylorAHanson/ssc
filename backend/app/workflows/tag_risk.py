"""
Deterministic Risk Scoring for Unity Catalog Tag Changes.

Evaluates a tag change plan and lint findings against a weighted-additive risk model
incorporating access-control impacts, blast radius, removals, overwrites, certified
dataset modifications, suspected typos, and dataset fragmentation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.workflows.tag_lint import (
    CASE_COLLISION,
    NEAR_MISS_KEY,
    NEAR_MISS_VALUE,
    LintFinding,
)
from app.workflows.tag_plan import DATASET_KEY, ObjectDiff, TagPlan, TagVocabulary
from app.workflows.tag_policy import TagPolicy

LOW = "low"
MEDIUM = "medium"
HIGH = "high"
CRITICAL = "critical"

_BAND_EMOJI = {LOW: "🟢", MEDIUM: "🟡", HIGH: "🟠", CRITICAL: "🔴"}

_DEFAULT_WEIGHTS = {
    "access_control_change": 18.0,
    "removal": 10.0,
    "overwrite": 6.0,
    "certified_object": 8.0,
    "blast_radius": 20.0,
    "near_miss_value": 25.0,
    "novel_value": 4.0,
    "unknown_key": 5.0,
    "dataset_fragmentation": 15.0,
}

_DEFAULT_CAPS = {
    "access_control_change": 36.0,
    "removal": 30.0,
    "overwrite": 24.0,
    "certified_object": 24.0,
    "near_miss_value": 50.0,
    "novel_value": 12.0,
    "unknown_key": 10.0,
    "dataset_fragmentation": 30.0,
}

_LABELS = {
    "access_control_change": "Access-control keys changed",
    "removal": "Tags removed",
    "overwrite": "Existing values overwritten",
    "certified_object": "Certified objects touched",
    "blast_radius": "Blast radius",
    "near_miss_value": "Suspected typos",
    "novel_value": "Values not seen elsewhere",
    "unknown_key": "Keys not in the policy",
    "dataset_fragmentation": "Dataset fragmentation",
}


@dataclass(frozen=True)
class RiskFactor:
    name: str
    label: str
    count: int
    contribution: float
    details: Tuple[str, ...] = ()

    @property
    def rounded(self) -> int:
        return int(round(self.contribution))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "count": self.count,
            "contribution": self.rounded,
            "details": list(self.details),
        }


@dataclass
class RiskReport:
    environment: str
    score: int = 0
    band: str = LOW
    multiplier: float = 1.0
    raw_score: float = 0.0
    factors: List[RiskFactor] = field(default_factory=list)
    object_count: int = 0
    statement_count: int = 0
    vocabulary_available: bool = False

    @property
    def emoji(self) -> str:
        return _BAND_EMOJI.get(self.band, "🟢")

    @property
    def contributing(self) -> List[RiskFactor]:
        return [f for f in self.factors if f.contribution > 0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "environment": self.environment,
            "score": self.score,
            "band": self.band,
            "emoji": self.emoji,
            "multiplier": self.multiplier,
            "raw_score": round(self.raw_score, 1),
            "object_count": self.object_count,
            "statement_count": self.statement_count,
            "vocabulary_available": self.vocabulary_available,
            "factors": [f.to_dict() for f in self.contributing],
            "all_factors": [f.to_dict() for f in self.factors],
        }


def _blast_radius(count: int, weight: float, saturation: int = 500) -> float:
    if count <= 0:
        return 0.0
    ceiling = max(saturation, 2)
    scaled = math.log10(1 + count) / math.log10(1 + ceiling)
    return weight * min(1.0, scaled)


def _fragmented_datasets(plan: TagPlan, vocabulary: TagVocabulary, dataset_key: str = DATASET_KEY) -> List[str]:
    if not vocabulary.dataset_members:
        return []

    moving: Dict[str, set] = {}
    for norm_key, diff in plan.diffs.items():
        old = diff.before.get(dataset_key)
        new = diff.after.get(dataset_key)
        if old and new and new != old:
            moving.setdefault(old, set()).add(norm_key)

    fragmented: List[str] = []
    for val, leaving in moving.items():
        members = vocabulary.dataset_members.get(val)
        if not members:
            continue
        staying = {m for m in members if m not in leaving}
        if staying:
            fragmented.append(
                f"Dataset '{val}': {len(leaving)} of {len(members)} table(s) move, {len(staying)} remain"
            )
    return sorted(fragmented)


def calculate_risk_score(
    plan: TagPlan,
    environment: str = "dev",
    findings: Sequence[LintFinding] = (),
    vocabulary: Optional[TagVocabulary] = None,
    policy: Optional[TagPolicy] = None,
    access_control_keys: Tuple[str, ...] = ("access_group", "approver_group"),
    certification_key: str = "system.certification_status",
    certification_value: str = "certified",
    dataset_key: str = DATASET_KEY,
) -> RiskReport:
    """Calculate the deterministic risk score and breakdown for a tag change plan."""
    vocab = vocabulary or TagVocabulary()
    diffs = [d for d in plan.diffs.values() if d.changed_keys]

    report = RiskReport(
        environment=environment,
        object_count=len(diffs),
        statement_count=len(plan.actionable),
        vocabulary_available=vocab.available,
    )

    access_keys = {k.lower() for k in access_control_keys}
    access_changes: List[str] = []
    removals: List[str] = []
    overwrites = 0
    certified: List[str] = []

    for diff in diffs:
        for key in diff.changed_keys:
            if key.lower() in access_keys:
                access_changes.append(f"`{diff.table}`.`{key}`")
        for key in diff.removed_keys:
            removals.append(f"`{diff.table}`.`{key}`")
        overwrites += len(diff.overwritten_keys)
        # Check certification status from all_tags
        cert_val = diff.before.get(certification_key) or ""
        if cert_val.lower() == certification_value.lower():
            certified.append(f"`{diff.table}`")

    # Unknown policy keys
    unknown_keys: List[str] = []
    if policy:
        unk_set = set()
        for diff in diffs:
            for key in diff.changed_keys:
                if key not in policy.known_keys and not policy.is_reserved(key):
                    unk_set.add(key)
        unknown_keys = [f"`{k}`" for k in sorted(unk_set)]

    # Novel values
    novel_values: List[str] = []
    if vocab.available:
        novel_set = set()
        for diff in diffs:
            for key in diff.changed_keys:
                val = diff.after.get(key)
                if val is not None and vocab.is_novel(key, val):
                    novel_set.add((key, val))
        novel_values = [f"`{k}` = `{v}`" for k, v in sorted(novel_set)]

    # Suspected typos count from lint findings
    typos = sum(
        1 for f in findings if f.code in (NEAR_MISS_VALUE, CASE_COLLISION, NEAR_MISS_KEY)
    )
    typo_details = [
        f"`{f.fqn}`: `{f.key}` = `{f.value}`"
        for f in findings
        if f.code in (NEAR_MISS_VALUE, CASE_COLLISION, NEAR_MISS_KEY)
    ]

    fragmented = _fragmented_datasets(plan, vocab, dataset_key)

    def _make_factor(name: str, count: int, details: Sequence[str] = ()) -> RiskFactor:
        weight = _DEFAULT_WEIGHTS.get(name, 0.0)
        cap = _DEFAULT_CAPS.get(name, 100.0)
        contrib = min(cap, weight * max(0, count))
        return RiskFactor(
            name=name,
            label=_LABELS.get(name, name),
            count=max(0, count),
            contribution=contrib,
            details=tuple(details[:10]),
        )

    report.factors = [
        _make_factor("access_control_change", len(access_changes), access_changes),
        _make_factor("removal", len(removals), removals),
        _make_factor("overwrite", overwrites, [f"{overwrites} existing tag value(s) overwritten"] if overwrites else []),
        _make_factor("certified_object", len(certified), certified),
        RiskFactor(
            name="blast_radius",
            label=_LABELS["blast_radius"],
            count=len(diffs),
            contribution=_blast_radius(len(diffs), _DEFAULT_WEIGHTS["blast_radius"], 500),
            details=(f"{len(diffs)} object(s) modified",) if diffs else (),
        ),
        _make_factor("near_miss_value", typos, typo_details),
        _make_factor("novel_value", len(novel_values), novel_values),
        _make_factor("unknown_key", len(unknown_keys), unknown_keys),
        _make_factor("dataset_fragmentation", len(fragmented), fragmented),
    ]

    report.raw_score = sum(f.contribution for f in report.factors)

    # Multipliers
    env_multipliers = {"dev": 0.6, "test": 0.8, "stage": 1.0, "prod": 1.25}
    report.multiplier = env_multipliers.get(environment.lower(), 1.0)
    report.score = int(round(max(0.0, min(100.0, report.raw_score * report.multiplier))))

    if report.score >= 75:
        report.band = CRITICAL
    elif report.score >= 50:
        report.band = HIGH
    elif report.score >= 25:
        report.band = MEDIUM
    else:
        report.band = LOW

    return report
