"""Context Catalog AI review: queue reviews, run them in the background, apply suggestions.

A review runs as the admin who asked (their token reaches Confluence), one
document per reviewer loop, ``CONTEXT_CATALOG_REVIEW_CONCURRENCY`` at a time.
Each run is a row in ``context_document_reviews``; the newest per document is
the one shown. Rows still queued or running that this process isn't working on
were cut off by a restart, and are closed as failed when next listed.

Nothing here edits a document except ``apply_review``, which an admin calls
explicitly and which refuses if the document changed after it was reviewed.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from contextlib import closing
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.context_catalog import ContextDocumentModel, ContextDocumentReviewModel, ContextDomainModel
from app.services.context_catalog_review.confluence import open_confluence
from app.services.context_catalog_review.reviewer import CatalogTools, ReviewInput, ReviewVerdict, run_reviewer
from app.services.context_catalog_staleness import document_url, is_stale, last_content_update, normalize_owner

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")

# Review ids this process has queued or is running. Anything else still marked
# queued/running in the table was cut off by a restart.
_active: Set[str] = set()


class ReviewConflict(Exception):
    """The document changed after it was reviewed, so its proposal no longer applies."""


def content_hash(title: Optional[str], body: Optional[str]) -> str:
    return hashlib.sha256(f"{title or ''}\n\0\n{body or ''}".encode("utf-8")).hexdigest()


def doc_hash(doc: ContextDocumentModel) -> str:
    return content_hash(doc.title, doc.body_markdown)


def _session_factory() -> Session:
    from app.db.session import get_lakebase_session

    return get_lakebase_session()


# ------------------------------------------------------------------ input

def _domain_path(db: Session, domain: Optional[ContextDomainModel]) -> List[str]:
    path: List[str] = []
    seen: Set[str] = set()
    while domain is not None and domain.id not in seen:
        seen.add(domain.id)
        path.insert(0, domain.name)
        domain = db.get(ContextDomainModel, domain.parent_id) if domain.parent_id else None
    return path


def owner_signals(db: Session, doc: ContextDocumentModel, domain: Optional[ContextDomainModel]) -> List[Dict[str, str]]:
    """People already tied to this document or its domain, with why: the reviewer's owner candidates."""
    reasons: Dict[str, List[str]] = {}

    def add(who: Optional[str], why: str) -> None:
        key = normalize_owner(who)
        if key:
            reasons.setdefault(key, [])
            if why not in reasons[key]:
                reasons[key].append(why)

    if domain is not None:
        add(domain.primary_owner, f"primary owner of the '{domain.name}' domain")
        add(domain.secondary_owner, f"secondary owner of the '{domain.name}' domain")
        for reviewer in domain.reviewers or []:
            add(reviewer, f"reviewer for the '{domain.name}' domain")
    add(doc.created_by, "created this document")
    add(doc.last_reviewed_by, "last marked this document reviewed")
    if domain is not None:
        counts: Dict[str, int] = {}
        for (owner,) in (
            db.query(ContextDocumentModel.owner)
            .filter(ContextDocumentModel.domain_id == domain.id, ContextDocumentModel.id != doc.id)
            .all()
        ):
            key = normalize_owner(owner)
            if key:
                counts[key] = counts.get(key, 0) + 1
        for owner, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3]:
            add(owner, f"owns {n} other document{'s' if n != 1 else ''} in this domain")
    return [{"who": who, "why": "; ".join(why)} for who, why in reasons.items()]


def build_input(db: Session, doc: ContextDocumentModel) -> ReviewInput:
    domain = db.get(ContextDomainModel, doc.domain_id)
    updated = last_content_update(doc)
    now = datetime.utcnow()
    return ReviewInput(
        document_id=doc.id,
        title=doc.title or "",
        body=doc.body_markdown or "",
        tags=list(doc.tags or []),
        metadata={
            "doc_type": doc.doc_type,
            "status": doc.status,
            "owner": doc.owner,
            "source_url": doc.source_url,
            "source_filename": doc.source_filename,
            "content_updated": updated.date().isoformat() if updated else None,
            "days_since_update": (now - updated).days if updated else None,
            "is_stale": is_stale(doc, now=now),
            "times_retrieved_by_assistant": doc.retrieval_count or 0,
            "today": now.date().isoformat(),
        },
        domain={
            "path": _domain_path(db, domain),
            "description": domain.description if domain else None,
            "categories": (domain.categories or []) if domain else [],
        },
        owner_signals=owner_signals(db, doc, domain),
        needs_owner=not normalize_owner(doc.owner),
    )


# ------------------------------------------------------------------ queueing

def select_documents(
    db: Session, *, domain_id: Optional[str] = None, skip_unchanged: bool = True,
) -> Tuple[List[str], int]:
    """Documents for Review all (optionally one domain and its sub-domains), and how many were skipped.

    ``skip_unchanged`` leaves out documents whose newest completed review saw
    their current title and body, so re-running Review all costs only the edits.
    """
    from app.services.context_catalog_service import ContextCatalogService

    q = db.query(ContextDocumentModel)
    if domain_id:
        q = q.filter(ContextDocumentModel.domain_id.in_(ContextCatalogService._descendant_ids(db, domain_id)))
    docs = q.order_by(ContextDocumentModel.title).all()
    if not skip_unchanged:
        return [d.id for d in docs], 0
    reviewed = {
        r.document_id: r.content_hash
        for r in _latest_by_document(db, status="completed").values()
    }
    selected = [d.id for d in docs if reviewed.get(d.id) != doc_hash(d)]
    return selected, len(docs) - len(selected)


def queue_reviews(
    db: Session, document_ids: Iterable[str], *, requested_by: Optional[str], batch_id: Optional[str] = None,
) -> List[ContextDocumentReviewModel]:
    """One queued row per document, skipping documents that already have a review in flight."""
    ids = list(dict.fromkeys(document_ids))
    busy = {
        doc_id for (doc_id,) in db.query(ContextDocumentReviewModel.document_id)
        .filter(ContextDocumentReviewModel.document_id.in_(ids),
                ContextDocumentReviewModel.status.in_(ACTIVE_STATUSES),
                ContextDocumentReviewModel.id.in_(_active or {""}))
        .all()
    } if ids else set()
    rows = []
    for doc_id in ids:
        if doc_id in busy:
            continue
        row = ContextDocumentReviewModel(
            id=str(uuid.uuid4()), document_id=doc_id, batch_id=batch_id, status="queued",
            requested_by=requested_by,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    _active.update(r.id for r in rows)
    return rows


def start(review_ids: List[str], obo_token: Optional[str]) -> None:
    """Run queued reviews in the background (returns immediately)."""
    if not review_ids:
        return
    from app.workers.poller import _spawn_background

    _spawn_background(run_batch(review_ids, obo_token), "context_catalog_review")


async def run_batch(review_ids: List[str], obo_token: Optional[str], **kwargs: Any) -> None:
    limit = max(1, int(getattr(settings, "CONTEXT_CATALOG_REVIEW_CONCURRENCY", 2) or 2))
    gate = asyncio.Semaphore(limit)

    async def one(review_id: str) -> None:
        async with gate:
            await run_review(review_id, obo_token, **kwargs)

    await asyncio.gather(*(one(r) for r in review_ids))
    logger.info("Context Catalog AI review: finished %d review(s).", len(review_ids))


# ------------------------------------------------------------------ running

def _build_llm():
    from app.model_serving.agent_llm import AgentLLMClient

    model = (getattr(settings, "CONTEXT_CATALOG_REVIEW_MODEL", "") or "").strip()
    if model:
        return AgentLLMClient(model=model, reasoning_effort=settings.CONTEXT_CATALOG_REVIEW_REASONING_EFFORT or "")
    return AgentLLMClient()


def _mark_running(review_id: str, session_factory: Callable[[], Session]) -> Optional[ReviewInput]:
    with closing(session_factory()) as db:
        review = db.get(ContextDocumentReviewModel, review_id)
        if review is None:
            return None
        doc = db.get(ContextDocumentModel, review.document_id)
        if doc is None:
            review.status, review.error, review.finished_at = "failed", "The document was deleted.", datetime.utcnow()
            db.commit()
            return None
        inp = build_input(db, doc)
        review.status = "running"
        review.started_at = datetime.utcnow()
        review.content_hash = doc_hash(doc)
        db.commit()
        return inp


def _finish(review_id: str, session_factory: Callable[[], Session], **fields: Any) -> None:
    with closing(session_factory()) as db:
        review = db.get(ContextDocumentReviewModel, review_id)
        if review is None:
            return
        for key, value in fields.items():
            setattr(review, key, value)
        review.finished_at = datetime.utcnow()
        db.commit()


def _verdict_fields(verdict: ReviewVerdict) -> Dict[str, Any]:
    return {
        "status": "completed",
        "summary": verdict.summary,
        "scores": verdict.scores,
        "overall_score": verdict.overall_score,
        "grade": verdict.grade,
        "recommendations": verdict.recommendations,
        "suggested_owners": verdict.suggested_owners,
        "related_sources": verdict.related_sources,
        "proposed_title": verdict.proposed_title,
        "proposed_tags": verdict.proposed_tags,
        "proposed_body_markdown": verdict.proposed_body_markdown,
        "rewrite_summary": verdict.rewrite_summary,
        "forced": verdict.forced,
        "tool_calls": verdict.tool_calls,
    }


async def run_review(
    review_id: str,
    obo_token: Optional[str],
    *,
    llm: Any = None,
    session_factory: Callable[[], Session] = _session_factory,
    confluence_opener: Callable[[Optional[str]], Any] = open_confluence,
) -> None:
    """Review one queued document and store the result. Never raises."""
    _active.add(review_id)
    try:
        inp = await asyncio.to_thread(_mark_running, review_id, session_factory)
        if inp is None:
            return
        llm = llm or _build_llm()
        async with confluence_opener(obo_token) as confluence:
            verdict = await run_reviewer(
                llm, inp,
                rubric=settings.CONTEXT_CATALOG_REVIEW_RUBRIC,
                catalog=CatalogTools(session_factory, inp.document_id),
                confluence=confluence,
                max_turns=max(1, int(settings.CONTEXT_CATALOG_REVIEW_MAX_TURNS or 12)),
                time_limit_seconds=float(settings.CONTEXT_CATALOG_REVIEW_TIME_LIMIT_SECONDS or 300),
            )
            conf_fields = {"confluence_status": confluence.status, "confluence_note": confluence.note or None,
                           "model": getattr(llm, "endpoint_name", None)}
        if verdict is None:
            await asyncio.to_thread(_finish, review_id, session_factory, status="failed", **conf_fields, error=(
                "The reviewer model didn't return a review. Try again; if it keeps failing, check the Reviewer "
                "model under Admin → Settings → Context Catalog → AI review."
            ))
            return
        await asyncio.to_thread(_finish, review_id, session_factory, **conf_fields, **_verdict_fields(verdict))
        logger.info("Context Catalog AI review: %s -> %s (%s)", inp.document_id, verdict.grade,
                    verdict.overall_score, extra={"data": {"review_id": review_id, "tool_calls": verdict.tool_calls,
                                                           "confluence": conf_fields["confluence_status"]}})
    except Exception as e:  # noqa: BLE001 - a failed review is recorded, never raised into the batch
        logger.exception("Context Catalog AI review %s failed", review_id)
        try:
            await asyncio.to_thread(_finish, review_id, session_factory, status="failed",
                                    error=f"The review failed: {str(e)[:500]}")
        except Exception:  # noqa: BLE001
            logger.exception("Context Catalog AI review %s: couldn't record the failure", review_id)
    finally:
        _active.discard(review_id)


# ------------------------------------------------------------------ reading

def recover_interrupted(db: Session) -> int:
    """Close rows left queued/running by a restart; returns how many."""
    q = db.query(ContextDocumentReviewModel).filter(ContextDocumentReviewModel.status.in_(ACTIVE_STATUSES))
    if _active:
        q = q.filter(~ContextDocumentReviewModel.id.in_(_active))
    rows = q.all()
    for row in rows:
        row.status = "failed"
        row.error = "Interrupted: the app restarted before this review finished. Review the document again."
        row.finished_at = datetime.utcnow()
    if rows:
        db.commit()
    return len(rows)


def _latest_by_document(db: Session, *, status: Optional[str] = None) -> Dict[str, ContextDocumentReviewModel]:
    q = db.query(ContextDocumentReviewModel)
    if status:
        q = q.filter(ContextDocumentReviewModel.status == status)
    latest: Dict[str, ContextDocumentReviewModel] = {}
    for row in q.order_by(ContextDocumentReviewModel.created_at.desc()).all():
        latest.setdefault(row.document_id, row)
    return latest


def _sources_with_links(db: Optional[Session], sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Related sources, with each catalog document's domain id so the UI can open it."""
    ids = [s["document_id"] for s in sources if s.get("document_id")]
    domains = dict(
        db.query(ContextDocumentModel.id, ContextDocumentModel.domain_id)
        .filter(ContextDocumentModel.id.in_(ids)).all()
    ) if db is not None and ids else {}
    return [{**s, "domain_id": domains.get(s.get("document_id"))} for s in sources]


def review_to_dict(review: ContextDocumentReviewModel, doc: Optional[ContextDocumentModel] = None,
                   *, full: bool = True, db: Optional[Session] = None) -> Dict[str, Any]:
    def iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    data: Dict[str, Any] = {
        "id": review.id,
        "document_id": review.document_id,
        "batch_id": review.batch_id,
        "status": review.status,
        "requested_by": review.requested_by,
        "created_at": iso(review.created_at),
        "started_at": iso(review.started_at),
        "finished_at": iso(review.finished_at),
        "grade": review.grade,
        "overall_score": review.overall_score,
        "scores": review.scores or [],
        "summary": review.summary,
        "error": review.error,
        "forced": bool(review.forced),
        "has_rewrite": bool(review.proposed_body_markdown or review.proposed_title or review.proposed_tags),
        "recommendation_count": len(review.recommendations or []),
        "owner_suggestion_count": len(review.suggested_owners or []),
        "applied_at": iso(review.applied_at),
        "applied_by": review.applied_by,
        "is_outdated": bool(doc is not None and review.content_hash and review.content_hash != doc_hash(doc)),
    }
    if full:
        data.update({
            "model": review.model,
            "recommendations": review.recommendations or [],
            "suggested_owners": review.suggested_owners or [],
            "related_sources": _sources_with_links(db, review.related_sources or []),
            "proposed_title": review.proposed_title,
            "proposed_tags": review.proposed_tags,
            "proposed_body_markdown": review.proposed_body_markdown,
            "rewrite_summary": review.rewrite_summary,
            "confluence_status": review.confluence_status,
            "confluence_note": review.confluence_note,
            "tool_calls": review.tool_calls or 0,
            "original_title": doc.title if doc is not None else None,
            "original_body_markdown": doc.body_markdown if doc is not None else None,
            "original_tags": (doc.tags or []) if doc is not None else None,
            "document_url": document_url(doc.id, doc.domain_id) if doc is not None else "",
        })
    return data


def latest_reviews(db: Session) -> List[Dict[str, Any]]:
    """The newest review of every document, without the long fields."""
    latest = _latest_by_document(db)
    if not latest:
        return []
    docs = {d.id: d for d in db.query(ContextDocumentModel).filter(ContextDocumentModel.id.in_(list(latest))).all()}
    return [review_to_dict(r, docs.get(r.document_id), full=False) for r in latest.values()]


def latest_for_document(db: Session, document_id: str) -> Optional[ContextDocumentReviewModel]:
    return (
        db.query(ContextDocumentReviewModel)
        .filter(ContextDocumentReviewModel.document_id == document_id)
        .order_by(ContextDocumentReviewModel.created_at.desc())
        .first()
    )


def progress(db: Session) -> Optional[Dict[str, Any]]:
    """Counts for the newest Review all run, or None if there hasn't been one."""
    newest = (
        db.query(ContextDocumentReviewModel.batch_id)
        .filter(ContextDocumentReviewModel.batch_id.isnot(None))
        .order_by(ContextDocumentReviewModel.created_at.desc())
        .first()
    )
    if not newest:
        return None
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
    rows = db.query(ContextDocumentReviewModel).filter(ContextDocumentReviewModel.batch_id == newest[0]).all()
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return {
        "batch_id": newest[0],
        "total": len(rows),
        **counts,
        "done": counts["completed"] + counts["failed"],
        "requested_by": rows[0].requested_by if rows else None,
        "started_at": min(r.created_at for r in rows).isoformat() if rows else None,
    }


def confluence_status() -> Dict[str, Any]:
    connection = (getattr(settings, "CONTEXT_CATALOG_CONFLUENCE_CONNECTION", "") or "").strip()
    return {"configured": bool(connection), "connection": connection or None}


# ------------------------------------------------------------------ applying

def apply_review(
    db: Session, review_id: str, applied_by: Optional[str], *, title: bool, body: bool, tags: bool,
) -> ContextDocumentModel:
    """Apply the chosen parts of a review's proposal to its document.

    Raises ``ReviewConflict`` if the title or body changed after the review,
    since applying would silently throw those edits away.
    """
    from app.services.context_catalog_service import ContextCatalogService

    review = db.get(ContextDocumentReviewModel, review_id)
    if review is None:
        raise LookupError("Review not found")
    if review.status != "completed":
        raise ValueError("Only a finished review can be applied")
    doc = db.get(ContextDocumentModel, review.document_id)
    if doc is None:
        raise LookupError("The reviewed document no longer exists")
    if review.content_hash != doc_hash(doc):
        raise ReviewConflict(
            "The document changed after this review, so applying it would overwrite those edits. "
            "Review it again, or copy the suggestion into the editor."
        )
    fields: Dict[str, Any] = {}
    if title and review.proposed_title:
        fields["title"] = review.proposed_title
    if body and review.proposed_body_markdown:
        fields["body_markdown"] = review.proposed_body_markdown
    if tags and review.proposed_tags:
        fields["tags"] = list(review.proposed_tags)
    if not fields:
        raise ValueError("This review has nothing of that kind to apply")
    doc = ContextCatalogService.update_document(db, doc.id, **fields)
    review = db.get(ContextDocumentReviewModel, review_id)
    review.applied_at = datetime.utcnow()
    review.applied_by = applied_by
    db.commit()
    return doc
