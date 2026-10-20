"""What the Databricks Apps proxy forwarded about the caller (ADR-0006 D7).

The proxy sets ``X-Forwarded-Email``, ``X-Forwarded-User`` and
``X-Forwarded-Preferred-Username`` on every request it authenticates, and
``X-Forwarded-Access-Token`` when user authorization is on. For a service
principal calling with an OAuth M2M token the values aren't documented, so a
caller is treated as a possible service principal whenever it carries an
identifier but no email address, and is matched against registered principals
on every identifier it carries.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

EMAIL_HEADER = "x-forwarded-email"
USER_HEADER = "x-forwarded-user"
USERNAME_HEADER = "x-forwarded-preferred-username"
TOKEN_HEADER = "x-forwarded-access-token"
IDENTITY_HEADERS = (EMAIL_HEADER, USER_HEADER, USERNAME_HEADER)

# Header shapes already logged for an unrecognized caller, so each is logged once per process.
_logged_shapes: set = set()


def forwarded_identity(headers: Mapping[str, str]) -> Dict[str, object]:
    """The identity headers actually present (None when absent), and which ones were seen."""
    def _get(name: str) -> Optional[str]:
        value = (headers.get(name) or "").strip()
        return value or None

    seen: List[str] = [h for h in IDENTITY_HEADERS + (TOKEN_HEADER,) if _get(h)]
    return {
        "email": _get(EMAIL_HEADER),
        "user_id": _get(USER_HEADER),
        "username": _get(USERNAME_HEADER),
        "headers_seen": seen,
    }


def is_email_address(value: Optional[str]) -> bool:
    return bool(value) and "@" in str(value) and not str(value).startswith("@")


def identifiers(fwd: Mapping[str, object]) -> List[str]:
    """Every forwarded identifier, in the order principals are matched on."""
    found: List[str] = []
    for key in ("user_id", "username", "email"):
        value = fwd.get(key)
        if isinstance(value, str) and value and value not in found:
            found.append(value)
    return found


def header_shape(fwd: Mapping[str, object]) -> Dict[str, object]:
    """Which identity headers were present and what kind of value each held. Never token values."""
    def _kind(value: object) -> Optional[str]:
        if not isinstance(value, str) or not value:
            return None
        if is_email_address(value):
            return "email"
        if len(value) == 36 and value.count("-") == 4:
            return "uuid"
        if value.isdigit():
            return "numeric"
        return "text"

    return {
        "headers": list(fwd.get("headers_seen") or []),
        "email": _kind(fwd.get("email")),
        "user": _kind(fwd.get("user_id")),
        "username": _kind(fwd.get("username")),
    }


def log_unrecognized_shape(fwd: Mapping[str, object]) -> None:
    shape = header_shape(fwd)
    key = repr(sorted(shape.items()))
    if key in _logged_shapes:
        return
    _logged_shapes.add(key)
    logger.info(
        "Caller with no email identity and no registered service principal. Identity headers present: %s "
        "(email=%s, user=%s, username=%s). Register one of its identifiers under Metadata Manager -> "
        "System principals; GET /api/v1/metadata/whoami shows the values.",
        shape["headers"], shape["email"], shape["user"], shape["username"],
    )
