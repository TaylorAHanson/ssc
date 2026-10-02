"""Parse the Databricks docs release-notes RSS feed for preview announcements.

The feed is product release notes. Phase (Beta / Public Preview / GA) and scope
(account console vs workspace) are free text, so both are classified with
simple rules here. Everything taken from the feed is untrusted: it is stored as
plain text and quoted with ``safe_text`` in approval reports.

The feed is used to:
* attach an announcement, date, release-note link and docs link to features the
  workspace API already found (matched by display name), and
* find account-level previews the workspace API doesn't list at all.
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

DOCS_ORIGIN = "https://docs.databricks.com"

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r"<a\s[^>]*href=\"([^\"]+)\"", re.IGNORECASE)
_STRONG_RE = re.compile(r"<strong>(.*?)</strong>", re.IGNORECASE | re.DOTALL)
_SEGMENT_SPLIT_RE = re.compile(r"</?(?:p|li)\b[^>]*>", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")

_PUBLIC_PREVIEW_RE = re.compile(r"\bpublic preview\b", re.IGNORECASE)
_PRIVATE_PREVIEW_RE = re.compile(r"\bprivate preview\b", re.IGNORECASE)
_BETA_RE = re.compile(r"\bbeta\b", re.IGNORECASE)
_GA_RE = re.compile(r"\bgenerally available\b|\bis now GA\b|\(GA\)|\bin GA\b", re.IGNORECASE)
_ACCOUNT_RE = re.compile(r"account console|account admins?\b", re.IGNORECASE)
_WORKSPACE_RE = re.compile(r"workspace admins?\b|workspace'?s? previews? page|previews page in (?:the|your) workspace",
                           re.IGNORECASE)

# Words that carry no meaning when matching a display name against feed text.
_STOPWORDS = {"the", "a", "an", "for", "and", "of", "in", "on", "to", "with", "new", "databricks"}


@dataclass
class FeedItem:
    title: str
    link: str
    published: Optional[datetime]
    description_html: str
    categories: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return html_to_text(self.description_html)

    @property
    def search_text(self) -> str:
        return normalize(f"{self.title} {self.text}")


def html_to_text(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def name_tokens(name: str) -> List[str]:
    return [w for w in _WORD_RE.findall((name or "").lower()) if w not in _STOPWORDS]


def parse_feed(xml_text: str) -> List[FeedItem]:
    """Items from an RSS 2.0 document, newest first. Bad XML returns []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("Docs feed is not valid XML: %s", e)
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    items: List[FeedItem] = []
    for it in channel.findall("item"):
        published = None
        raw_date = it.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).replace(tzinfo=None)
            except (TypeError, ValueError):
                published = None
        items.append(FeedItem(
            title=(it.findtext("title") or "").strip(),
            link=(it.findtext("link") or it.findtext("guid") or "").strip(),
            published=published,
            description_html=it.findtext("description") or "",
            categories=[(c.text or "").strip() for c in it.findall("category") if c.text],
        ))
    items.sort(key=lambda i: i.published or datetime.min, reverse=True)
    return items


def classify_phase(text: str) -> Optional[str]:
    """The preview phase the text announces, ``GA``, or None."""
    if _PRIVATE_PREVIEW_RE.search(text):
        return "PRIVATE_PREVIEW"
    if _PUBLIC_PREVIEW_RE.search(text):
        return "PUBLIC_PREVIEW"
    if _BETA_RE.search(text):
        return "BETA"
    if _GA_RE.search(text):
        return "GA"
    return None


def classify_scope(text: str) -> str:
    """``account`` when an account admin enables it from the account console."""
    if _ACCOUNT_RE.search(text):
        return "account"
    if _WORKSPACE_RE.search(text):
        return "workspace"
    return "unknown"


def _absolute(href: str) -> str:
    return urljoin(DOCS_ORIGIN + "/", html.unescape(href))


def _is_feature_doc(url: str) -> bool:
    """A docs page that isn't itself a release note."""
    parsed = urlparse(url)
    if parsed.hostname != urlparse(DOCS_ORIGIN).hostname:
        return False
    return "/release-notes/" not in parsed.path


def doc_links(fragment: str) -> List[str]:
    out: List[str] = []
    for href in _HREF_RE.findall(fragment or ""):
        url = _absolute(href)
        if _is_feature_doc(url) and url not in out:
            out.append(url)
    return out


def segments(item: FeedItem) -> List[str]:
    """The item's paragraphs / list entries as HTML fragments."""
    parts = [p for p in _SEGMENT_SPLIT_RE.split(item.description_html or "") if html_to_text(p)]
    return parts or [item.description_html or ""]


def mentions(display_name: str, text_norm: str) -> bool:
    """Does normalized text name ``display_name``?

    An exact phrase match always counts. Otherwise every meaningful word of a
    multi-word name must appear close together (within a few words of each
    other), so "Production Monitoring for MLflow" doesn't match an item that
    happens to say "production", "monitoring" and "MLflow" in different places.
    """
    phrase = normalize(display_name)
    if not phrase:
        return False
    padded = f" {text_norm} "
    if f" {phrase} " in padded:
        return True
    tokens = name_tokens(display_name)
    # Two-word names ("AI Enrich") must appear as a phrase: "ai_search to enrich"
    # would otherwise count. The loose match is for longer names whose wording
    # gets reordered ("Lakeflow Connect for Aha!" vs "Aha! connector ... Lakeflow Connect").
    if len(tokens) < 3:
        return False
    words = text_norm.split()
    wanted = set(tokens)
    window = len(tokens) + 2
    positions = [i for i, w in enumerate(words) if w in wanted]
    for start in positions:
        if wanted <= set(words[start:start + window]):
            return True
    return False


@dataclass
class FeedMatch:
    item: FeedItem
    segment_html: str
    phase: Optional[str]
    scope: str
    docs_link: Optional[str]

    @property
    def announcement_text(self) -> str:
        # A roundup item ("Dashboard enhancements") covers many features, so
        # quote only the entry about this one; a dedicated item reads best whole.
        if len(segments(self.item)) > 3:
            return html_to_text(self.segment_html) or self.item.text
        return self.item.text


def _match_in(display_name: str, item: FeedItem) -> FeedMatch:
    best = None
    for seg in segments(item):
        if mentions(display_name, normalize(html_to_text(seg))):
            best = seg
            break
    seg = best or item.description_html
    # A title like "X is in Beta" carries the phase even when the paragraph doesn't.
    phase = classify_phase(html_to_text(seg)) or classify_phase(item.title)
    links = doc_links(seg) or doc_links(item.description_html)
    return FeedMatch(
        item=item, segment_html=seg, phase=phase,
        scope=classify_scope(item.text), docs_link=links[0] if links else None,
    )


def match_feature(display_name: str, items: Iterable[FeedItem]) -> Optional[FeedMatch]:
    """The feed item about ``display_name``, narrowed to the segment that names it.

    An item whose title names the feature beats one that only mentions it in
    passing; within each pass the newest wins (``items`` is newest first).
    """
    items = list(items)
    for item in items:
        if mentions(display_name, normalize(item.title)):
            return _match_in(display_name, item)
    for item in items:
        if mentions(display_name, item.search_text):
            return _match_in(display_name, item)
    return None


@dataclass
class AccountPreview:
    """A preview an account admin turns on from the account console."""
    name: str
    key: str
    phase: str
    match: FeedMatch


_NAMED_PREVIEW_RE = re.compile(
    r"<strong>([^<]+)</strong>\s*(?:preview\b|\((?:beta|public preview|private preview)\))", re.IGNORECASE)
_TITLE_PHASE_RE = re.compile(
    r"\s+(?:is|are)\s+(?:now\s+)?(?:in\s+|available in\s+)?(?:beta|public preview|private preview)\b.*$",
    re.IGNORECASE)


def _preview_name(segment_html: str, title: str) -> str:
    """The preview's name: "<strong>X</strong> preview" or "<strong>X</strong> (Beta)", else the title."""
    m = _NAMED_PREVIEW_RE.search(segment_html or "")
    if m:
        name = re.sub(r"\s+preview$", "", html_to_text(m.group(1)), flags=re.IGNORECASE)
        if name:
            return name
    cleaned = _TITLE_PHASE_RE.sub("", title)
    cleaned = re.sub(r"\s*\((?:beta|public preview|private preview)\)\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or title


def account_previews(items: Iterable[FeedItem]) -> List[AccountPreview]:
    """Account-console previews announced in the feed, newest announcement per name."""
    found: Dict[str, AccountPreview] = {}
    for item in items:
        for seg in segments(item):
            seg_text = html_to_text(seg)
            if not (_ACCOUNT_RE.search(seg_text) and re.search(r"\bpreviews?\b", seg_text, re.IGNORECASE)):
                continue
            phase = classify_phase(seg_text) or classify_phase(item.title)
            if phase not in ("BETA", "PUBLIC_PREVIEW", "PRIVATE_PREVIEW"):
                continue
            name = _preview_name(seg, item.title)
            key = normalize(name)
            if not key or key in found:
                continue
            links = doc_links(seg) or doc_links(item.description_html)
            found[key] = AccountPreview(
                name=name, key=key, phase=phase,
                match=FeedMatch(item=item, segment_html=seg, phase=phase, scope="account",
                                docs_link=links[0] if links else None),
            )
    return list(found.values())
