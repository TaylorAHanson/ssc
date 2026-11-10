"""
Configured links to Databricks experiences outside this app.

Set under Admin -> Settings -> Links & Embedded Apps. ``links.genie_one_url`` is
account-level Genie One: the sidebar's Genie One link, and where the assistant
sends people who want to analyze data. Blank by default, which hides the link.
"""
from app.core.config import _yaml_config


def genie_one_url() -> str:
    """The account-level Genie One address with a scheme, or "" when unset."""
    links = _yaml_config.get("links") or {}
    raw = str(links.get("genie_one_url") or "").strip() if isinstance(links, dict) else ""
    if not raw:
        return ""
    return raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
