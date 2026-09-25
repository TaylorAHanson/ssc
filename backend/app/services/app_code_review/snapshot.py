"""Extract the reviewable text of a repository tarball, in memory.

Nothing is written to disk and nothing from the repository is executed. Only
regular files are read (symlinks, hardlinks and devices are ignored), so the
archive can't point the reviewer outside itself. Dependencies, build output,
lockfiles and binaries are skipped because they are large and aren't the
app's own code.
"""
from __future__ import annotations

import io
import posixpath
import tarfile
from typing import List, Tuple

from app.services.app_code_review.models import Snapshot

EXCLUDED_DIRS = {
    ".git", "node_modules", "dist", "build", "out", ".next", ".nuxt", ".svelte-kit",
    "__pycache__", ".venv", "venv", "env", "site-packages", "vendor", "target",
    "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".turbo", ".cache",
    ".idea", ".vscode",
}

EXCLUDED_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
    "Pipfile.lock", "Cargo.lock", "bun.lockb", "composer.lock", "Gemfile.lock",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".svgz",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".zip", ".gz", ".tgz", ".tar",
    ".bz2", ".xz", ".7z", ".jar", ".whl", ".egg", ".so", ".dylib", ".dll", ".exe",
    ".pyc", ".pyo", ".class", ".o", ".a", ".parquet", ".avro", ".orc", ".pkl",
    ".pickle", ".joblib", ".npy", ".npz", ".h5", ".onnx", ".pt", ".bin", ".db",
    ".sqlite", ".mp3", ".mp4", ".mov", ".wav", ".xlsx", ".xls", ".docx", ".pptx",
}

# Read first when the budget is tight: these decide the identity question.
_PRIORITY_NAMES = {
    "app.yaml", "app.yml", "databricks.yml", "databricks.yaml", "requirements.txt",
    "pyproject.toml", "package.json", ".env",
}

# A gzip bomb (tiny archive, enormous content) costs CPU to walk even when
# nothing is kept, since reaching the next header means decompressing past the
# current member. Cap how many members, and how many declared bytes, we walk.
_MAX_MEMBERS = 20000
_MAX_WALK_BYTES = 512 * 1024 * 1024


def _skip_reason(path: str) -> str:
    parts = path.split("/")
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return "dependencies or build output"
    name = parts[-1]
    if name in EXCLUDED_FILES:
        return "lockfile"
    if name.endswith((".min.js", ".min.css", ".map")):
        return "generated or minified"
    if posixpath.splitext(name)[1].lower() in BINARY_EXTENSIONS:
        return "binary"
    return ""


def _priority(path: str) -> Tuple[int, int, str]:
    name = path.rsplit("/", 1)[-1]
    return (0 if name in _PRIORITY_NAMES else 1, path.count("/"), path)


def extract_snapshot(
    archive: bytes,
    *,
    subpath: str = "",
    max_total_bytes: int,
    max_file_bytes: int,
) -> Snapshot:
    """Read the text files of a GitHub tarball (optionally only under ``subpath``).

    Paths are kept relative to the repository root (GitHub's
    ``owner-repo-sha/`` top directory is dropped) so report links line up.
    """
    snap = Snapshot()
    prefix = subpath.strip("/") + "/" if subpath else ""
    # Contents are read during the single pass (a gzip stream only reads
    # forward cheaply), then prioritized. Collection stops at a multiple of the
    # budget so a huge repo can't balloon memory before the budget is applied.
    candidates: List[Tuple[str, bytes]] = []
    collected, collect_cap = 0, max_total_bytes * 4
    walked = 0

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for count, member in enumerate(tar):
            walked += member.size
            if count >= _MAX_MEMBERS or walked > _MAX_WALK_BYTES:
                snap.skipped.append(("(remaining files)", "archive too large to review in full"))
                snap.budget_exhausted = True
                break
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            path = posixpath.normpath(parts[1])
            if path.startswith(("../", "/")) or path == "..":
                continue
            if prefix and not path.startswith(prefix):
                continue
            reason = _skip_reason(path)
            if reason:
                snap.skipped.append((path, reason))
            elif member.size > max_file_bytes:
                snap.skipped.append((path, f"larger than {max_file_bytes // 1024} KB"))
            elif collected + member.size > collect_cap:
                snap.skipped.append((path, "review size budget reached"))
                snap.budget_exhausted = True
            else:
                fh = tar.extractfile(member)
                raw = fh.read(max_file_bytes + 1) if fh is not None else b""
                if b"\x00" in raw[:8192]:
                    snap.skipped.append((path, "binary"))
                    continue
                candidates.append((path, raw))
                collected += len(raw)

    candidates.sort(key=lambda c: _priority(c[0]))
    total = 0
    for path, raw in candidates:
        if total + len(raw) > max_total_bytes:
            snap.skipped.append((path, "review size budget reached"))
            snap.budget_exhausted = True
            continue
        snap.files[path] = raw.decode("utf-8", errors="replace")
        total += len(raw)

    return snap
