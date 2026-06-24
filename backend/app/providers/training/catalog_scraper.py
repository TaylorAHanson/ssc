"""Scraper for the public Databricks training catalog.

There is no broadly-available customer-facing Training/Academy API. The public
catalog at ``databricks.com/training/catalog`` does expose stable course-detail
URLs, so the "Sync from Catalog" admin action fetches that page and extracts
``(title, deeplink)`` pairs to seed/refresh ``source="catalog"`` courses.

Scraping a marketing site is inherently brittle (markup changes, JS rendering).
This parser is deliberately defensive: it pulls every anchor that points at a
course-detail-looking path, de-duplicates, and returns a structured result with
a ``note`` so the UI can explain when nothing was found (e.g. the page rendered
client-side). Fetches go through the SSRF-safe ``safe_fetch`` with an explicit
allowlist for the catalog host.
"""
import logging
import re
from typing import Dict, List
from urllib.parse import urljoin, urlparse

from app.tools.web._common import safe_fetch

logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = ["databricks.com", "www.databricks.com"]

# A course-detail URL lives under the training catalog path with at least one
# slug segment beyond the catalog root (so the catalog index itself is skipped).
_COURSE_PATH_RE = re.compile(r"/training/catalog/[a-z0-9][a-z0-9\-/]+", re.IGNORECASE)
# Anchor href + inner text, tolerant of attribute ordering.
_ANCHOR_RE = re.compile(
    r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = _WS_RE.sub(" ", text).strip()
    return text


def _looks_like_course(path: str) -> bool:
    if not _COURSE_PATH_RE.search(path):
        return False
    # Drop obvious non-course leaves (anchors, query-only, the index page).
    tail = path.rstrip("/").rsplit("/", 1)[-1].lower()
    if tail in {"catalog", "training"}:
        return False
    return True


async def scrape_catalog(catalog_url: str) -> Dict[str, object]:
    """Fetch + parse the catalog page.

    Returns ``{"ok": bool, "courses": [{"title", "url"}], "note": str}``.
    Never raises — failures come back as ``ok=False`` with a ``note``.
    """
    res = await safe_fetch(
        catalog_url,
        allowed_domains=_ALLOWED_HOSTS,
        extract=False,
        max_chars=5_000_000,
    )
    if not res.get("ok"):
        return {
            "ok": False,
            "courses": [],
            "note": f"Could not fetch the catalog page: {res.get('error', 'unknown error')}",
        }

    html = str(res.get("raw", ""))
    base = str(res.get("url") or catalog_url)
    seen: Dict[str, str] = {}
    for href, inner in _ANCHOR_RE.findall(html):
        if not _looks_like_course(href):
            continue
        url = urljoin(base, href)
        # Re-validate the resolved host stays on the catalog domain.
        host = (urlparse(url).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS):
            continue
        # Strip fragments/queries for a stable key.
        clean_url = url.split("#")[0].split("?")[0]
        title = _clean_text(inner)
        if not title:
            continue
        # First non-empty title wins for a given URL.
        if clean_url not in seen:
            seen[clean_url] = title

    courses: List[Dict[str, str]] = [
        {"title": title, "url": url} for url, title in seen.items()
    ]
    courses.sort(key=lambda c: c["title"].lower())

    if not courses:
        note = (
            "Fetched the catalog page but found no course links. The catalog may "
            "render its course list client-side; you can still add catalog "
            "courses manually with their detail URL as the deeplink."
        )
        return {"ok": True, "courses": [], "note": note}

    return {"ok": True, "courses": courses, "note": f"Found {len(courses)} catalog courses."}
