"""Client for the Databricks Academy course catalog.

Academy runs on Docebo, whose public catalog endpoint
(``<base>/learn/v1/courses``) returns every listed course as JSON without a
login: the same feed the Academy website renders from. Each item carries the
Docebo ``id_course``, the Academy course ``code``, name, type
(``elearning`` / ``classroom``), duration in seconds, language, price and slug.

Course codes look like ``ACAD-ALL-SLP-DINLC-EN-v3``: audience, delivery,
an acronym for the course, language and version. The same course is listed
once per delivery and language, and a new version gets a new code, so codes
are compared by their acronym (``course_stem``) rather than verbatim.

Fetches go through the SSRF-safe ``safe_fetch``, limited to databricks.com.
"""
import html
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode, urlparse

from app.tools.web._common import safe_fetch

logger = logging.getLogger(__name__)

_ALLOWED_DOMAINS = ["databricks.com"]
_PAGE_SIZE = 100
_MAX_PAGES = 50
_DESCRIPTION_CHARS = 600

# Code tokens that describe how or for whom a course is offered, not which
# course it is. Whatever remains is the course's acronym.
_AUDIENCE = {"ALL", "CUST", "PART", "PARTNER", "INT"}
_DELIVERY = {"SLP", "SLPPUB", "ILT", "ILTPUB", "ILTB", "ILTPRIV", "PUB", "OD", "VILT", "FREE", "PAID"}
_LANGUAGE = {"EN", "ENG", "JA", "JP", "KO", "KR", "ZH", "CN", "ES", "FR", "BR", "PT", "DE", "IT", "ILF"}
_MARKERS = {"ACAD", "RET"}
_VERSION_RE = re.compile(r"^V\d+(\.\d+)*$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def course_stem(code: Optional[str]) -> Optional[str]:
    """The acronym part of an Academy course code, or None if it has none.

    ``ACAD-ALL-SLP-FREE-DINLC-EN-v1`` and ``ACAD-CUST-SLP-DINLC-EN-v3`` both
    give ``DINLC``. Codes that aren't Academy-shaped give None, so callers
    fall back to comparing the code verbatim.
    """
    tokens = [t for t in re.split(r"[-\s]+", (code or "").strip().upper()) if t]
    if not tokens or tokens[0] != "ACAD":
        return None
    rest = [
        t for t in tokens
        if t not in _AUDIENCE | _DELIVERY | _LANGUAGE | _MARKERS and not _VERSION_RE.match(t)
    ]
    return "-".join(rest) or None


def codes_match(required: Optional[str], completed: Iterable[str]) -> bool:
    """True if any completed code is the required course, in any version or delivery."""
    if not required:
        return False
    required = required.strip()
    stem = course_stem(required)
    for code in completed:
        if not code:
            continue
        if code.strip() == required:
            return True
        if stem and course_stem(code) == stem:
            return True
    return False


def course_url(base_url: str, course_type: str, course_id: int, slug: str) -> str:
    """Deep link to a course's public Academy page (no login needed to view it)."""
    kind = "classroom" if course_type == "classroom" else "elearning"
    return f"{base_url.rstrip('/')}/learn/course/external/view/{kind}/{course_id}/{slug}"


def format_duration(seconds: Optional[int]) -> Optional[str]:
    """``7200`` -> ``2 hrs``; ``2700`` -> ``45 min``; ``5400`` -> ``1.5 hrs``."""
    if not seconds or seconds <= 0:
        return None
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    hours = round(seconds / 3600, 1)
    text = f"{hours:g}"
    return f"{text} hr" if hours == 1 else f"{text} hrs"


def _plain_text(raw: Optional[str]) -> str:
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > _DESCRIPTION_CHARS:
        text = text[:_DESCRIPTION_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def _is_free(price: Any) -> bool:
    try:
        return float(price or 0) == 0
    except (TypeError, ValueError):
        return False


def normalize(item: Dict[str, Any], base_url: str) -> Optional[Dict[str, Any]]:
    """One Docebo catalog item as the fields we store, or None if unusable."""
    try:
        course_id = int(item.get("id_course"))
    except (TypeError, ValueError):
        return None
    name = (item.get("name") or "").strip()
    if not name:
        return None
    course_type = (item.get("course_type") or "elearning").strip().lower()
    slug = (item.get("slug_name") or "").strip()
    code = (item.get("code") or "").strip() or None
    try:
        duration = int(item.get("duration") or 0) or None
    except (TypeError, ValueError):
        duration = None
    return {
        "id": course_id,
        "code": code,
        "stem": course_stem(code),
        "name": name,
        "description": _plain_text(item.get("description")),
        "course_type": course_type,
        "duration_seconds": duration,
        "language": (item.get("language") or "").strip().lower() or None,
        "is_free": _is_free(item.get("price")),
        "category": ((item.get("category") or {}).get("name") or None),
        "slug": slug,
        "url": course_url(base_url, course_type, course_id, slug) if slug else None,
        "image_url": item.get("img_url") or None,
        "updated_at": item.get("date_last_updated") or None,
    }


async def fetch_catalog(base_url: str, languages: Optional[List[str]] = None) -> Dict[str, Any]:
    """Fetch every catalog page.

    Returns ``{"ok": bool, "courses": [...], "error": str}`` and never raises.
    ``languages`` (Docebo names such as ``english``) limits which courses are
    kept; empty keeps all of them.
    """
    base_url = (base_url or "").strip().rstrip("/")
    host = (urlparse(base_url).hostname or "").lower()
    if not base_url or not host:
        return {"ok": False, "courses": [], "error": "No Academy URL is set."}
    if not (host == "databricks.com" or host.endswith(".databricks.com")):
        return {"ok": False, "courses": [], "error": f"{host} is not a databricks.com address."}

    wanted = {lang.strip().lower() for lang in (languages or []) if lang and lang.strip()}
    courses: List[Dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        url = f"{base_url}/learn/v1/courses?{urlencode({'page_size': _PAGE_SIZE, 'page': page})}"
        res = await safe_fetch(url, allowed_domains=_ALLOWED_DOMAINS, extract=False, timeout=30,
                               accept="application/json")
        if not res.get("ok"):
            return {"ok": False, "courses": [], "error": f"Page {page}: {res.get('error') or 'fetch failed'}"}
        try:
            data = json.loads(str(res.get("raw") or ""))["data"]
            items = data.get("items") or []
        except (ValueError, KeyError, TypeError, AttributeError):
            return {"ok": False, "courses": [], "error": f"Page {page} wasn't the expected catalog JSON."}
        for item in items:
            course = normalize(item, base_url)
            if course and (not wanted or course["language"] in wanted):
                courses.append(course)
        if not data.get("has_more_data") or not items:
            break
    else:
        logger.warning("Academy catalog: stopped after %d pages", _MAX_PAGES)

    if not courses:
        return {"ok": False, "courses": [], "error": "The catalog returned no courses in the chosen languages."}
    return {"ok": True, "courses": courses, "error": ""}
