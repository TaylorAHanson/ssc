"""
Optional original-file storage for the Context Catalog.

When ``CONTEXT_CATALOG_VOLUME_PATH`` is configured, uploaded originals are
written to a Unity Catalog Volume via the Databricks SDK Files API (the same
mechanism used by ``VolumeGitOpsProvider``). When it is not configured (e.g.
local development), storage is a no-op and only the extracted text is kept in
the database.
"""
import logging
from io import BytesIO
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class ContextCatalogStorage:
    """Best-effort storage of uploaded document originals on a UC Volume."""

    def __init__(self, volume_path: Optional[str] = None):
        self.volume_path = (volume_path or settings.CONTEXT_CATALOG_VOLUME_PATH or "").rstrip("/")
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.volume_path)

    @property
    def client(self):
        if self._client is None:
            from databricks.sdk import WorkspaceClient
            self._client = WorkspaceClient()
        return self._client

    def store_original(self, document_id: str, filename: str, content: bytes) -> Optional[str]:
        """Persist the original bytes. Returns the storage path, or None.

        Failures are logged but non-fatal: keeping the extracted text is the
        important part, so a volume hiccup must not block an upload.
        """
        if not self.enabled:
            return None
        safe_name = filename.replace("/", "_")
        path = f"{self.volume_path}/{document_id}/{safe_name}"
        try:
            self.client.files.upload(
                file_path=path,
                contents=BytesIO(content),
                overwrite=True,
            )
            logger.info("Stored context document original at %s", path)
            return path
        except Exception as e:  # noqa: BLE001 - storage is best-effort
            logger.warning("Failed to store context document original at %s: %s", path, e)
            return None

    def delete_original(self, storage_path: Optional[str]) -> None:
        if not storage_path or not self.enabled:
            return
        try:
            self.client.files.delete(file_path=storage_path)
        except Exception as e:  # noqa: BLE001 - cleanup is best-effort
            logger.warning("Failed to delete context document original at %s: %s", storage_path, e)
