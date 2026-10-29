"""The change service: one implementation behind the UI, REST, MCP and the agent (ADR-0006 D6).

Workstream A.

``preview`` resolves ops into items against current desired state (merge, unset,
replace and comment semantics, exempt keys set aside, cross-environment copies via
``environments.counterpart``), runs ``authz.decide`` and the risk report, and
returns a ``ChangePreview``. An object UC already has but that has no desired row
starts from its observed tags and description, so a first edit keeps them unless
it removes them. ``submit`` re-reads such objects from UC, previews again, then
routes per ``on_approval_needed``: direct items go to ``store.write_items``; items
that need approval go to ``requests.file_change_request``. Idempotency is checked first.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from pydantic import ValidationError
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.metadata import SOURCE_ADOPT, SOURCE_IMPORT, SOURCE_REVERT, SOURCES, MetadataObservedModel
from app.services.metadata import (
    abac,
    authz,
    environments,
    errors,
    lint,
    observed,
    policy,
    reconciler,
    report,
    requests,
    scope,
    store,
)
from app.services.metadata.report import APPROVAL, DENIED, DIRECT
from app.services.metadata.types import (
    Actor,
    AdoptRequest,
    ChangeItem,
    ChangeOp,
    ChangePreview,
    ChangeRequest,
    ChangeResult,
    ChangeSet,
    ChangeSource,
    DesiredState,
    Finding,
    ImportRequest,
    ImportRow,
    ItemDecision,
    ObjectKey,
    ObservedState,
    PromoteRequest,
    RevertRequest,
    normalize_value,
)

logger = logging.getLogger(__name__)

# Objects per change set when adopting, so a cutover adopt of a whole metastore commits in batches.
ADOPT_BATCH_SIZE = 500
# Relations a submit re-reads from UC before its first edits of them; more need a recent whole read.
FRESH_READ_LIMIT = 200

_LEVEL_TYPES = {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}
_IMPORT_COLUMNS = ("name", "column", "comment", "tags")
# Environment keys mapped onto tag_risk's multipliers, least to most sensitive.
_RISK_ENVIRONMENTS = (
    ("dev", ("dev", "development")),
    ("test", ("tst", "test", "qa")),
    ("stage", ("stg", "stage", "staging", "uat")),
    ("prod", ("prd", "prod", "production")),
)


# --- Small helpers -----------------------------------------------------------------


def _dedupe(values: Iterable[Any]) -> List[Any]:
    return list(dict.fromkeys(v for v in values if v is not None))


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _display(item: ChangeItem) -> str:
    return f"{item.name}.{item.column}" if item.column else item.name


def _deny(code: str, message: str, *, fqn_key: Optional[str] = None, key: Optional[str] = None) -> Finding:
    return Finding(code=code, severity="deny", message=message, fqn_key=fqn_key, key=key)


def _parse(name: str, column: Optional[str] = None, *, where: str = "") -> ObjectKey:
    try:
        return ObjectKey.parse(name, column)
    except ValueError as exc:
        raise errors.MetadataError(f"{where}: {exc}" if where else str(exc)) from None


def _require_admin(actor: Actor, what: str) -> None:
    if not actor.is_admin:
        raise errors.Forbidden(f"Only admins can {what}.")


def items_digest(items: Sequence[ChangeItem]) -> str:
    """sha256 over the resolved items, so a record step can refuse a change that moved since preview."""
    # unchanged_in_uc follows the snapshot, which may refresh between preview and submit;
    # before_hidden is only ever set on a copy returned to a caller.
    payload = [i.model_dump(mode="json", exclude={"unchanged_in_uc", "before_hidden"}) for i in items]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _environment_keys(requested: Sequence[str], findings: List[Finding]) -> List[str]:
    wanted = [e for e in _dedupe((e or "").strip() for e in requested) if e]
    if not wanted:
        return []
    known = environments.environment_keys()
    canonical = {k.lower(): k for k in known}
    out: List[str] = []
    for env in wanted:
        key = canonical.get(env.lower())
        if key is None:
            findings.append(_deny(
                "unknown_environment",
                f"'{env}' isn't a configured environment. Use one of: {', '.join(known) or 'none configured'}.",
            ))
        elif key not in out:
            out.append(key)
    return out


def _object_limit(actor: Actor) -> int:
    limit = int(settings.METADATA_MAX_OBJECTS_PER_CHANGE or 0)
    cap = actor.principal.max_objects_per_change if actor.principal else None
    if limit > 0 and cap and cap > limit:
        limit = int(cap)
    return limit


def _reason_findings(reason: str) -> List[Finding]:
    minimum = int(settings.METADATA_MIN_REASON_LENGTH or 0)
    if minimum > 0 and len((reason or "").strip()) < minimum:
        return [_deny("reason_too_short", f"Give a reason of at least {minimum} characters.")]
    return []


def _change_findings(items: Sequence[ChangeItem], actor: Actor, reason: str) -> List[Finding]:
    """Checks on a change as a whole: the managed catalogs, the object limit and the reason."""
    if not items:
        return []
    found: List[Finding] = []
    outside = scope.outside(scope.catalog_of(i.fqn_key) for i in items)
    if outside:
        found.append(_deny("outside_managed_catalogs", scope.outside_message(outside)))
    object_count = len({i.fqn_key for i in items})
    limit = _object_limit(actor)
    if limit > 0 and object_count > limit:
        found.append(_deny(
            "too_many_objects",
            f"A change can touch at most {limit} objects and this one touches {object_count}. "
            "Split it into smaller changes.",
        ))
    found.extend(_reason_findings(reason))
    return found


def _unique_findings(findings: Iterable[Finding]) -> List[Finding]:
    seen = OrderedDict()
    for f in findings:
        seen.setdefault((f.code, f.severity, f.message, f.fqn_key, f.key), f)
    return list(seen.values())


# --- Resolving ops into items ----------------------------------------------------


@dataclass
class _Target:
    """One op applied to one object: the named object, or its counterpart in another environment."""

    op: ChangeOp
    key: ObjectKey
    copied_from: Optional[str] = None
    # The object a copy is made from; its type is the fallback for a counterpart with no row yet.
    source_fqn: Optional[str] = None
    type_hint: Optional[str] = None
    # An "also make these changes in" copy repeats what the op changed on the source; a promote
    # copy (False) makes the counterpart match the source.
    repeats_source: bool = False


@dataclass
class _Work:
    """An object's tags and description while ops are applied in order."""

    target: _Target
    desired: Optional[DesiredState]
    obs: Optional[ObservedState]
    tags: Dict[str, Optional[str]] = field(default_factory=dict)
    comment: Optional[str] = None
    expected: Dict[str, int] = field(default_factory=dict)
    expected_comment: Optional[int] = None
    op_type: Optional[str] = None
    # For an object UC has but with no desired row: its observed tags and description, the work's start.
    seed: Optional[Dict[str, Optional[str]]] = None
    seed_comment: Optional[str] = None
    # Tag keys the ops named, and whether one named the description.
    named: Set[str] = field(default_factory=set)
    named_comment: bool = False


# (fqn_key, field, key) of an item.
ItemRef = Tuple[str, str, Optional[str]]


def _ref(item: ChangeItem) -> ItemRef:
    return item.fqn_key, item.field, item.key if item.field == "tag" else None


@dataclass
class _Resolved:
    items: List[ChangeItem]
    findings: List[Finding]
    noop_count: int = 0
    observed: Dict[str, ObservedState] = field(default_factory=dict)
    # Kept values of a first edit that no op named: the seed carried them in.
    seeded: Set[ItemRef] = field(default_factory=set)


def _request_targets(request: ChangeRequest) -> Tuple[List[_Target], List[Finding]]:
    findings: List[Finding] = []
    envs = _environment_keys(request.environments, findings)
    targets: List[_Target] = []
    uncopied: Dict[Tuple[str, str], Dict[str, ObjectKey]] = {}
    for op in request.ops:
        key = _parse(op.name, op.column)
        targets.append(_Target(op=op, key=key))
        if not envs:
            continue
        own = environments.environment_of(key.catalog)
        for env in envs:
            if env == own:
                continue
            counterpart = environments.counterpart(key, env)
            if counterpart is None:
                uncopied.setdefault((key.catalog.lower(), env), {})[key.fqn_key] = key
                continue
            targets.append(_Target(op=op, key=counterpart, copied_from=own, source_fqn=key.fqn_key,
                                   repeats_source=True))
    for (catalog, env), keys in uncopied.items():
        only = next(iter(keys.values())) if len(keys) == 1 else None
        what = only.display if only else f"{len(keys)} objects in {catalog}"
        findings.append(Finding(
            code="no_counterpart", severity="warn", fqn_key=only.fqn_key if only else None,
            message=(f"{what} {'has' if only else 'have'} no counterpart in {env}, because the {catalog} catalog "
                     f"isn't named by an environment pattern, so {env} is skipped for "
                     f"{'it' if only else 'them'}."),
        ))
    return targets, findings


def _exempt_finding(fqn_key: str, key: str) -> Finding:
    return _deny("exempt_key", f"'{key}' is managed outside this app and can't be changed here.",
                 fqn_key=fqn_key, key=key)


def _apply(op: ChangeOp, work: _Work, fqn_key: str, findings: List[Finding], *,
           removed_on_source: Optional[Sequence[str]] = None) -> None:
    """``removed_on_source``: for a copy of a replace, the keys it removed on the source object; the copy
    sets the op's tags and removes only those, not the counterpart's other tags."""
    if op.op == "set_tags":
        for key, value in op.tags.items():
            if policy.is_exempt(key):
                findings.append(_exempt_finding(fqn_key, key))
            else:
                work.tags[key] = value
                work.named.add(key)
    elif op.op == "unset_tags":
        for raw in op.keys:
            key = str(raw or "").strip()
            if not key:
                continue
            if policy.is_exempt(key):
                findings.append(_exempt_finding(fqn_key, key))
            else:
                work.tags.pop(key, None)
                work.named.add(key)
    elif op.op == "replace_tags":
        wanted = {k: v for k, v in op.tags.items() if not policy.is_exempt(k)}
        dropped = [k for k in work.tags if k not in wanted and not policy.is_exempt(k)] \
            if removed_on_source is None else list(removed_on_source)
        for key in dropped:
            work.tags.pop(key, None)
        work.tags.update(wanted)
        work.named.update(wanted, dropped)
    elif op.op == "set_comment":
        work.comment = op.comment or None
        work.named_comment = True
    else:
        work.comment = None
        work.named_comment = True


def _diff_items(
    base: Dict[str, Any],
    desired: Optional[DesiredState],
    tags: Dict[str, Optional[str]],
    comment: Optional[str],
    *,
    expected: Optional[Dict[str, int]] = None,
    expected_comment: Optional[int] = None,
) -> List[ChangeItem]:
    """Items turning ``desired`` (None = no row) into ``tags`` + ``comment``."""
    expected = expected or {}
    current = desired.tags if desired else {}
    versions = {t.key: t.version for t in desired.tag_details} if desired else {}
    items: List[ChangeItem] = []
    for key in sorted(set(current) | set(tags)):
        version = expected.get(key, versions.get(key, 0))
        if key in tags and (key not in current or current[key] != tags[key]):
            items.append(ChangeItem(**base, field="tag", key=key, action="set", before=current.get(key),
                                    before_exists=key in current, before_version=version, after=tags[key]))
        elif key in current and key not in tags:
            items.append(ChangeItem(**base, field="tag", key=key, action="unset", before=current[key],
                                    before_exists=True, before_version=version, after=None))
    now = desired.comment.value if desired else None
    if comment != now:
        if expected_comment is None:
            expected_comment = desired.comment.version if desired else 0
        items.append(ChangeItem(
            **base, field="comment", action="comment_set" if comment is not None else "comment_clear",
            before=now, before_exists=now is not None, before_version=expected_comment, after=comment,
        ))
    return items


def _securable_type(work: _Work, desired: Dict[str, DesiredState], seen: Dict[str, ObservedState]) -> str:
    if work.desired is not None:
        return work.desired.securable_type
    if work.obs is not None:
        return work.obs.securable_type
    if work.op_type:
        return work.op_type
    source = work.target.source_fqn
    if source:
        if source in desired:
            return desired[source].securable_type
        if source in seen:
            return seen[source].securable_type
    if work.target.type_hint:
        return work.target.type_hint
    return _LEVEL_TYPES[work.target.key.level]


def _seed(work: _Work) -> None:
    """Start an object UC has but with no desired row from its observed tags and description."""
    work.seed = {k: normalize_value(v) for k, v in (work.obs.tags or {}).items() if not policy.is_exempt(k)}
    work.seed_comment = work.obs.comment or None
    work.tags, work.comment = dict(work.seed), work.seed_comment


def _first_row_items(base: Dict[str, Any], work: _Work) -> List[ChangeItem]:
    """Items creating a seeded object's first desired row, judged against what UC has.

    Every value the row ends up with is an item (the ones UC already holds flagged
    ``unchanged_in_uc``), and so is every UC value it drops. None when nothing would
    change in UC: restating its values doesn't start managing it.
    """
    seed = work.seed or {}
    items: List[ChangeItem] = []
    for key in sorted(set(seed) | set(work.tags)):
        version = work.expected.get(key, 0)
        if key in work.tags:
            items.append(ChangeItem(**base, field="tag", key=key, action="set", before=seed.get(key),
                                    before_exists=key in seed, before_version=version, after=work.tags[key]))
        else:
            items.append(ChangeItem(**base, field="tag", key=key, action="unset", before=seed[key],
                                    before_exists=True, before_version=version, after=None))
    if work.comment is not None or work.seed_comment is not None:
        items.append(ChangeItem(
            **base, field="comment", action="comment_set" if work.comment is not None else "comment_clear",
            before=work.seed_comment, before_exists=work.seed_comment is not None,
            before_version=work.expected_comment or 0, after=work.comment,
        ))
    _mark_unchanged_in_uc(items, work.obs)
    return items if any(not i.unchanged_in_uc for i in items) else []


def _mark_unchanged_in_uc(items: Sequence[ChangeItem], obs: ObservedState) -> None:
    """Flag the items of an object's first desired row that only restate what UC already has."""
    tags = obs.tags or {}
    for item in items:
        if item.field == "tag":
            item.unchanged_in_uc = (item.action == "set" and item.key in tags
                                    and normalize_value(tags[item.key]) == normalize_value(item.after))
        else:
            item.unchanged_in_uc = item.action == "comment_set" and normalize_value(obs.comment) == normalize_value(item.after)


def _resolve_targets(
    db: Session,
    targets: Sequence[_Target],
    actor: Actor,
    *,
    reason: str,
    findings: List[Finding],
) -> _Resolved:
    keys = _dedupe([t.key.fqn_key for t in targets] + [t.source_fqn for t in targets])
    desired = store.get_objects(db, keys) if keys else {}
    seen = observed.get_observed(db, keys) if keys else {}

    work: Dict[str, _Work] = {}
    # id(op) -> the keys a replace removed on its source object, for its "also make these changes in" copies.
    replaced: Dict[int, List[str]] = {}
    noop = 0
    for target in targets:
        fqn = target.key.fqn_key
        w = work.get(fqn)
        if w is None:
            d = desired.get(fqn)
            w = _Work(target=target, desired=d, obs=seen.get(fqn))
            if d is not None:
                w.tags, w.comment = dict(d.tags), d.comment.value
            elif w.obs is not None and w.obs.present:
                _seed(w)
            work[fqn] = w
        op = target.op
        if w.op_type is None and op.securable_type:
            w.op_type = op.securable_type
        # A copy's expected versions belong to the source object, not the counterpart.
        if target.copied_from is None:
            for key, version in op.expected_versions.items():
                w.expected.setdefault(key, int(version))
            if w.expected_comment is None and op.expected_comment_version is not None:
                w.expected_comment = int(op.expected_comment_version)
        before = (dict(w.tags), w.comment)
        finding_count = len(findings)
        _apply(op, w, fqn, findings, removed_on_source=replaced.get(id(op), []) if target.repeats_source else None)
        if op.op == "replace_tags" and not target.copied_from:
            replaced[id(op)] = [k for k in before[0] if k not in w.tags]
        if (w.tags, w.comment) == before and len(findings) == finding_count:
            noop += 1

    items: List[ChangeItem] = []
    seeded: Set[ItemRef] = set()
    for fqn, w in work.items():
        key = w.desired.key if w.desired else (w.obs.key if w.obs else w.target.key)
        if w.desired is not None:
            state = w.desired.object_state
        else:
            state = "exists" if w.obs is not None and w.obs.present else "pending"
        base = dict(
            fqn_key=fqn,
            name=key.name,
            column=key.column_name,
            securable_type=_securable_type(w, desired, seen),
            environment=environments.environment_of(key.catalog),
            object_exists=w.desired is not None,
            object_state=state,
            copied_from=w.target.copied_from,
        )
        if w.seed is None:
            changed = _diff_items(base, w.desired, w.tags, w.comment, expected=w.expected,
                                  expected_comment=w.expected_comment)
        else:
            changed = _first_row_items(base, w)
            seeded.update(_ref(i) for i in changed if i.unchanged_in_uc
                          and not (w.named_comment if i.field == "comment" else i.key in w.named))
        items.extend(changed)

    findings.extend(_change_findings(items, actor, reason))
    return _Resolved(items=items, findings=_unique_findings(findings), noop_count=noop, observed=seen,
                     seeded=seeded)


def resolve(db: Session, request: ChangeRequest, actor: Actor) -> Tuple[List[ChangeItem], List[Finding], int]:
    """(items, change-level findings, noop op count). Raises ``errors.MetadataError`` for bad names."""
    targets, findings = _request_targets(request)
    resolved = _resolve_targets(db, targets, actor, reason=request.reason, findings=findings)
    return resolved.items, resolved.findings, resolved.noop_count


# --- Preview ---------------------------------------------------------------------


def _risk_environment(items: Sequence[ChangeItem]) -> str:
    ranks = [
        rank
        for item in items
        for rank, (_, aliases) in enumerate(_RISK_ENVIRONMENTS)
        if (item.environment or "").lower() in aliases
    ]
    return _RISK_ENVIRONMENTS[max(ranks)][0] if ranks else ""


def _tag_plan(items: Sequence[ChangeItem]):
    """The resolved items as a ``TagPlan``, for the risk score and the access-policy check; None on failure."""
    if not items:
        return None
    try:
        from app.workflows.tag_plan import ObjectDiff, TagPlan

        diffs: "OrderedDict[str, ObjectDiff]" = OrderedDict()
        for item in items:
            diff = diffs.get(item.fqn_key)
            if diff is None:
                diff = ObjectDiff(table=item.name, object_type=item.securable_type.upper(),
                                  exists=item.object_state == "exists", before={}, after={}, column=item.column)
                diffs[item.fqn_key] = diff
            if item.field == "tag":
                if item.before_exists:
                    diff.before[item.key] = item.before or ""
                if item.action == "set":
                    diff.after[item.key] = item.after or ""
            else:
                diff.comment_before = item.before
                diff.comment_after = item.after or ""
        return TagPlan(diffs=diffs)
    except Exception as exc:  # advisory; preview must not fail on it
        logger.debug("Metadata: tag plan unavailable: %s", exc)
        return None


def _risk(items: Sequence[ChangeItem], plan, abac_impacts: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
    """tag_risk's score, fed from the resolved items. Best effort: None when it can't be computed."""
    if plan is None:
        return None
    try:
        from app.workflows.tag_risk import calculate_risk_score

        return calculate_risk_score(plan, environment=_risk_environment(items),
                                    abac_impacts=list(abac_impacts)).to_dict()
    except Exception as exc:  # the score is advisory; preview must not fail on it
        logger.debug("Metadata: risk score unavailable: %s", exc)
        return None


def _preview(db: Session, actor: Actor, resolved: _Resolved, *, reason: str, title: str) -> ChangePreview:
    decisions = authz.decide(db, actor, resolved.items, observed=resolved.observed,
                             seeded=resolved.seeded) if resolved.items else []
    buckets = [report.bucket(d) for d in decisions]
    groups = [g for d, b in zip(decisions, buckets) if b == APPROVAL for g in d.decision.approver_groups]
    items = [d.item for d in decisions]
    blocked = (any(f.severity == "deny" for f in resolved.findings)
               or any(f.severity == "deny" for d in decisions for f in d.findings))
    in_uc = [i for i in items if not i.stops_managing]
    plan = _tag_plan(in_uc)
    policy_findings, impacts = abac.check(in_uc, plan) if not blocked else ([], [])
    typo_findings = lint.check([i for i in items if not i.unchanged_in_uc], lint.known_keys(db)) if not blocked else []
    pv = ChangePreview(
        items=decisions,
        findings=[*resolved.findings, *policy_findings, *typo_findings],
        blocked=blocked,
        direct_count=buckets.count(DIRECT),
        approval_count=buckets.count(APPROVAL),
        denied_count=buckets.count(DENIED),
        noop_count=resolved.noop_count,
        kept_count=sum(1 for d, b in zip(decisions, buckets) if b == DIRECT and d.item.kept),
        needs_approval=APPROVAL in buckets,
        approver_groups=_dedupe(groups),
        environments=sorted({i.environment for i in items if i.environment}),
        object_count=len({i.fqn_key for i in items}),
        digest=items_digest(items),
        risk=_risk(items, plan, impacts),
    )
    pv.report_markdown = report.build_report(pv, reason=reason, title=title)
    return pv


def preview(db: Session, request: ChangeRequest, actor: Actor) -> ChangePreview:
    targets, findings = _request_targets(request)
    resolved = _resolve_targets(db, targets, actor, reason=request.reason, findings=findings)
    return _preview(db, actor, resolved, reason=request.reason, title="Metadata change")


# --- Copying from Unity Catalog ----------------------------------------------------
#
# Starting to manage an object copies what UC has into its first desired row, and syncing then
# removes whatever that copy lacks, so the copy must be recent.


def _max_read_age() -> int:
    """METADATA_ADOPT_MAX_READ_AGE_MINUTES; 0 when off, as when syncing is off (UC isn't read then)."""
    minutes = int(settings.METADATA_ADOPT_MAX_READ_AGE_MINUTES or 0)
    return minutes if minutes > 0 and reconciler.current_mode() != "off" else 0


def _stale_catalogs(db: Session, catalogs: Iterable[str]) -> List[str]:
    """Of ``catalogs``, those whose last whole read from UC is older than the limit, never happened, or
    is listed as unread since (a later check failed or was cut off)."""
    minutes = _max_read_age()
    if not minutes:
        return []
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)
    reads = {c.lower(): at for c, at in observed.confirmed_reads(db).items()}
    return sorted(c for c in {(c or "").lower() for c in catalogs} if c and (reads.get(c) is None or reads[c] < cutoff))


def _stale_message(stale: Sequence[str], lead: str) -> str:
    return (
        f"{lead}, but the last full read of {', '.join(stale)} is more than {_max_read_age()} minutes old or "
        "hasn't happened. Tags added in Unity Catalog since would be missing, and syncing would then remove them. "
        "Run Check now under Sync history, then try again."
    )


def _first_rows_lead(what: str) -> str:
    return f"{what} starts managing objects from the tags and descriptions they had at the last check of Unity Catalog"


def _read_unmanaged(db: Session, targets: Sequence[_Target], what: str) -> None:
    """Re-read from UC the objects these targets would start managing, so their seed is current.

    Up to FRESH_READ_LIMIT relations are re-read; more need a recent whole read of their
    catalogs instead. Raises ``errors.MetadataError`` when that can't be had. Commits the
    refreshed snapshot.
    """
    if not _max_read_age() or not targets:
        return
    keys = {t.key.fqn_key: t.key for t in targets}
    unmanaged = [key for fqn, key in keys.items() if fqn not in store.get_objects(db, keys)]
    if not unmanaged:
        return
    if len({k.relation_key().fqn_key for k in unmanaged}) > FRESH_READ_LIMIT:
        stale = _stale_catalogs(db, (k.catalog for k in unmanaged))
        if stale:
            raise errors.MetadataError(_stale_message(stale, _first_rows_lead(what)))
        return
    try:
        found = observed.refresh(db, unmanaged)
    except Exception as exc:  # noqa: BLE001 - any failure leaves the seed unverified
        db.rollback()
        logger.warning("Metadata: couldn't re-read %d object(s) before a first edit: %s", len(unmanaged), exc)
        found = {}
    missing = [k for k in unmanaged if k.fqn_key not in found]
    if missing:
        subject = missing[0].display if len(missing) == 1 else f"{len(missing)} of these objects"
        raise errors.MetadataError(
            f"{what} was not saved: couldn't read {subject} from Unity Catalog just now, and saving without "
            "a current copy of its tags could remove tags added there since the last check. Try again in a "
            "moment; if it keeps failing, see Sync history."
        )
    db.commit()


# --- Submit and routing ----------------------------------------------------------


def _payload(result: ChangeResult) -> dict:
    """What an idempotent retry replays; the change set is re-read from its own row."""
    return result.model_dump(mode="json", exclude={"change_set", "replayed"})


def _replay(db: Session, actor: Actor, idempotency_key: Optional[str]) -> Optional[ChangeResult]:
    if not idempotency_key:
        return None
    stored = store.find_idempotent(db, actor.id, idempotency_key)
    if stored is None:
        return None
    logger.info("Metadata: replaying idempotent result for %s (key %s)", actor.id, idempotency_key)
    return ChangeResult(**{**stored, "replayed": True})


def _clean_key(key: Optional[str]) -> Optional[str]:
    return (key or "").strip() or None


def _forbidden_message(denied: Sequence[ItemDecision]) -> str:
    reasons = _dedupe(
        r for d in denied for r in (d.decision.reasons or [f"You can't edit {_display(d.item)}."])
    )
    return "You can't make this change. " + " ".join(reasons)


def _reject_message(approval: Sequence[ItemDecision]) -> str:
    reasons = _dedupe(r for d in approval for r in [*d.decision.sensitive_reasons, *d.decision.reasons])
    text = " ".join(reasons) or "Some of it needs approval."
    return f"This change needs approval and on_approval_needed is 'reject'. {text}"


def _file(db: Session, request: ChangeRequest, pv: ChangePreview, decisions: List[ItemDecision], actor: Actor,
          source: str, *, reverts_change_set_id: Optional[str] = None) -> str:
    request_id = requests.file_change_request(db, request=request, preview=pv, items=decisions, actor=actor,
                                              source=source, reverts_change_set_id=reverts_change_set_id)
    db.commit()
    return request_id


def _route(
    db: Session,
    pv: ChangePreview,
    *,
    actor: Actor,
    source: str,
    reason: str,
    routing: str,
    filed_request: Callable[[], ChangeRequest],
    idempotency_key: Optional[str] = None,
    reverts_change_set_id: Optional[str] = None,
) -> ChangeResult:
    """Commit, file or refuse a previewed change per ``routing`` (request, split or reject)."""
    if pv.blocked:
        raise errors.ChangeBlocked(pv)
    if not pv.items:
        return ChangeResult(status="noop", preview=pv, message="Nothing to change: everything already matches.")
    groups: Dict[str, List[ItemDecision]] = {DIRECT: [], APPROVAL: [], DENIED: []}
    for decision in pv.items:
        groups[report.bucket(decision)].append(decision)
    if groups[DENIED]:
        raise errors.Forbidden(_forbidden_message(groups[DENIED]))
    direct, approval = groups[DIRECT], groups[APPROVAL]
    # The first desired row of an object UC has is its whole desired state (what it leaves out, syncing removes),
    # so it waits whole with its edits that need approval: the object starts being managed once they're approved.
    waiting = {d.item.fqn_key for d in approval}

    def waits(d: ItemDecision) -> bool:
        first_row = not d.item.object_exists and d.item.object_state == "exists"
        return d.item.fqn_key in waiting and (d.item.unchanged_in_uc or first_row)

    approval += [d for d in direct if waits(d)]
    direct = [d for d in direct if not waits(d)]
    types = {d.item.fqn_key: d.item.securable_type for d in pv.items}

    def write(decisions: List[ItemDecision], result: ChangeResult) -> ChangeSet:
        return store.write_items(
            db, [d.item for d in decisions], actor=actor, source=source, reason=reason,
            idempotency_key=idempotency_key, reverts_change_set_id=reverts_change_set_id,
            securable_types=types, result_payload=_payload(result),
        )

    def objects(decisions: List[ItemDecision]) -> str:
        return _count(len({d.item.fqn_key for d in decisions}), "object")

    if not approval:
        result = ChangeResult(status="committed", preview=pv, message=(
            f"Saved {_count(len(direct), 'change')} on {objects(direct)}."
        ))
        result.change_set = write(direct, result)
        return result
    if routing == "reject":
        raise errors.Forbidden(_reject_message(approval))
    if routing == "split" and direct:
        result = ChangeResult(status="partial", preview=pv, message=(
            f"Saved {_count(len(direct), 'change')} on {objects(direct)}; "
            f"{_count(len(approval), 'change')} still need approval."
        ))
        result.change_set = write(direct, result)
        result.request_id = _file(db, filed_request(), pv, approval, actor, source,
                                  reverts_change_set_id=reverts_change_set_id)
        result.message = (f"Saved {_count(len(direct), 'change')} on {objects(direct)} and sent "
                          f"{_count(len(approval), 'change')} for approval (request {result.request_id}).")
        store.update_result(db, result.change_set.id, _payload(result))
        return result

    to_file = approval if routing == "split" else list(pv.items)
    claim = None
    if idempotency_key:
        # Claimed in the filing's transaction: a concurrent submit with the same key fails
        # on the unique index before it files a second request.
        claim = store.write_items(
            db, [], actor=actor, source=source, reason=reason, idempotency_key=idempotency_key, commit=False,
            result_payload={"status": "pending_approval", "preview": {}, "message": "Filing the approval request."},
        )
    try:
        request_id = _file(db, filed_request(), pv, to_file, actor, source,
                           reverts_change_set_id=reverts_change_set_id)
    except Exception:
        db.rollback()
        raise
    result = ChangeResult(status="pending_approval", request_id=request_id, preview=pv, message=(
        f"Sent {_count(len(to_file), 'change')} for approval (request {request_id})."
    ))
    if claim is not None:
        store.update_result(db, claim.id, _payload(result), request_id=request_id)
    return result


def submit(db: Session, request: ChangeRequest, actor: Actor, *, source: ChangeSource) -> ChangeResult:
    """Raises ``errors.VersionConflict``, ``errors.ChangeBlocked``, ``errors.Forbidden`` and
    ``errors.MetadataError``."""
    key = _clean_key(request.idempotency_key)
    replayed = _replay(db, actor, key)
    if replayed is not None:
        return replayed
    _read_unmanaged(db, _request_targets(request)[0], "This change")
    pv = preview(db, request, actor)
    try:
        result = _route(db, pv, actor=actor, source=source, reason=request.reason,
                        routing=request.on_approval_needed, filed_request=lambda: request, idempotency_key=key)
    except (errors.VersionConflict, IntegrityError):
        # Lost a race with an identical submit (same key): answer with the winner's result.
        db.rollback()
        replayed = _replay(db, actor, key)
        if replayed is None:
            raise
        logger.info("Metadata: %s's submit with key %s lost a race; replaying the first result", actor.id, key)
        return replayed
    logger.info("Metadata: %s submitted %d item(s) via %s: %s", actor.id, len(pv.items), source, result.status)
    return result


# --- Promote ---------------------------------------------------------------------


def _promote_targets(db: Session, request: PromoteRequest,
                     sees: Optional[Callable[[str], bool]] = None) -> Tuple[List[_Target], List[Finding]]:
    """``sees`` limits the copied objects to what the caller may browse: their values become the "after" values."""
    key = _parse(request.name, request.column)
    source = store.get_object(db, key.fqn_key) if sees is None or sees(key.fqn_key) else None
    if source is None:
        raise errors.NotFound(f"{key.display} has no desired tags or description to promote.")
    sources = [source]
    if request.include_columns and key.level == "relation":
        sources.extend(c for c in store.get_columns(db, key.fqn_key) if sees is None or sees(c.fqn_key))
    findings: List[Finding] = []
    envs = _environment_keys(request.to_environments, findings)
    source_env = environments.environment_of(key.catalog)
    targets: List[_Target] = []
    for env in envs:
        if env == source_env:
            continue
        if environments.counterpart(source.key, env) is None:
            findings.append(_deny(
                "no_counterpart",
                f"{source.key.display} has no counterpart in {env}: its catalog isn't named by an environment pattern.",
                fqn_key=source.fqn_key,
            ))
            continue
        for obj in sources:
            counterpart = environments.counterpart(obj.key, env)
            if counterpart is None:
                continue
            common = dict(name=counterpart.name, column=counterpart.column_name, securable_type=obj.securable_type)
            ops: List[ChangeOp] = []
            if request.include_tags:
                ops.append(ChangeOp(op="replace_tags", tags=dict(obj.tags), **common))
            if request.include_comment:
                if obj.comment.value is not None:
                    ops.append(ChangeOp(op="set_comment", comment=obj.comment.value, **common))
                else:
                    ops.append(ChangeOp(op="clear_comment", **common))
            targets.extend(
                _Target(op=op, key=counterpart, copied_from=source_env, source_fqn=obj.fqn_key,
                        type_hint=obj.securable_type)
                for op in ops
            )
    return targets, findings


PROMOTE_TITLE = "Promote metadata"


def _promote_preview(db: Session, request: PromoteRequest, actor: Actor,
                     sees: Optional[Callable[[str], bool]] = None) -> Tuple[ChangePreview, List[_Target]]:
    targets, findings = _promote_targets(db, request, sees)
    resolved = _resolve_targets(db, targets, actor, reason=request.reason, findings=findings)
    return _preview(db, actor, resolved, reason=request.reason, title=PROMOTE_TITLE), targets


def preview_promote(db: Session, request: PromoteRequest, actor: Actor, *,
                    sees: Optional[Callable[[str], bool]] = None) -> ChangePreview:
    return _promote_preview(db, request, actor, sees)[0]


def promote(db: Session, request: PromoteRequest, actor: Actor, *, source: ChangeSource,
            sees: Optional[Callable[[str], bool]] = None) -> ChangeResult:
    key = _clean_key(request.idempotency_key)
    replayed = _replay(db, actor, key)
    if replayed is not None:
        return replayed
    _read_unmanaged(db, _promote_targets(db, request, sees)[0], "This promote")
    pv, targets = _promote_preview(db, request, actor, sees)
    return _route(
        db, pv, actor=actor, source=source, reason=request.reason, routing=request.on_approval_needed,
        filed_request=lambda: ChangeRequest(ops=[t.op for t in targets], reason=request.reason,
                                            on_approval_needed=request.on_approval_needed, idempotency_key=key),
        idempotency_key=key,
    )


# --- Revert ----------------------------------------------------------------------


def _ops_from_items(items: Sequence[ChangeItem]) -> List[ChangeOp]:
    """One op per item, pinned to the item's expected version (for a request that re-resolves them)."""
    ops: List[ChangeOp] = []
    for item in items:
        common = dict(name=item.name, column=item.column, securable_type=item.securable_type)
        if item.field == "tag":
            versions = {item.key: item.before_version}
            if item.action == "set":
                ops.append(ChangeOp(op="set_tags", tags={item.key: item.after}, expected_versions=versions, **common))
            else:
                ops.append(ChangeOp(op="unset_tags", keys=[item.key], expected_versions=versions, **common))
        elif item.action == "comment_set":
            ops.append(ChangeOp(op="set_comment", comment=item.after,
                                expected_comment_version=item.before_version, **common))
        else:
            ops.append(ChangeOp(op="clear_comment", expected_comment_version=item.before_version, **common))
    return ops


def _names_list(names: Sequence[str], limit: int = 5) -> str:
    shown = ", ".join(names[:limit])
    return f"{shown} and {len(names) - limit} more" if len(names) > limit else shown


def _revert_findings(plan: store.RevertPlan) -> List[Finding]:
    found: List[Finding] = []
    if plan.unmanaged:
        names = {i.fqn_key: _display(i) for i in plan.items}
        one = len(plan.unmanaged) == 1
        found.append(Finding(code="stops_managing", severity="info", message=(
            f"This change started managing {_count(len(plan.unmanaged), 'object')}, so undoing it stops managing "
            f"{'it' if one else 'them'}: {_names_list([names[f] for f in plan.unmanaged])}. Unity Catalog keeps the "
            f"tags and description {'it has' if one else 'they have'} now, including anything syncing already "
            "applied; nothing is removed there."
        )))
    dropped = set(plan.unmanaged)
    for fqn, display, key in plan.changed_since:
        what = f"the tag {key}" if key is not None else "the description"
        outcome = ("stopping managing it drops that later change here too (Unity Catalog keeps the value)"
                   if fqn in dropped else "undoing this change restores it to its value from before this change anyway")
        found.append(Finding(
            code="changed_since", severity="warn", fqn_key=fqn, key=key,
            message=f"{display}: {what} was changed again after this change; {outcome}.",
        ))
    return found


def _revert_preview(
    db: Session, change_set_id: str, request: RevertRequest, actor: Actor,
) -> Tuple[ChangePreview, List[ChangeItem]]:
    plan = store.revert_plan(db, change_set_id)
    items = plan.items
    findings = [*_revert_findings(plan), *_change_findings(items, actor, request.reason)] if items else []
    seen = observed.get_observed(db, _dedupe(i.fqn_key for i in items)) if items else {}
    resolved = _Resolved(items=items, findings=findings, observed=seen)
    pv = _preview(db, actor, resolved, reason=request.reason, title=f"Revert of change set {change_set_id}")
    return pv, items


def preview_revert(db: Session, change_set_id: str, request: RevertRequest, actor: Actor) -> ChangePreview:
    return _revert_preview(db, change_set_id, request, actor)[0]


def revert(
    db: Session, change_set_id: str, request: RevertRequest, actor: Actor, *, source: ChangeSource,
) -> ChangeResult:
    """Always recorded with source ``revert``; ``source`` is the surface it came through, for the log.

    A revert is all or nothing: ``split`` routes like ``request``, so a change set is
    never marked reverted with part of it still waiting for approval.
    """
    pv, items = _revert_preview(db, change_set_id, request, actor)
    reason = (request.reason or "").strip() or f"Revert change set {change_set_id}"
    routing = "request" if request.on_approval_needed == "split" else request.on_approval_needed
    result = _route(
        db, pv, actor=actor, source=SOURCE_REVERT, reason=reason, routing=routing,
        filed_request=lambda: ChangeRequest(ops=_ops_from_items(items), reason=reason,
                                            on_approval_needed=request.on_approval_needed),
        reverts_change_set_id=change_set_id,
    )
    logger.info("Metadata: %s reverted change set %s via %s: %s", actor.id, change_set_id, source, result.status)
    return result


# --- Adopt -----------------------------------------------------------------------


def _adoptable(obs: ObservedState) -> bool:
    try:
        ObjectKey.parse(obs.key.name, obs.key.column_name)
    except ValueError:
        logger.warning("Metadata: not adopting %s: its name can't be managed here", obs.fqn_key)
        return False
    return True


def _adopt(
    db: Session, fqn_keys: Sequence[str], *, overwrite: bool, reason: str, merge: bool = False,
) -> Tuple[List[ChangeSet], int]:
    """(change sets written, objects skipped because they already have desired state).

    ``merge`` keeps existing desired state and only adds the observed tags (and the
    description, when it has none) that it doesn't set.
    """
    seen = observed.get_observed(db, fqn_keys) if fqn_keys else {}
    present = [seen[k] for k in fqn_keys if k in seen and seen[k].present and _adoptable(seen[k])]
    desired = store.get_objects(db, [o.fqn_key for o in present]) if present else {}
    candidates = [o for o in present if overwrite or merge or o.fqn_key not in desired]
    skipped = len(present) - len(candidates)
    system = authz.system_actor()
    change_sets: List[ChangeSet] = []
    for start in range(0, len(candidates), ADOPT_BATCH_SIZE):
        items: List[ChangeItem] = []
        seeds: List[store.ObjectSeed] = []
        types: Dict[str, str] = {}
        for obs in candidates[start:start + ADOPT_BATCH_SIZE]:
            current = desired.get(obs.fqn_key)
            tags = {k: normalize_value(v) for k, v in (obs.tags or {}).items() if not policy.is_exempt(k)}
            comment = obs.comment or None
            if current is not None and merge:
                tags.update(current.tags)
                comment = current.comment.value if current.comment.value is not None else comment
            elif current is not None:
                tags.update({k: v for k, v in current.tags.items() if policy.is_exempt(k)})
            base = dict(
                fqn_key=obs.fqn_key,
                name=obs.key.name,
                column=obs.key.column_name,
                securable_type=obs.securable_type,
                environment=environments.environment_of(obs.key.catalog),
                object_exists=current is not None,
                object_state=current.object_state if current is not None else "exists",
            )
            items.extend(_diff_items(base, current, tags, comment))
            types[obs.fqn_key] = obs.securable_type
            if current is None:
                seeds.append(store.ObjectSeed(key=obs.key, securable_type=obs.securable_type, object_state="exists"))
        if items or seeds:
            change_sets.append(store.write_items(db, items, actor=system, source=SOURCE_ADOPT, reason=reason,
                                                 securable_types=types, ensure_objects=seeds))
    return change_sets, skipped


def _adopt_result(change_sets: List[ChangeSet], skipped: int) -> ChangeResult:
    if not change_sets:
        return ChangeResult(status="noop", preview=ChangePreview(noop_count=skipped), message=(
            "Nothing to adopt: every object found already has desired state, or none is in the snapshot yet."
        ))
    objects = sum(int(cs.summary.get("objects", 0)) for cs in change_sets)
    changes = sum(int(cs.summary.get("tags_set", 0)) + int(cs.summary.get("tags_unset", 0))
                  + int(cs.summary.get("comments", 0)) for cs in change_sets)
    envs = sorted({e for cs in change_sets for e in cs.summary.get("environments", [])})
    batches = f" in {len(change_sets)} change sets" if len(change_sets) > 1 else ""
    values = f"{changes} tag or description" if changes == 1 else f"{changes} tags or descriptions"
    return ChangeResult(
        status="committed",
        change_set=change_sets[0],
        preview=ChangePreview(direct_count=changes, object_count=objects, environments=envs, noop_count=skipped),
        message=f"Adopted {_count(objects, 'object')} with {values}{batches}.",
    )


def _relation_columns(db: Session, fqn_key: str) -> List[str]:
    try:
        level = ObjectKey.from_fqn_key(fqn_key).level
    except ValueError:
        return []
    return [c.fqn_key for c in observed.get_columns(db, fqn_key)] if level == "relation" else []


def _observed_under(db: Session, fqn_key: str, *, include_columns: bool) -> List[str]:
    """The present observed objects at or under a catalog or schema key."""
    col = MetadataObservedModel.fqn_key
    query = db.query(col).filter(
        or_(col == fqn_key, col.startswith(f"{fqn_key}.", autoescape=True)),
        MetadataObservedModel.present.is_(True),
    )
    if not include_columns:
        query = query.filter(MetadataObservedModel.column_name.is_(None))
    return [k for (k,) in query.order_by(col)]


def adopt(db: Session, request: AdoptRequest, actor: Actor) -> ChangeResult:
    """Make observed state the desired state for the named objects or catalogs (admins only).

    A catalog or schema name adopts everything observed under it.
    """
    _require_admin(actor, "adopt Unity Catalog state")
    catalogs = [c.strip() for c in request.catalogs if (c or "").strip()]
    names = [n.strip() for n in request.names if (n or "").strip()]
    if not catalogs and not names:
        raise errors.MetadataError("Name at least one catalog or object to adopt.")
    outside = scope.outside([*catalogs, *(_parse(n).catalog for n in names)])
    if outside:
        raise errors.MetadataError(scope.outside_message(outside))
    # Adopting copies the snapshot; after a failed read it can lack tags added in UC since, which syncing would remove.
    named = {_parse(c).catalog.lower() for c in catalogs} | {_parse(n).catalog.lower() for n in names}
    unread = sorted(named & set(observed.unread_catalogs(db)))
    if unread:
        raise errors.MetadataError(
            f"The last check couldn't read {', '.join(unread)} from Unity Catalog, so the copy saved here may be "
            "missing tags added there since. Starting to manage it now would save that out-of-date copy, and "
            "syncing would then remove the missing tags. Try again once a check has read it (see Sync history)."
        )
    keys: List[str] = []
    for catalog in catalogs:
        keys.extend(_observed_under(db, _parse(catalog).fqn_key, include_columns=request.include_columns))
    for name in names:
        key = _parse(name)
        if key.level in ("catalog", "schema"):
            keys.extend(_observed_under(db, key.fqn_key, include_columns=request.include_columns))
            continue
        keys.append(key.fqn_key)
        if request.include_columns:
            keys.extend(_relation_columns(db, key.fqn_key))
    keys = _dedupe(keys)
    copied = keys if request.overwrite else [k for k in keys if k not in store.get_objects(db, keys)]
    stale = _stale_catalogs(db, (scope.catalog_of(k) for k in copied))
    if stale:
        raise errors.MetadataError(_stale_message(
            stale, "Start managing copies each object's tags and description from the last check of Unity Catalog",
        ))
    reason = f"{(request.reason or '').strip() or 'Adopt current Unity Catalog state'} (requested by {actor.id})"
    change_sets, skipped = _adopt(db, keys, overwrite=request.overwrite, reason=reason)
    logger.info("Metadata: %s adopted %d change set(s) for %d catalog(s) and %d name(s)",
                actor.id, len(change_sets), len(catalogs), len(names))
    return _adopt_result(change_sets, skipped)


def adopt_observed(db: Session, fqn_keys: Sequence[str], *, reason: str) -> Optional[ChangeSet]:
    """The reconciler's new-object policy: adopt these observed objects as the system actor.

    A relation's columns are adopted with it; objects that already have desired
    state are skipped. Returns the first change set (large adopts are batched), or
    None when there was nothing to adopt.
    """
    keys: List[str] = []
    for fqn in fqn_keys:
        if not fqn:
            continue
        keys.append(fqn)
        keys.extend(_relation_columns(db, fqn))
    change_sets, _ = _adopt(db, _dedupe(keys), overwrite=False, reason=reason)
    return change_sets[0] if change_sets else None


def merge_observed(db: Session, fqn_keys: Sequence[str], *, reason: str) -> Optional[ChangeSet]:
    """The reconciler, for objects created or recreated under existing desired state: keep that
    state, and add the tags (and description, when it has none) they were created with.

    Without this the first sync would remove whatever the creator set. Returns the
    first change set, or None when nothing needed adding.
    """
    change_sets, _ = _adopt(db, _dedupe([k for k in fqn_keys if k]), overwrite=False, reason=reason, merge=True)
    return change_sets[0] if change_sets else None


# --- Import ----------------------------------------------------------------------


def _csv_cell(raw: Dict[str, Optional[str]], columns: Dict[str, str], name: str) -> str:
    return (raw.get(columns[name]) or "") if name in columns else ""


def _csv_rows(text: str) -> List[Tuple[str, ImportRow]]:
    reader = csv.DictReader(io.StringIO(text))
    try:
        header = reader.fieldnames
    except csv.Error as exc:
        raise errors.MetadataError(f"Line 1: {exc}") from None
    if not header:
        raise errors.MetadataError("The CSV is empty. Start with a header row: name,column,comment,tags.")
    columns = {(h or "").strip().lower(): h for h in header}
    if "name" not in columns:
        raise errors.MetadataError("The CSV header needs a name column (name,column,comment,tags).")
    unknown = sorted(c for c in columns if c not in _IMPORT_COLUMNS)
    if unknown:
        raise errors.MetadataError(
            f"Line 1: unknown column {', '.join(unknown)}. Use name, column, comment and tags."
        )

    rows: List[Tuple[str, ImportRow]] = []
    try:
        for raw in reader:
            where = f"Line {reader.line_num}"
            if None in raw:
                raise errors.MetadataError(f"{where}: more cells than the header has columns.")
            if not any((v or "").strip() for v in raw.values()):
                continue
            name = _csv_cell(raw, columns, "name").strip()
            if not name:
                raise errors.MetadataError(f"{where}: name is required.")
            tags = None
            tags_text = _csv_cell(raw, columns, "tags").strip()
            if tags_text:
                try:
                    tags = json.loads(tags_text)
                except ValueError:
                    tags = None
                if not isinstance(tags, dict):
                    raise errors.MetadataError(f'{where}: tags must be a JSON object, like {{"pii": null}}.')
                bad = sorted(str(k) for k, v in tags.items() if v is not None and not isinstance(v, str))
                if bad:
                    raise errors.MetadataError(f"{where}: tag values must be text or null ({', '.join(bad)}).")
            comment = _csv_cell(raw, columns, "comment")
            rows.append((where, ImportRow(
                name=name,
                column=_csv_cell(raw, columns, "column").strip() or None,
                tags=tags,
                comment=comment if comment != "" else None,
            )))
    except csv.Error as exc:
        raise errors.MetadataError(f"Line {reader.line_num}: {exc}") from None
    return rows


def _import_targets(request: ImportRequest) -> List[_Target]:
    rows: List[Tuple[str, ImportRow]] = [(f"Row {n}", row) for n, row in enumerate(request.rows, 1)]
    if (request.csv or "").strip():
        rows.extend(_csv_rows(request.csv))
    if not rows:
        raise errors.MetadataError("Nothing to import: send rows, or CSV text with a name,column,comment,tags header.")
    tag_op = "set_tags" if request.mode == "merge" else "replace_tags"
    targets: List[_Target] = []
    for where, row in rows:
        key = _parse(row.name, row.column, where=where)
        common = dict(name=row.name, column=row.column)
        try:
            if row.tags is not None:
                targets.append(_Target(op=ChangeOp(op=tag_op, tags=row.tags, **common), key=key))
            if row.comment is not None:
                op = (ChangeOp(op="set_comment", comment=row.comment, **common) if row.comment != ""
                      else ChangeOp(op="clear_comment", **common))
                targets.append(_Target(op=op, key=key))
        except ValidationError as exc:
            raise errors.MetadataError(f"{where}: {exc.errors()[0].get('msg', 'invalid row')}") from None
    return targets


def _import_preview(db: Session, request: ImportRequest, actor: Actor) -> Tuple[ChangePreview, List[_Target]]:
    targets = _import_targets(request)
    resolved = _resolve_targets(db, targets, actor, reason=request.reason, findings=[])
    stale = _stale_catalogs(db, (scope.catalog_of(i.fqn_key) for i in resolved.items
                                 if not i.object_exists and i.object_state == "exists"))
    if stale:
        resolved.findings.append(_deny("stale_snapshot", _stale_message(stale, _first_rows_lead("This import"))))
    return _preview(db, actor, resolved, reason=request.reason, title="Import metadata"), targets


def preview_import(db: Session, request: ImportRequest, actor: Actor) -> ChangePreview:
    _require_admin(actor, "import metadata")
    return _import_preview(db, request, actor)[0]


def import_rows(db: Session, request: ImportRequest, actor: Actor) -> ChangeResult:
    """merge = ``set_tags``, replace = ``replace_tags``; a sensitive row sends the whole import to approval."""
    _require_admin(actor, "import metadata")
    pv, targets = _import_preview(db, request, actor)
    result = _route(
        db, pv, actor=actor, source=SOURCE_IMPORT, reason=request.reason, routing="request",
        filed_request=lambda: ChangeRequest(ops=[t.op for t in targets], reason=request.reason),
    )
    logger.info("Metadata: %s imported %d item(s): %s", actor.id, len(pv.items), result.status)
    return result


# --- Recording an approved request -----------------------------------------------


def record_prepared(
    db: Session, *, request_id: str, prepared: dict, requester: Actor, approver: Optional[str],
) -> ChangeSet:
    """Write a prepared (approved) change from an approval request, rechecked against current versions.

    Used by the workflow's record step. ``prepared`` is what ``requests`` stored at
    prepare time; nothing from the tool's arguments is trusted. ``prepared["items"]``
    holds ChangeItem dicts (ItemDecision dicts are accepted too); optional keys are
    ``source``, ``reason`` and ``reverts_change_set_id``. Recording the same request
    again returns the change set that recorded it.
    """
    existing = store.find_recorded(db, request_id)
    if existing is not None:
        logger.info("Metadata: request %s was already recorded as change set %s", request_id, existing.id)
        return existing
    prepared = prepared or {}
    raw_items = [
        i["item"] if isinstance(i, dict) and "item" in i and "decision" in i else i
        for i in prepared.get("items") or []
    ]
    try:
        items = [ChangeItem.model_validate(i) for i in raw_items]
    except ValidationError as exc:
        raise errors.MetadataError(
            f"Request {request_id}'s prepared change has {exc.error_count()} invalid field(s) and can't be recorded."
        ) from None
    if not items:
        raise errors.MetadataError(f"Request {request_id} has no prepared changes to record.")
    if any(i.stops_managing for i in items) and not prepared.get("reverts_change_set_id"):
        raise errors.MetadataError(f"Request {request_id}'s prepared change stops managing objects but isn't a "
                                   "revert, so it can't be recorded.")
    source = prepared.get("source") if prepared.get("source") in SOURCES else "agent"
    note = f"approved by {approver}" if approver else "approved"
    reason = (prepared.get("reason") or "").strip()
    try:
        return store.write_items(
            db, items, actor=requester, source=source,
            reason=f"{reason} ({note})" if reason else note[0].upper() + note[1:],
            request_id=request_id,
            reverts_change_set_id=prepared.get("reverts_change_set_id"),
            securable_types={i.fqn_key: i.securable_type for i in items},
        )
    except errors.VersionConflict:
        # A concurrent record of the same request bumps the versions this one expected.
        existing = store.find_recorded(db, request_id)
        if existing is None:
            raise
        logger.info("Metadata: request %s was recorded concurrently as change set %s", request_id, existing.id)
        return existing
