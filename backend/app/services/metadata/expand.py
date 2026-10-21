"""Expand name patterns in a change into one op per matching object.

A ``ChangeOp`` may carry ``match`` (a glob over ``catalog``, ``catalog.schema`` or
``catalog.schema.table``; ``*`` and ``?`` match within one part) instead of
``name``, so one op can tag every table in a schema, or every ``email`` column
across a catalog. A catalog part in braces, ``{sales}`` (a glob too), means every
catalog whose environment-pattern base matches: ``sales_dev``, ``sales_prd`` and
so on. The matches are the objects the platform knows about: present rows in the
snapshot plus active desired rows, so a table declared before it was created
matches and a dropped one doesn't. Nothing here reads Unity Catalog.

The API and the tools call ``expand_request`` before ``changes.preview`` or
``changes.submit``; everything after that sees ordinary named ops, so limits,
authorization, approvals and the log apply per object as usual.
"""
from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.metadata import MetadataObjectModel, MetadataObservedModel
from app.services.metadata import environments
from app.services.metadata import scope as managed
from app.services.metadata.errors import ChangeBlocked, MetadataError
from app.services.metadata.types import (
    RELATION_TYPES,
    Actor,
    ChangeOp,
    ChangePreview,
    ChangeRequest,
    ChangeResult,
    ChangeSource,
    Finding,
    ObjectKey,
)

logger = logging.getLogger(__name__)

# The settings' ceiling on objects per change; a pattern can't usefully match more.
MAX_MATCHES = 100_000

_UNSAFE = re.compile(r"[`'\";\s\x00-\x1f\x7f]")
# {sales} as the catalog part: the catalog with that environment-pattern base in every environment.
_EVERY_ENVIRONMENT = re.compile(r"\{([^{}]+)\}")
_UNSAFE_COLUMN = re.compile(r"[`'\";\x00-\x1f\x7f]")
_LEVEL_TYPES: Dict[int, Tuple[str, ...]] = {1: ("catalog",), 2: ("schema",), 3: RELATION_TYPES}
_ESCAPE = "\\"
_O = MetadataObjectModel.__table__
_B = MetadataObservedModel.__table__


@dataclass
class Match:
    name: str
    column: Optional[str]
    securable_type: str
    fqn_key: str


@dataclass
class Expansion:
    request: ChangeRequest
    # One info or warn finding per pattern, saying how many objects it matched.
    findings: List[Finding] = field(default_factory=list)
    matched: Dict[str, int] = field(default_factory=dict)


def has_patterns(request: ChangeRequest) -> bool:
    return any((op.match or "").strip() for op in request.ops)


def _parts(pattern: str) -> List[str]:
    parts = [p.strip() for p in (pattern or "").strip().split(".")]
    if not 1 <= len(parts) <= 3 or not all(parts):
        raise MetadataError(
            f"'{pattern}' isn't a name pattern. Use catalog, catalog.schema or catalog.schema.table, "
            "with * or ? inside a part (for example sales_dev.bronze.*)."
        )
    if any(_UNSAFE.search(p) for p in parts):
        raise MetadataError(f"'{pattern}' contains a character names can't have (quotes, spaces or ;).")
    if any(c in p for p in parts[1:] for c in "{}") or (
        any(c in parts[0] for c in "{}") and not _EVERY_ENVIRONMENT.fullmatch(parts[0])
    ):
        raise MetadataError(
            f"'{pattern}': braces go around the whole catalog part only, as in {{sales}}.bronze.*, "
            "meaning the sales catalog in every environment."
        )
    return [p.lower() for p in parts]


def _environment_catalogs(db: Session, base_glob: str) -> List[str]:
    """Known catalogs, in any environment, whose environment-pattern base matches ``base_glob``."""
    known = set(db.execute(select(func.lower(_B.c.catalog)).distinct()).scalars())
    known |= set(db.execute(select(func.lower(_O.c.catalog)).distinct()).scalars())
    return sorted(
        c for c in known
        if c and (base := environments.base_of(c)) is not None and fnmatch.fnmatchcase(base.lower(), base_glob)
    )


def _like(glob: str) -> str:
    out = []
    for ch in glob:
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        elif ch in ("%", "_", _ESCAPE):
            out.append(_ESCAPE + ch)
        else:
            out.append(ch)
    return "".join(out)


def _part_clause(column, glob: str):
    if "*" not in glob and "?" not in glob:
        return func.lower(column) == glob
    return func.lower(column).like(_like(glob), escape=_ESCAPE)


def _label(op: ChangeOp) -> str:
    pattern = (op.match or "").strip()
    return f"{pattern} (column {op.column.strip()})" if (op.column or "").strip() else pattern


def match_objects(
    db: Session,
    pattern: str,
    *,
    column: Optional[str] = None,
    types: Sequence[str] = (),
    limit: int = MAX_MATCHES,
) -> List[Match]:
    """Known objects matching ``pattern`` (and ``column``, a column-name glob), sorted by fqn_key."""
    parts = _parts(pattern)
    column_glob = (column or "").strip().lower() or None
    if column_glob is not None:
        if len(parts) != 3:
            raise MetadataError("A column pattern needs a catalog.schema.table pattern, for example sales_*.*.*.")
        if _UNSAFE_COLUMN.search(column_glob):
            raise MetadataError(f"'{column}' contains a character column names can't have.")
        wanted = ("column",)
    else:
        wanted = _LEVEL_TYPES[len(parts)]
        if types:
            unknown = sorted(set(types) - set(wanted))
            if unknown:
                raise MetadataError(
                    f"A pattern with {len(parts)} part(s) matches {', '.join(wanted)}; "
                    f"it can't match {', '.join(unknown)}."
                )
            wanted = tuple(t for t in wanted if t in set(types))

    globs = parts + ([column_glob] if column_glob else [])
    every = _EVERY_ENVIRONMENT.fullmatch(parts[0])
    catalogs = set(_environment_catalogs(db, every.group(1))) if every else None
    if catalogs is not None and not catalogs:
        return []
    found: Dict[str, Match] = {}

    def part_ok(i: int, value: Optional[str], glob: str) -> bool:
        if value is None:
            return False
        if i == 0 and catalogs is not None:
            return value.lower() in catalogs
        return fnmatch.fnmatchcase(value.lower(), glob)

    def collect(table, extra) -> None:
        names = [table.c.catalog, table.c.schema_name, table.c.object_name, table.c.column_name][: len(globs)]
        clauses = [
            func.lower(col).in_(sorted(catalogs)) if i == 0 and catalogs is not None else _part_clause(col, g)
            for i, (col, g) in enumerate(zip(names, globs))
        ]
        where = [table.c.securable_type.in_(wanted), *extra, *clauses]
        rows = db.execute(
            select(table.c.fqn_key, table.c.securable_type, table.c.catalog, table.c.schema_name,
                   table.c.object_name, table.c.column_name)
            .where(and_(*where)).order_by(table.c.fqn_key).limit(limit + 1)
        ).all()
        for fqn_key, stype, catalog, schema, obj, col in rows:
            values = [catalog, schema, obj, col][: len(globs)]
            if not all(part_ok(i, v, g) for i, (v, g) in enumerate(zip(values, globs))):
                continue
            if fqn_key in found:
                continue
            name = ".".join(p for p in (catalog, schema, obj) if p)
            try:
                ObjectKey.parse(name, col)
            except ValueError:
                continue
            found[fqn_key] = Match(name=name, column=col, securable_type=stype, fqn_key=fqn_key)

    # Snapshot first, so a relation's type and spelling come from Unity Catalog when it exists.
    collect(_B, [_B.c.present.is_(True)])
    collect(_O, [_O.c.status == "active", or_(_O.c.object_state.is_(None), _O.c.object_state != "orphaned")])
    matches = sorted(found.values(), key=lambda m: m.fqn_key)
    if len(matches) > limit:
        raise MetadataError(
            f"'{pattern}' matches more than {limit:,} objects. Narrow the pattern or split the change."
        )
    return matches


def expand_request(db: Session, request: ChangeRequest) -> Expansion:
    """``request`` with every pattern op replaced by one named op per match.

    Raises ``MetadataError`` for a malformed pattern, an op that combines a pattern
    with expected versions, or a change whose patterns match nothing at all.
    """
    if not has_patterns(request):
        return Expansion(request=request)
    ops: List[ChangeOp] = []
    findings: List[Finding] = []
    matched: Dict[str, int] = {}
    left_out: List[str] = []
    for op in request.ops:
        if not (op.match or "").strip():
            ops.append(op)
            continue
        if op.expected_versions or op.expected_comment_version is not None:
            raise MetadataError("A pattern can't carry expected versions; name each object to use them.")
        label = _label(op)
        found = match_objects(db, op.match, column=op.column, types=op.match_types)
        outside = managed.outside(managed.catalog_of(m.fqn_key) for m in found)
        if outside:
            kept = [m for m in found if managed.manages(managed.catalog_of(m.fqn_key))]
            left_out.extend(outside)
            findings.append(Finding(
                code="pattern_outside_managed_catalogs", severity="info",
                message=(f"'{label}' also matched {len(found) - len(kept):,} object(s) in catalogs this deployment "
                         f"doesn't manage ({', '.join(outside[:5])}); they were left out."),
            ))
            found = kept
        matched[label] = matched.get(label, 0) + len(found)
        if found:
            findings.append(Finding(
                code="pattern_matched", severity="info", message=f"'{label}' matched {len(found):,} object(s).",
            ))
        else:
            findings.append(Finding(
                code="pattern_no_match", severity="warn", message=f"'{label}' doesn't match any known object.",
            ))
        for m in found:
            ops.append(op.model_copy(update={
                "name": m.name, "column": m.column, "match": None, "match_types": [],
                "securable_type": m.securable_type,
            }))
    if not ops and left_out:
        raise MetadataError(managed.outside_message(sorted(set(left_out))))
    if not ops:
        raise MetadataError(
            "No known object matches " + ", ".join(f"'{k}'" for k in matched)
            + ". Objects appear once the snapshot has read them, or once someone declares them by name."
        )
    logger.info("Metadata: expanded %d pattern(s) into %d op(s)", len(matched), len(ops))
    return Expansion(request=request.model_copy(update={"ops": ops}), findings=findings, matched=matched)


UNMANAGED_SHOWN = 3


def _collapse_unmanaged(findings: List[Finding]) -> List[Finding]:
    """One warning per unmanaged object drowns a bulk preview; keep a few and summarize the rest."""
    unmanaged = [f for f in findings if f.code == "unmanaged_object"]
    if len(unmanaged) <= UNMANAGED_SHOWN:
        return findings
    rest = len(unmanaged) - UNMANAGED_SHOWN
    summary = Finding(
        code="unmanaged_objects", severity="warn",
        message=(f"{rest:,} more object(s) aren't managed yet either, so this change becomes their complete "
                 "desired state and the tags they carry in Unity Catalog now will be removed. Adopt their "
                 "catalog or schema first to keep them."),
    )
    out: List[Finding] = []
    shown = 0
    for f in findings:
        if f.code != "unmanaged_object":
            out.append(f)
        elif shown < UNMANAGED_SHOWN:
            out.append(f)
            shown += 1
            if shown == UNMANAGED_SHOWN:
                out.append(summary)
    return out


def _with_findings(preview: Optional[ChangePreview], findings: List[Finding]) -> None:
    if preview is not None:
        preview.findings = _collapse_unmanaged([*findings, *preview.findings])


def preview(db: Session, request: ChangeRequest, actor: Actor) -> ChangePreview:
    """``changes.preview`` after expanding patterns; the preview says what each pattern matched."""
    from app.services.metadata import changes

    expansion = expand_request(db, request)
    result = changes.preview(db, expansion.request, actor)
    _with_findings(result, expansion.findings)
    return result


def submit(db: Session, request: ChangeRequest, actor: Actor, *, source: ChangeSource) -> ChangeResult:
    """``changes.submit`` after expanding patterns. Raises what ``changes.submit`` raises."""
    from app.services.metadata import changes

    expansion = expand_request(db, request)
    try:
        result = changes.submit(db, expansion.request, actor, source=source)
    except ChangeBlocked as exc:
        _with_findings(exc.preview, expansion.findings)
        raise
    _with_findings(result.preview, expansion.findings)
    return result
