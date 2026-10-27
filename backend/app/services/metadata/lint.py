"""Typo and hygiene warnings for a metadata preview.

Compares the tag keys and values a change sets with the ones already in use, and warns about
near-misses (``clasification`` for ``classification``, ``sales-egn`` for ``sales-eng``),
differences only in capitalisation, stray whitespace and unusual characters. The checks
themselves live in ``app.workflows.tag_lint``; this module feeds them from the Metadata
Manager's own list of keys and values and turns what they find into preview findings.

Advisory: every finding is a warning, so it never blocks a change, and a preview never
fails because of it.
"""
import logging
from collections import OrderedDict
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.metadata import policy, search
from app.services.metadata.types import ChangeItem, Finding, KeyInfo

logger = logging.getLogger(__name__)

# Keys read as the vocabulary: the most used ones, plus every key that has a key policy.
KEY_LIMIT = 500
# Findings listed before the rest are summarised, so one sloppy bulk edit doesn't bury the preview.
MAX_FINDINGS = 20


def known_keys(db: Session) -> List[KeyInfo]:
    """The keys and top values already in use, or nothing when the check is off or can't read them."""
    if not settings.METADATA_LINT_CHECK:
        return []
    try:
        return search.list_keys(db, limit=KEY_LIMIT)
    except Exception as exc:  # noqa: BLE001 - advisory
        logger.info("Metadata: typo check skipped, couldn't read the known keys: %s", exc)
        return []


def _suggestion_text(suggestions) -> str:
    names = [f"'{s.value}'" for s in suggestions if s.value]
    return f" Did you mean {' or '.join(names)}?" if names else ""


def check(items: Sequence[ChangeItem], keys: Sequence[KeyInfo]) -> List[Finding]:
    """Warnings for the tags ``items`` set, judged against ``keys`` (from ``known_keys``)."""
    if not keys:
        return []
    # One finding per distinct key and value, however many objects carry it.
    pairs: "OrderedDict[Tuple[str, str], ChangeItem]" = OrderedDict()
    for item in items:
        if item.field == "tag" and item.action == "set" and item.after is not None:
            pairs.setdefault((item.key, item.after), item)
    if not pairs:
        return []

    try:
        from app.workflows.tag_lint import WARNING, run_lint_checks
        from app.workflows.tag_plan import ObjectDiff, TagPlan, TagVocabulary
        from app.workflows.tag_policy import TagPolicy

        # A key with a fixed list of allowed values is checked against that list already.
        free_text = [k for k in keys if not (k.policy and k.policy.allowed_values)]
        vocabulary = TagVocabulary(
            values={k.key: {f.value: f.count for f in k.values if f.value} for k in free_text},
            available=True,
        )
        tag_policy = TagPolicy(known_keys={k.key: {} for k in keys}, reserved_keys=policy.exempt_keys())

        diffs: "OrderedDict[str, ObjectDiff]" = OrderedDict()
        for (key, value), item in pairs.items():
            diffs[f"{key}\x00{value}"] = ObjectDiff(
                table=item.name, object_type=item.securable_type.upper(), exists=True,
                before={}, after={key: value}, column=item.column,
            )
        lint = run_lint_checks(TagPlan(diffs=diffs), vocabulary=vocabulary, policy=tag_policy)
    except Exception as exc:  # noqa: BLE001 - advisory
        logger.info("Metadata: typo check failed: %s", exc)
        return []

    findings: List[Finding] = []
    for f in lint:
        if f.severity != WARNING:
            continue
        item: Optional[ChangeItem] = pairs.get((f.key, f.value))
        findings.append(Finding(
            code=f.code.lower(), severity="warn", key=f.key,
            fqn_key=item.fqn_key if item else None,
            message=f.message + _suggestion_text(f.suggestions),
        ))
    if len(findings) > MAX_FINDINGS:
        extra = len(findings) - MAX_FINDINGS
        findings = findings[:MAX_FINDINGS]
        findings.append(Finding(
            code="lint_more", severity="info",
            message=f"{extra:,} more suspected typos aren't listed.",
        ))
    return findings
