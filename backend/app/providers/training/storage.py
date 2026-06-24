"""Storage for training media bytes.

Media (videos, PDFs, slide decks) is kept *out* of the database. When
``TRAINING_VOLUME_PATH`` is configured the bytes are written to a Unity Catalog
Volume via the Databricks SDK Files API (the same mechanism the Context Catalog
uses for uploaded originals). When it is not configured — e.g. local
development — bytes fall back to a local directory so the feature is fully
usable without a workspace.

Reads support HTTP Range so the learner UI can seek within a video. For local
files we ``seek``; for UC Volumes we stream sequentially and skip to the start
offset (a far seek re-reads from the beginning, which is acceptable for the
expected media sizes — revisit with a Range-aware REST call if needed).
"""
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Iterator, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bytes per chunk when streaming a media file back to the client.
_STREAM_CHUNK = 1024 * 256  # 256 KiB


class TrainingMediaStorage:
    """Persist + read back training media bytes (UC Volume or local fallback)."""

    def __init__(self, volume_path: Optional[str] = None, local_dir: Optional[str] = None):
        self.volume_path = (volume_path or settings.TRAINING_VOLUME_PATH or "").rstrip("/")
        self.local_dir = (local_dir or settings.TRAINING_LOCAL_MEDIA_DIR or "training_media").rstrip("/")
        self._client = None

    # ------------------------------------------------------------------ helpers

    @property
    def uses_volume(self) -> bool:
        return bool(self.volume_path)

    @property
    def client(self):
        if self._client is None:
            from databricks.sdk import WorkspaceClient

            self._client = WorkspaceClient()
        return self._client

    def _build_path(self, media_id: str, filename: str) -> str:
        safe_name = (filename or "media").replace("/", "_").replace("\\", "_")
        if self.uses_volume:
            return f"{self.volume_path}/{media_id}/{safe_name}"
        return os.path.join(self.local_dir, media_id, safe_name)

    # -------------------------------------------------------------------- write

    def store_media(self, media_id: str, filename: str, content: bytes) -> Tuple[str, int]:
        """Persist ``content`` and return ``(storage_path, size_bytes)``."""
        path = self._build_path(media_id, filename)
        size = len(content)
        if self.uses_volume:
            self.client.files.upload(file_path=path, contents=BytesIO(content), overwrite=True)
            logger.info("Stored training media at %s (%d bytes)", path, size)
        else:
            local = Path(path)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)
            logger.info("Stored training media locally at %s (%d bytes)", path, size)
        return path, size

    def delete_media(self, storage_path: Optional[str]) -> None:
        if not storage_path:
            return
        try:
            if self.uses_volume and storage_path.startswith("/Volumes"):
                self.client.files.delete(file_path=storage_path)
            else:
                p = Path(storage_path)
                if p.exists():
                    p.unlink()
        except Exception as e:  # noqa: BLE001 - cleanup is best-effort
            logger.warning("Failed to delete training media at %s: %s", storage_path, e)

    # --------------------------------------------------------------------- read

    def get_size(self, storage_path: str) -> Optional[int]:
        try:
            if storage_path.startswith("/Volumes"):
                meta = self.client.files.get_metadata(file_path=storage_path)
                return int(meta.content_length) if getattr(meta, "content_length", None) else None
            p = Path(storage_path)
            return p.stat().st_size if p.exists() else None
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to stat training media at %s: %s", storage_path, e)
            return None

    def read_range(self, storage_path: str, start: int, end: int) -> Iterator[bytes]:
        """Yield bytes of ``storage_path`` from ``start`` to ``end`` inclusive."""
        length = end - start + 1
        if length <= 0:
            return

        if storage_path.startswith("/Volumes"):
            yield from self._read_range_volume(storage_path, start, length)
        else:
            yield from self._read_range_local(storage_path, start, length)

    def _read_range_local(self, path: str, start: int, length: int) -> Iterator[bytes]:
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(_STREAM_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    def _read_range_volume(self, path: str, start: int, length: int) -> Iterator[bytes]:
        # The Files API download streams the whole object; skip to ``start`` then
        # emit up to ``length`` bytes. A deep seek re-reads from 0 — acceptable
        # for now given expected media sizes.
        resp = self.client.files.download(file_path=path)
        body = resp.contents  # file-like, streaming
        to_skip = start
        while to_skip > 0:
            chunk = body.read(min(_STREAM_CHUNK, to_skip))
            if not chunk:
                return
            to_skip -= len(chunk)
        remaining = length
        while remaining > 0:
            chunk = body.read(min(_STREAM_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
