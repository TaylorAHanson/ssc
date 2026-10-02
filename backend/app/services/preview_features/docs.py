"""Find a documentation page for a preview by matching its name to the docs sitemap.

This is the last automatic fallback (after the metadata ``docs_link`` and the
feed item's link), so results are labeled "suggested" in the UI. A URL slug is
the only signal the sitemap carries, so the match is deliberately strict.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from app.services.preview_features.feed import name_tokens

_SLUG_WORD_RE = re.compile(r"[a-z0-9]+")
# Settings often carry a product prefix the doc slug drops ("Lakeflow Connect for Jira" -> /jira).
_PREFIXES = ("lakeflow connect for", "lakeflow connector for", "lakeflow connect", "ai bi")
_MIN_SCORE = 0.75
# Reference pages that share words with feature names but don't document them.
_SKIP_PATHS = ("/release-notes/", "/error-messages/", "/pyspark/reference/")


def _core_tokens(display_name: str) -> List[str]:
    name = " ".join(_SLUG_WORD_RE.findall(display_name.lower()))
    for prefix in _PREFIXES:
        if name.startswith(prefix + " "):
            name = name[len(prefix) + 1:]
            break
    return name_tokens(name)


def exact_doc(setting_name: Optional[str], urls: Iterable[str]) -> Optional[str]:
    """A docs page named exactly after the setting (``.../functions/ai_enrich``), or None.

    Function previews are documented under their own name, so this is a strong
    signal, stronger than the release-note link.
    """
    if not setting_name:
        return None
    urls = list(urls)
    # The literal name first (SQL function pages use it), then the hyphenated slug.
    for wanted in (setting_name.lower(), setting_name.lower().replace("_", "-")):
        for url in urls:
            path = urlparse(url).path.rstrip("/")
            if any(part in path for part in _SKIP_PATHS):
                continue
            if path.rsplit("/", 1)[-1].lower() == wanted:
                return url
    return None


def suggest_doc(display_name: str, urls: Iterable[str]) -> Optional[str]:
    """The best docs URL whose last path segment covers the feature's name, or None.

    Score = share of the name's words found in the page's last slug, minus a
    small penalty for extra slug words; ties go to the shorter (more general) path.
    """
    tokens = _core_tokens(display_name)
    if not tokens:
        return None
    wanted = set(tokens)
    best: Optional[str] = None
    best_key = None
    for url in urls:
        path = urlparse(url).path.rstrip("/")
        if any(part in path for part in _SKIP_PATHS):
            continue
        last = set(_SLUG_WORD_RE.findall(path.rsplit("/", 1)[-1].lower()))
        if not last:
            continue
        hit = len(wanted & last) / len(wanted)
        if hit < 1.0 and len(wanted) < 3:
            continue
        score = hit - 0.05 * len(last - wanted)
        if score < _MIN_SCORE:
            continue
        key = (score, -len(path))
        if best_key is None or key > best_key:
            best, best_key = url, key
    return best
