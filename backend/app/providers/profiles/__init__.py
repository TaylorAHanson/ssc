"""Agent profile loading (read-only).

Profiles are authored in the Command Center's Agent Studio and stored as
``AGENT.md`` (+ ``skills/*.md``) on UC Volumes / the user's Workspace folder.
This package only *loads* a profile for a single chat request under the
caller's OBO token; it never writes.
"""
from app.providers.profiles.client import (  # noqa: F401
    LoadedProfile,
    ProfileError,
    ProfileProvider,
    get_profile_provider,
)
