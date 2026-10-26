"""
Microsoft Teams channel messages via a Teams **Workflows** (Power Automate)
incoming webhook.

Workflows webhooks ("Post to a channel when a webhook request is received")
accept a ``message`` envelope carrying one Adaptive Card attachment and support
real @mentions through ``msteams.entities``: the card text contains
``<at>Name</at>`` and a matching entity names the person by UPN/email (or AAD
object id). The legacy Office 365 connector ``MessageCard`` format, which the
generic ``NotificationProvider.send_teams`` and the ``send_notification``
workflow tool still emit, cannot mention anyone and is being retired by
Microsoft, so it isn't used here.

The webhook URL is a bearer credential (anyone holding it can post to the
channel). It is resolved from the environment first (local ``.env``), then
from a Databricks secret scope read by the app's own service principal, and is
never logged.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
ADAPTIVE_CARD_VERSION = "1.5"

# Teams rejects messages over ~28 KB; stay comfortably under it.
MAX_MESSAGE_BYTES = 24_000

_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")

# Secret reads are a Databricks API round-trip; cache briefly so a rotated or
# newly-added secret is picked up without a restart.
_SECRET_TTL_SECONDS = 600
_secret_cache: Dict[Tuple[str, str], Tuple[float, str]] = {}


def is_email(value: Optional[str]) -> bool:
    return bool(value and _EMAIL_RE.match(value.strip()))


def display_name_from_email(email: str) -> str:
    """``jane.doe@corp.com`` -> ``Jane Doe``. Teams resolves the person from the
    entity id; this is only the text shown inside the mention."""
    local = (email or "").split("@", 1)[0]
    parts = [p for p in re.split(r"[._\-+]+", local) if p]
    return " ".join(p.capitalize() for p in parts) or email


def card_text(value: Optional[str]) -> str:
    """Neutralize user-authored text (titles, names) before it goes in a card.

    Angle brackets are dropped so a title can't forge an ``<at>`` mention, and
    square brackets are swapped so it can't forge a markdown link.
    """
    text = (value or "").replace("<", "").replace(">", "")
    text = text.replace("[", "(").replace("]", ")")
    return " ".join(text.split())


@dataclass
class Mention:
    """One @mention: ``text`` goes in the card body, ``entity`` in msteams.entities."""

    id: str
    name: str

    @property
    def text(self) -> str:
        return f"<at>{self.name}</at>"

    @property
    def entity(self) -> Dict[str, Any]:
        return {"type": "mention", "text": self.text, "mentioned": {"id": self.id, "name": self.name}}


def mention_for(email: str, name: Optional[str] = None) -> Mention:
    return Mention(id=email.strip(), name=card_text(name) or display_name_from_email(email))


def adaptive_card_message(
    body: List[Dict[str, Any]],
    *,
    mentions: Optional[List[Mention]] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Wrap Adaptive Card body elements in the Workflows webhook message envelope."""
    content: Dict[str, Any] = {
        "$schema": ADAPTIVE_CARD_SCHEMA,
        "type": "AdaptiveCard",
        "version": ADAPTIVE_CARD_VERSION,
        "body": body,
        "msteams": {"width": "Full"},
    }
    if actions:
        content["actions"] = actions
    # One entity per distinct mention text; Teams pairs them by exact text.
    entities: Dict[str, Dict[str, Any]] = {}
    for m in mentions or []:
        entities.setdefault(m.text, m.entity)
    if entities:
        content["msteams"]["entities"] = list(entities.values())
    return {
        "type": "message",
        "attachments": [
            {"contentType": ADAPTIVE_CARD_CONTENT_TYPE, "contentUrl": None, "content": content}
        ],
    }


def _read_secret(scope: str, key: str) -> str:
    cache_key = (scope, key)
    cached = _secret_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _SECRET_TTL_SECONDS:
        return cached[1]
    value = ""
    try:
        from databricks.sdk import WorkspaceClient

        secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
        if secret and secret.value:
            value = base64.b64decode(secret.value).decode("utf-8").strip()
    except Exception as e:  # noqa: BLE001 - a missing secret just means "not configured"
        logger.warning(
            "Could not read the Teams webhook secret %s/%s: %s — check the key exists in the "
            "scope and the app's service principal has READ on it.", scope, key, e,
        )
    _secret_cache[cache_key] = (time.monotonic(), value)
    return value


def resolve_webhook_url(env_value: Optional[str], scope: Optional[str], key: Optional[str]) -> str:
    """The webhook URL from the environment, else from ``scope``/``key``; '' if neither."""
    if env_value and env_value.strip():
        return env_value.strip()
    if scope and key:
        return _read_secret(scope, key)
    return ""


def webhook_source(env_value: Optional[str], scope: Optional[str], key: Optional[str]) -> str:
    """Where the URL would come from, for the admin status view (never the URL itself)."""
    if env_value and env_value.strip():
        return "environment"
    if scope and key:
        return f"secret {scope}/{key}"
    return "not configured"


@dataclass
class PostResult:
    ok: bool
    status_code: Optional[int] = None
    error: Optional[str] = None


def post_message(webhook_url: str, message: Dict[str, Any], *, timeout: float = 15.0) -> PostResult:
    """POST one message to a Workflows webhook. Never raises; never logs the URL.

    Workflows answers ``202 Accepted`` once the flow is triggered; any 2xx is
    success. The flow itself can still fail afterwards (e.g. the flow owner lost
    access to the channel) — that shows in the flow's run history, not here.
    """
    if not webhook_url:
        return PostResult(ok=False, error="Teams webhook URL is not configured")
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(webhook_url, json=message)
        if 200 <= response.status_code < 300:
            return PostResult(ok=True, status_code=response.status_code)
        detail = (response.text or "")[:300]
        logger.warning("Teams webhook returned HTTP %s: %s", response.status_code, detail)
        return PostResult(ok=False, status_code=response.status_code, error=f"HTTP {response.status_code}: {detail}")
    except httpx.HTTPError as e:
        logger.warning("Teams webhook request failed: %s", type(e).__name__)
        return PostResult(ok=False, error=f"Request failed: {type(e).__name__}")
