"""Shared safety + helper layer for the web lookup tools.

Everything that touches the network goes through here so the allowlist and
SSRF protections are enforced in exactly one place:

* ``is_allowed_url`` — scheme + domain allowlist (suffix match).
* ``_host_is_public`` — resolves the host and rejects private/loopback/
  link-local/reserved IPs (blocks cloud metadata endpoints like
  169.254.169.254 and internal services).
* ``safe_fetch`` — async GET that re-validates every redirect hop against
  both checks above, caps the response size, and returns extracted text.
* ``get_sitemap_urls`` — cached sitemap fetch used for keyless doc discovery.

Web content is untrusted: callers must treat returned text as *reference
material only* and never let it drive privileged actions (prompt injection).
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_USER_AGENT = "EnterpriseDataHub-DocsBot/1.0 (+internal agent; respects robots)"
_MAX_REDIRECTS = 4
# Cap raw bytes pulled before extraction so a huge page can't blow memory.
_MAX_RESPONSE_BYTES = 3_000_000
_SITEMAP_TTL_SECONDS = 3600

# url -> (fetched_at_epoch, [page_urls])
_sitemap_cache: Dict[str, Tuple[float, List[str]]] = {}


def web_config() -> dict:
    """Normalized web-lookup config (allowlist, sitemaps, limits)."""
    return settings.web_search_config()


def _host_allowed(host: str, allowed_domains: List[str]) -> bool:
    host = (host or "").lower().strip()
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in allowed_domains)


def _host_is_public(host: str) -> bool:
    """True only if every resolved IP for ``host`` is a public address."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True


def is_allowed_url(url: str, allowed_domains: Optional[List[str]] = None) -> bool:
    """Allowlist + scheme gate. Does NOT resolve DNS (cheap pre-check)."""
    if allowed_domains is None:
        allowed_domains = web_config()["allowed_domains"]
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    return _host_allowed(parsed.hostname or "", allowed_domains)


def _validate(url: str, allowed_domains: List[str]) -> Optional[str]:
    """Return an error string if ``url`` is not safe to fetch, else None."""
    if not is_allowed_url(url, allowed_domains):
        return "url is not https or its domain is not on the approved allowlist"
    host = urlparse(url).hostname or ""
    if not _host_is_public(host):
        return "host did not resolve, or resolves to a private/internal address"
    return None


# Tags whose contents are never useful page text.
_STRIP_TAGS = ["script", "style", "nav", "header", "footer", "aside", "noscript", "form"]


def extract_main_text(html: str, max_chars: int) -> Tuple[str, str]:
    """Return (title, cleaned_text) from an HTML document.

    Prefers the semantic main/article container and strips chrome. Falls
    back to a naive tag strip if BeautifulSoup isn't available.
    """
    try:
        from bs4 import BeautifulSoup  # imported lazily; optional dependency
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return ("", text[:max_chars])

    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    container = soup.find("main") or soup.find("article") or soup.body or soup
    text = container.get_text("\n", strip=True)
    # Collapse runs of blank lines the get_text join can leave behind.
    text = re.sub(r"\n{3,}", "\n\n", text)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip() + "\n\n[...truncated...]"
    return (title, text)


async def safe_fetch(
    url: str,
    *,
    timeout: Optional[float] = None,
    max_chars: Optional[int] = None,
    allowed_domains: Optional[List[str]] = None,
    extract: bool = True,
) -> Dict[str, object]:
    """SSRF-safe HTTPS GET with per-hop redirect validation.

    Returns ``{"ok": bool, "url": final_url, "status": int, "title": str,
    "text"/"raw": str, "error": str}``. Never raises for network/validation
    issues — the agent gets a structured error instead.
    """
    cfg = web_config()
    allowed = allowed_domains if allowed_domains is not None else cfg["allowed_domains"]
    timeout = timeout if timeout is not None else cfg["fetch_timeout_seconds"]
    max_chars = max_chars if max_chars is not None else cfg["max_fetch_chars"]

    current = url
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml"},
        ) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                err = _validate(current, allowed)
                if err:
                    return {"ok": False, "url": current, "status": 0, "error": err}

                resp = await client.get(current)

                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        return {"ok": False, "url": current, "status": resp.status_code, "error": "redirect without Location"}
                    current = urljoin(current, location)
                    continue

                if resp.status_code >= 400:
                    return {"ok": False, "url": current, "status": resp.status_code, "error": f"HTTP {resp.status_code}"}

                # Read body with a hard byte cap.
                raw = resp.content[:_MAX_RESPONSE_BYTES]
                body = raw.decode(resp.encoding or "utf-8", errors="replace")
                if not extract:
                    return {"ok": True, "url": str(resp.url), "status": resp.status_code, "raw": body}
                title, text = extract_main_text(body, max_chars)
                return {
                    "ok": True,
                    "url": str(resp.url),
                    "status": resp.status_code,
                    "title": title,
                    "text": text,
                }

            return {"ok": False, "url": current, "status": 0, "error": "too many redirects"}
    except httpx.TimeoutException:
        return {"ok": False, "url": current, "status": 0, "error": "request timed out"}
    except Exception as e:  # noqa: BLE001 - surface a clean message to the agent
        logger.warning("safe_fetch failed for %s: %s", url, e)
        return {"ok": False, "url": current, "status": 0, "error": f"fetch failed: {e}"}


_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


async def get_sitemap_urls(
    sitemaps: Optional[List[str]] = None,
    allowed_domains: Optional[List[str]] = None,
) -> List[str]:
    """Fetch + cache page URLs from the configured sitemap(s).

    Handles both flat ``<urlset>`` sitemaps and ``<sitemapindex>`` files
    (one level of recursion). Results are cached per-sitemap for an hour.
    Only URLs on the allowlist are returned.
    """
    cfg = web_config()
    sitemaps = sitemaps if sitemaps is not None else cfg["sitemaps"]
    allowed = allowed_domains if allowed_domains is not None else cfg["allowed_domains"]

    collected: List[str] = []
    seen = set()

    async def _pull(sm_url: str, depth: int) -> None:
        now = time.time()
        cached = _sitemap_cache.get(sm_url)
        if cached and (now - cached[0]) < _SITEMAP_TTL_SECONDS:
            locs = cached[1]
        else:
            res = await safe_fetch(sm_url, allowed_domains=allowed, extract=False)
            if not res.get("ok"):
                logger.info("sitemap fetch failed (%s): %s", sm_url, res.get("error"))
                _sitemap_cache[sm_url] = (now, [])
                return
            body = str(res.get("raw", ""))
            locs = _LOC_RE.findall(body)
            is_index = "<sitemapindex" in body.lower()
            _sitemap_cache[sm_url] = (now, locs)
            if is_index and depth == 0:
                # Recurse into child sitemaps (cap to keep it bounded).
                for child in locs[:25]:
                    await _pull(child, depth + 1)
                return
        for loc in locs:
            if loc in seen:
                continue
            if loc.endswith(".xml"):
                continue
            if is_allowed_url(loc, allowed):
                seen.add(loc)
                collected.append(loc)

    for sm in sitemaps:
        await _pull(sm, 0)
    return collected


def url_to_title(url: str) -> str:
    """Derive a human-ish title from a docs URL slug (cheap, no fetch)."""
    path = urlparse(url).path.strip("/")
    # Drop the cloud/lang prefix (e.g. aws/en/) for readability.
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] in {"aws", "azure", "gcp"} and len(parts[1]) <= 3:
        parts = parts[2:]
    tail = parts[-2:] if len(parts) >= 2 else parts
    words = " ".join(tail).replace("-", " ").replace("_", " ")
    return words.strip().title() or url
