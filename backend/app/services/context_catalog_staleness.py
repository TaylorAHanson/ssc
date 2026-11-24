"""
Context Catalog document freshness: owners, staleness, and Teams reminders.

A published document is **stale** once ``max(content updated, last reviewed)``
is older than ``CONTEXT_CATALOG_STALE_AFTER_DAYS``. "Content updated" is
``content_updated_at`` (title/body/link edits only), never ``updated_at``,
which also moves on reviews, reminder bookkeeping and owner changes.

Reminders go to one Microsoft Teams channel through a Workflows webhook,
grouped by owner with one @mention each. The owner is the document's
``owner``, else its domain's ``primary_owner``; owners that aren't an email
address, and documents with no owner at all, are listed without a mention.
A document isn't mentioned again until ``CONTEXT_CATALOG_STALE_RENOTIFY_DAYS``
have passed since its last reminder; "Mark reviewed" or an edit resets it.

Admins can also *request* a review on demand (``request_reviews``): for every
document that needs review — stale or never marked reviewed, which is how a
catalog's first round of ownership is kicked off — or for chosen documents,
whatever their state. It posts the same card and does the same bookkeeping.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from sqlalchemy.orm import Session, load_only

from app.core.config import settings
from app.db.context_catalog import ContextDocumentModel, ContextDomainModel
from app.providers.notifications import teams

logger = logging.getLogger(__name__)

CATALOG_PATH = "/governance/context-catalog"

# One reminder run at a time per process (the scheduler and "Run now").
_run_lock = threading.Lock()


# ------------------------------------------------------------------ settings

def stale_after_days() -> int:
    return max(1, int(getattr(settings, "CONTEXT_CATALOG_STALE_AFTER_DAYS", 90) or 90))


def renotify_days() -> int:
    return max(1, int(getattr(settings, "CONTEXT_CATALOG_STALE_RENOTIFY_DAYS", 7) or 7))


def max_docs_per_owner() -> int:
    return max(1, int(getattr(settings, "CONTEXT_CATALOG_STALE_MAX_DOCS_PER_OWNER", 15) or 15))


def reminders_enabled() -> bool:
    return bool(getattr(settings, "CONTEXT_CATALOG_STALE_REMINDERS_ENABLED", False))


def _webhook_args() -> Tuple[str, str, str]:
    return (
        getattr(settings, "CONTEXT_CATALOG_TEAMS_WEBHOOK_URL", "") or "",
        getattr(settings, "CONTEXT_CATALOG_TEAMS_WEBHOOK_SECRET_SCOPE", "") or "",
        getattr(settings, "CONTEXT_CATALOG_TEAMS_WEBHOOK_SECRET_KEY", "") or "",
    )


def webhook_url() -> str:
    return teams.resolve_webhook_url(*_webhook_args())


def _app_url() -> str:
    return (getattr(settings, "APP_BASE_URL", "") or "").strip().rstrip("/")


def catalog_url() -> str:
    base = _app_url()
    return f"{base}{CATALOG_PATH}" if base else ""


def document_url(document_id: str, domain_id: str) -> str:
    """Deep link that opens the document in the catalog editor ('' without APP_BASE_URL)."""
    base = catalog_url()
    if not base:
        return ""
    return f"{base}?domain={quote(domain_id, safe='')}&doc={quote(document_id, safe='')}"


# ------------------------------------------------------------------ staleness

def normalize_owner(value: Optional[str]) -> Optional[str]:
    """Trimmed owner; emails lower-cased so grouping and owner checks match. '' -> None."""
    owner = (value or "").strip()
    if not owner:
        return None
    return owner.lower() if teams.is_email(owner) else owner


def last_content_update(doc: ContextDocumentModel) -> Optional[datetime]:
    return doc.content_updated_at or doc.created_at or doc.updated_at


def last_activity_at(doc: ContextDocumentModel) -> Optional[datetime]:
    """The later of the last content edit and the last 'Mark reviewed'."""
    stamps = [t for t in (last_content_update(doc), doc.last_reviewed_at) if t]
    return max(stamps) if stamps else None


def is_stale(doc: ContextDocumentModel, *, now: Optional[datetime] = None, days: Optional[int] = None) -> bool:
    """Only published documents go stale — drafts aren't retrieved by the agent."""
    if doc.status != "published":
        return False
    activity = last_activity_at(doc)
    if activity is None:
        return False
    now = now or datetime.utcnow()
    return activity < now - timedelta(days=days or stale_after_days())


def review_reason(doc: ContextDocumentModel, *, now: Optional[datetime] = None) -> Optional[str]:
    """Why a document needs its owner to look at it: ``"stale"``, ``"never_reviewed"`` or ``None``.

    Both are the same job for the owner (confirm it's still right), so the catalog shows them behind one
    "Needs review" filter. Stale wins when both apply. Like staleness, only published documents count:
    drafts aren't retrieved by the agent.
    """
    if doc.status != "published":
        return None
    if is_stale(doc, now=now):
        return "stale"
    if doc.last_reviewed_at is None:
        return "never_reviewed"
    return None


def documents_needing_review(db: Session, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every published document that is stale or has never been marked reviewed, catalog-wide.

    Lightweight on purpose (id, domain and reason only) so the catalog page can filter its domain tree
    without loading every domain's documents.
    """
    now = now or datetime.utcnow()
    docs = (
        db.query(ContextDocumentModel)
        .options(load_only(
            ContextDocumentModel.id,
            ContextDocumentModel.domain_id,
            ContextDocumentModel.status,
            ContextDocumentModel.content_updated_at,
            ContextDocumentModel.created_at,
            ContextDocumentModel.updated_at,
            ContextDocumentModel.last_reviewed_at,
        ))
        .filter(ContextDocumentModel.status == "published")
        .all()
    )
    rows = []
    for doc in docs:
        reason = review_reason(doc, now=now)
        if reason:
            rows.append({"id": doc.id, "domain_id": doc.domain_id, "reason": reason})
    return {"stale_after_days": stale_after_days(), "documents": rows}


def freshness_fields(doc: ContextDocumentModel, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Owner + freshness fields merged into the document API payload."""
    def iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    return {
        "owner": doc.owner,
        "content_updated_at": iso(last_content_update(doc)),
        "last_reviewed_at": iso(doc.last_reviewed_at),
        "last_reviewed_by": doc.last_reviewed_by,
        "last_activity_at": iso(last_activity_at(doc)),
        "stale_notified_at": iso(doc.stale_notified_at),
        "is_stale": is_stale(doc, now=now),
        "review_reason": review_reason(doc, now=now),
        "stale_after_days": stale_after_days(),
    }


def effective_owner(
    doc: ContextDocumentModel, domain: Optional[ContextDomainModel]
) -> Tuple[Optional[str], Optional[str]]:
    """``(owner, source)``: the document's owner, else the domain's primary owner."""
    if normalize_owner(doc.owner):
        return normalize_owner(doc.owner), "document"
    if domain is not None and normalize_owner(domain.primary_owner):
        return normalize_owner(domain.primary_owner), "domain"
    return None, None


@dataclass
class StaleDocument:
    id: str
    title: str
    domain_id: str
    domain_name: str
    owner: Optional[str]
    owner_source: Optional[str]
    last_activity_at: Optional[datetime]
    age_days: int
    url: str
    stale_notified_at: Optional[datetime]
    recently_notified: bool = False
    # "stale", "never_reviewed", or "requested" (an admin asked about this one document).
    reason: str = "stale"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "domain_id": self.domain_id,
            "domain_name": self.domain_name,
            "owner": self.owner,
            "owner_source": self.owner_source,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "age_days": self.age_days,
            "url": self.url,
            "stale_notified_at": self.stale_notified_at.isoformat() if self.stale_notified_at else None,
            "recently_notified": self.recently_notified,
            "reason": self.reason,
        }


def find_stale_documents(
    db: Session,
    *,
    now: Optional[datetime] = None,
    days: Optional[int] = None,
    renotify_after_days: Optional[int] = None,
    include_never_reviewed: bool = False,
    document_ids: Optional[List[str]] = None,
) -> List[StaleDocument]:
    """Every stale published document, oldest first.

    With ``renotify_after_days``, documents mentioned in a reminder within that
    window are flagged ``recently_notified`` (callers drop them before sending).
    ``include_never_reviewed`` adds published documents no one has marked
    reviewed. ``document_ids`` instead selects exactly those documents, in any
    state (reason ``"requested"`` unless they're stale or never reviewed anyway).
    """
    now = now or datetime.utcnow()
    days = days or stale_after_days()
    renotify_cutoff = now - timedelta(days=renotify_after_days) if renotify_after_days else None
    query = db.query(ContextDocumentModel, ContextDomainModel).join(
        ContextDomainModel, ContextDocumentModel.domain_id == ContextDomainModel.id
    )
    if document_ids is not None:
        query = query.filter(ContextDocumentModel.id.in_(document_ids))
    else:
        query = query.filter(ContextDocumentModel.status == "published")
    out: List[StaleDocument] = []
    for doc, domain in query.all():
        if document_ids is not None:
            reason = review_reason(doc, now=now) or "requested"
        elif is_stale(doc, now=now, days=days):
            reason = "stale"
        elif include_never_reviewed and doc.last_reviewed_at is None:
            reason = "never_reviewed"
        else:
            continue
        owner, source = effective_owner(doc, domain)
        activity = last_activity_at(doc)
        out.append(StaleDocument(
            id=doc.id,
            title=doc.title,
            domain_id=domain.id,
            domain_name=domain.name,
            owner=owner,
            owner_source=source,
            last_activity_at=activity,
            age_days=(now - activity).days if activity else 0,
            url=document_url(doc.id, domain.id),
            stale_notified_at=doc.stale_notified_at,
            recently_notified=bool(
                renotify_cutoff and doc.stale_notified_at and doc.stale_notified_at >= renotify_cutoff
            ),
            reason=reason,
        ))
    out.sort(key=lambda d: (-d.age_days, d.title.lower()))
    return out


@dataclass
class OwnerGroup:
    owner: Optional[str]
    mention: Optional[teams.Mention]
    docs: List[StaleDocument] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.mention:
            return self.mention.text
        if self.owner:
            return f"**{teams.card_text(self.owner)}**"
        return "**No owner**"


def group_by_owner(docs: List[StaleDocument]) -> List[OwnerGroup]:
    """One group per owner; email owners get a mention, the no-owner group goes last."""
    groups: Dict[Optional[str], OwnerGroup] = {}
    for doc in docs:
        key = doc.owner
        if key not in groups:
            mention = teams.mention_for(key) if teams.is_email(key) else None
            groups[key] = OwnerGroup(owner=key, mention=mention)
        groups[key].docs.append(doc)
    return sorted(
        groups.values(),
        key=lambda g: (g.owner is None, g.mention is None, (g.owner or "").lower()),
    )


# ------------------------------------------------------------------ messages

_REASON_SUFFIX = {"never_reviewed": "never reviewed", "requested": "update requested"}


def _doc_line(doc: StaleDocument) -> str:
    title = teams.card_text(doc.title) or "(untitled)"
    linked = f"[{title}]({doc.url})" if doc.url else title
    age = _REASON_SUFFIX.get(doc.reason) or f"{doc.age_days} days"
    return f"- {linked} · {teams.card_text(doc.domain_name)} · {age}"


def _owner_blocks(group: OwnerGroup, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    listed = group.docs[:limit]
    count = len(group.docs)
    noun = "document" if count == 1 else "documents"
    header = f"{group.label} — {count} {noun} to review"
    if group.owner is None:
        header += " (set an owner on each so the right person is reminded)"
    elif group.mention is None:
        header += " (owner isn't an email address, so they can't be mentioned)"
    lines = [_doc_line(d) for d in listed]
    if count > len(listed):
        lines.append(f"- …and {count - len(listed)} more")
    blocks = [
        {"type": "TextBlock", "text": header, "wrap": True, "separator": True, "spacing": "Medium"},
        {"type": "TextBlock", "text": "\n".join(lines), "wrap": True, "spacing": "Small"},
    ]
    return blocks, [d.id for d in listed]


@dataclass
class ReminderMessage:
    payload: Dict[str, Any]
    document_ids: List[str]
    owners: List[Optional[str]]


def build_reminder_messages(
    groups: List[OwnerGroup],
    *,
    days: Optional[int] = None,
    per_owner_limit: Optional[int] = None,
    max_bytes: int = teams.MAX_MESSAGE_BYTES,
    intro: Optional[str] = None,
) -> List[ReminderMessage]:
    """Adaptive Card messages for the grouped stale documents.

    Owners are packed into as few messages as fit under ``max_bytes``; an owner
    is never split across messages. Only listed documents (up to
    ``per_owner_limit`` per owner) are reported back as notified, so overflow
    documents come up first next time. ``intro`` replaces the default
    "haven't been edited in N days" explanation under the title.
    """
    import json

    if not groups:
        return []
    days = days or stale_after_days()
    limit = per_owner_limit or max_docs_per_owner()
    brand = teams.card_text(settings.BRAND_NAME)
    total = sum(len(g.docs) for g in groups)
    link = catalog_url()

    def header(part: int, parts: int) -> List[Dict[str, Any]]:
        title = f"Context Catalog: {total} {'document needs' if total == 1 else 'documents need'} review"
        if parts > 1:
            title += f" (part {part} of {parts})"
        return [
            {"type": "TextBlock", "text": title, "size": "Medium", "weight": "Bolder", "wrap": True},
            {"type": "TextBlock", "isSubtle": True, "wrap": True, "spacing": "None",
             "text": intro or (f"These published {brand} documents haven't been edited or marked reviewed in "
                               f"over {days} days, so the assistant may be giving out-of-date answers. Open "
                               "each one, then update it or select Mark reviewed if it's still accurate.")},
        ]

    actions = [{"type": "Action.OpenUrl", "title": "Open Context Catalog", "url": link}] if link else None

    def assemble(chunk: List[OwnerGroup], part: int, parts: int) -> ReminderMessage:
        blocks = header(part, parts)
        ids: List[str] = []
        for group in chunk:
            group_blocks, group_ids = _owner_blocks(group, limit)
            blocks.extend(group_blocks)
            ids.extend(group_ids)
        mentions = [g.mention for g in chunk if g.mention]
        payload = teams.adaptive_card_message(blocks, mentions=mentions, actions=actions)
        return ReminderMessage(payload, ids, [g.owner for g in chunk])

    def size(chunk: List[OwnerGroup]) -> int:
        # Sized with a multi-part header, the longer of the two.
        return len(json.dumps(assemble(chunk, 99, 99).payload).encode("utf-8"))

    chunks: List[List[OwnerGroup]] = []
    current: List[OwnerGroup] = []
    for group in groups:
        if current and size(current + [group]) > max_bytes:
            chunks.append(current)
            current = []
        current.append(group)
    chunks.append(current)
    return [assemble(chunk, i + 1, len(chunks)) for i, chunk in enumerate(chunks)]


# ------------------------------------------------------------------ actions

def _new_report(trigger: str, now: datetime) -> Dict[str, Any]:
    return {
        "trigger": trigger, "status": "nothing_due", "stale_total": 0, "due": 0,
        "skipped_recently_notified": 0, "notified_documents": 0, "owners": 0,
        "messages_sent": 0, "messages_failed": 0, "errors": [], "ran_at": now.isoformat(),
    }


def _send_reminders(
    db: Session,
    report: Dict[str, Any],
    candidates: List[StaleDocument],
    *,
    url: str,
    now: datetime,
    intro: Optional[str] = None,
) -> None:
    """Post cards for the candidates not reminded recently, then stamp the listed ones.

    Marks each listed document ``stale_notified_at`` only when its message was
    accepted, so a failed post is retried on the next run.
    """
    due = [d for d in candidates if not d.recently_notified]
    report.update(stale_total=len(candidates), due=len(due), skipped_recently_notified=len(candidates) - len(due))
    if not due:
        return
    groups = group_by_owner(due)
    report["owners"] = len(groups)
    notified: List[str] = []
    for message in build_reminder_messages(groups, intro=intro):
        result = teams.post_message(url, message.payload)
        if result.ok:
            report["messages_sent"] += 1
            notified.extend(message.document_ids)
        else:
            report["messages_failed"] += 1
            report["errors"].append(result.error or "Teams rejected the message")
    if notified:
        (
            db.query(ContextDocumentModel)
            .filter(ContextDocumentModel.id.in_(notified))
            .update(
                {
                    ContextDocumentModel.stale_notified_at: now,
                    # Reminder bookkeeping isn't an edit; keep updated_at as it was.
                    ContextDocumentModel.updated_at: ContextDocumentModel.updated_at,
                },
                synchronize_session=False,
            )
        )
        db.commit()
    report["notified_documents"] = len(notified)
    if report["messages_failed"] == 0:
        report["status"] = "sent"
    else:
        report["status"] = "partial" if report["messages_sent"] else "failed"
    logger.info(
        "Context Catalog reminders (%s): %s — %d candidate(s), %d due, %d notified in %d message(s), %d failed.",
        report["trigger"], report["status"], report["stale_total"], report["due"],
        report["notified_documents"], report["messages_sent"], report["messages_failed"],
    )


def _locked_run(
    db: Session,
    trigger: str,
    now: Optional[datetime],
    select: Callable[[datetime], List[StaleDocument]],
    intro: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared run wrapper: one run at a time, needs a webhook, records the report."""
    now = now or datetime.utcnow()
    report = _new_report(trigger, now)
    if not _run_lock.acquire(blocking=False):
        report["status"] = "busy"
        return report
    try:
        url = webhook_url()
        if not url:
            report["status"] = "not_configured"
            report["errors"].append("The Teams webhook URL is not configured.")
            return report
        _send_reminders(db, report, select(now), url=url, now=now, intro=intro)
        return report
    finally:
        record_run(report)
        _run_lock.release()


def run_stale_reminders(db: Session, *, trigger: str = "schedule", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Post reminders for stale documents not mentioned within the re-notify window."""
    return _locked_run(
        db, trigger, now,
        lambda at: find_stale_documents(db, now=at, renotify_after_days=renotify_days()),
    )


def _request_intro(single: bool) -> str:
    brand = teams.card_text(settings.BRAND_NAME)
    what = "this published document is" if single else f"these published {brand} documents are"
    return (f"A catalog admin asked you to confirm {what} still accurate, so the assistant doesn't give "
            "out-of-date answers. Open each one, then update it or select Mark reviewed if it's still "
            "accurate.")


def request_reviews(
    db: Session,
    *,
    document_ids: Optional[List[str]] = None,
    requested_by: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Ask owners to review documents now, through the Teams reminder channel.

    Without ``document_ids``: every published document that is stale or has
    never been marked reviewed (the way to start a catalog's first round of
    ownership), skipping documents reminded within the re-notify window so a
    repeat request doesn't ping owners twice. With ``document_ids``: exactly
    those documents, whatever their state, ignoring the re-notify window —
    the admin asked for them by name.
    """
    logger.info(
        "Context Catalog review requested by %s for %s",
        requested_by or "unknown", f"{len(document_ids)} document(s)" if document_ids is not None else "all",
    )
    if document_ids is not None:
        ids = list(dict.fromkeys(i for i in document_ids if i))
        return _locked_run(
            db, "request", now,
            lambda at: find_stale_documents(db, now=at, document_ids=ids),
            intro=_request_intro(len(ids) == 1),
        )
    return _locked_run(
        db, "request_all", now,
        lambda at: find_stale_documents(
            db, now=at, renotify_after_days=renotify_days(), include_never_reviewed=True,
        ),
        intro=_request_intro(False),
    )


def send_test_message(requested_by: Optional[str] = None) -> teams.PostResult:
    """Post a short connectivity check, mentioning the admin who asked when possible."""
    url = webhook_url()
    if not url:
        return teams.PostResult(ok=False, error="The Teams webhook URL is not configured.")
    brand = teams.card_text(settings.BRAND_NAME)
    mention = teams.mention_for(requested_by) if teams.is_email(requested_by) else None
    who = f"{mention.text} sent this test" if mention else "An admin sent this test"
    body = [
        {"type": "TextBlock", "text": f"{brand}: Context Catalog reminders are connected",
         "size": "Medium", "weight": "Bolder", "wrap": True},
        {"type": "TextBlock", "wrap": True,
         "text": (f"{who}. Stale-document reminders will be posted in this channel, mentioning each "
                  "document's owner.")},
    ]
    link = catalog_url()
    actions = [{"type": "Action.OpenUrl", "title": "Open Context Catalog", "url": link}] if link else None
    message = teams.adaptive_card_message(body, mentions=[mention] if mention else None, actions=actions)
    return teams.post_message(url, message)


def reminder_status(db: Session, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Configuration + the current stale set, for the admin reminders panel."""
    stale = find_stale_documents(db, now=now, renotify_after_days=renotify_days())
    needs_review = find_stale_documents(
        db, now=now, renotify_after_days=renotify_days(), include_never_reviewed=True,
    )
    env_value, scope, key = _webhook_args()
    return {
        "enabled": reminders_enabled(),
        "cron": (getattr(settings, "CONTEXT_CATALOG_STALE_REMINDER_CRON", "") or "").strip(),
        "stale_after_days": stale_after_days(),
        "renotify_days": renotify_days(),
        "max_docs_per_owner": max_docs_per_owner(),
        "webhook_configured": bool(webhook_url()),
        "webhook_source": teams.webhook_source(env_value, scope, key),
        "app_base_url_set": bool(_app_url()),
        "next_run_at": _next_run_iso(),
        "last_run": last_run_report(),
        "stale_documents": [d.to_dict() for d in stale],
        # What "Request review from owners" would cover: stale or never reviewed, and how many
        # of those haven't been reminded within the re-notify window.
        "needs_review_count": len(needs_review),
        "needs_review_due": sum(1 for d in needs_review if not d.recently_notified),
    }


def mark_reviewed(db: Session, document_id: str, reviewer: Optional[str]) -> ContextDocumentModel:
    """Confirm a document is still accurate without editing it (restarts its staleness clock)."""
    doc = db.query(ContextDocumentModel).filter(ContextDocumentModel.id == document_id).first()
    if not doc:
        raise ValueError("Document not found")
    doc.last_reviewed_at = datetime.utcnow()
    doc.last_reviewed_by = reviewer
    doc.stale_notified_at = None
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# ------------------------------------------------------------------ run bookkeeping

_last_run: Optional[Dict[str, Any]] = None


def record_run(report: Dict[str, Any]) -> None:
    global _last_run
    _last_run = dict(report)


def last_run_report() -> Optional[Dict[str, Any]]:
    return dict(_last_run) if _last_run else None


def _next_run_iso() -> Optional[str]:
    import sys

    task = sys.modules.get("app.workers.tasks.context_catalog_reminders")
    nxt = getattr(task, "_next_reminder_time", None) if task else None
    return nxt.isoformat() if nxt else None
