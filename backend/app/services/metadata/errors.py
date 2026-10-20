"""Metadata Manager exceptions. Messages are written for the caller; the API maps each to a status code."""
from typing import List, Optional

from app.services.metadata.types import ChangePreview, VersionConflictItem


class MetadataError(Exception):
    """Invalid input (400)."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(MetadataError):
    status_code = 404


class Forbidden(MetadataError):
    status_code = 403


class VersionConflict(MetadataError):
    """Someone changed a tag or description since the caller read it (409)."""

    status_code = 409

    def __init__(self, conflicts: List[VersionConflictItem], message: Optional[str] = None):
        names = ", ".join(sorted({f"{c.name} ({c.key or 'description'})" for c in conflicts}))
        super().__init__(message or f"Changed by someone else since you loaded it: {names}. Reload and try again.")
        self.conflicts = conflicts


class ChangeBlocked(MetadataError):
    """A policy finding denies the change (422). ``preview`` carries the findings."""

    status_code = 422

    def __init__(self, preview: ChangePreview, message: Optional[str] = None):
        denies = [f.message for f in preview.findings if f.severity == "deny"]
        denies += [f.message for d in preview.items for f in d.findings if f.severity == "deny"]
        super().__init__(message or (" ".join(dict.fromkeys(denies)) or "A metadata policy blocks this change."))
        self.preview = preview
