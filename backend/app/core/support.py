"""
The configured day-to-day support contact.

Set under Admin -> Settings -> Links & Embedded Apps (``support.name``,
``support.email``, ``support.notes``). Blank by default, so nothing is shown or
told to the assistant until an admin fills it in.
"""
from typing import Dict

from app.core.config import _yaml_config

_FIELDS = ("name", "email", "notes")


def get_support_contact() -> Dict[str, str]:
    """The non-empty support fields, or ``{}`` when no contact is configured."""
    raw = _yaml_config.get("support") or {}
    if not isinstance(raw, dict):
        return {}
    contact = {k: str(raw.get(k) or "").strip() for k in _FIELDS}
    contact = {k: v for k, v in contact.items() if v}
    # Notes alone ("see #data-help") are still worth showing; nothing at all is not.
    return contact
