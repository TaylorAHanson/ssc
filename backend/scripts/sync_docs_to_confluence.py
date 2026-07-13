"""
Sync local Markdown files in /docs to Confluence Cloud pages.

Uses Confluence REST API v2 with Atlassian Document Format (ADF) bodies.
Tracks page IDs in docs/.confluence-sync.json so re-runs update in place.

Usage:
    # Dry run (no API calls beyond optional space lookup)
    python backend/scripts/sync_docs_to_confluence.py --dry-run

    # First sync — set credentials via env or backend/.env
    python backend/scripts/sync_docs_to_confluence.py

    # Sync a single file
    python backend/scripts/sync_docs_to_confluence.py --file ARCHITECTURE.md

Environment (also loaded from backend/.env when present):
    CONFLUENCE_BASE_URL   e.g. https://your-org.atlassian.net/wiki
    CONFLUENCE_EMAIL      Atlassian account email
    CONFLUENCE_API_TOKEN  API token from https://id.atlassian.com/manage-profile/security/api-tokens
    CONFLUENCE_SPACE_KEY  Space key, e.g. ENG (preferred)
    CONFLUENCE_SPACE_ID   Numeric space id (optional if SPACE_KEY is set)
    CONFLUENCE_PARENT_PAGE_ID  Optional parent page for all synced docs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

try:
    from marklassian import markdown_to_adf
except ImportError as exc:  # pragma: no cover - import guard for clearer CLI error
    raise SystemExit(
        "marklassian is required. Install project deps: pip install -r backend/requirements.txt"
    ) from exc

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
MANIFEST_PATH = DOCS_DIR / ".confluence-sync.json"

RELATIVE_MD_LINK = re.compile(r"\]\((\./)?([^)#]+\.md)(#[^)]+)?\)")
LEADING_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ConfluenceConfig:
    base_url: str
    email: str
    api_token: str
    space_key: str | None
    space_id: str | None
    parent_page_id: str | None


class ConfluenceClient:
    def __init__(self, config: ConfluenceConfig) -> None:
        self._config = config
        self._base = config.base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            auth=(config.email, config.api_token),
            headers={"Accept": "application/json"},
            timeout=60.0,
        )
        self._space_id = config.space_id

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ConfluenceClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def resolve_space_id(self) -> str:
        if self._space_id:
            return self._space_id
        if not self._config.space_key:
            raise ValueError("Set CONFLUENCE_SPACE_KEY or CONFLUENCE_SPACE_ID")
        response = self._client.get(
            "/api/v2/spaces",
            params={"keys": self._config.space_key, "limit": 1},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            raise ValueError(f"Confluence space not found for key {self._config.space_key!r}")
        self._space_id = str(results[0]["id"])
        self._space_key = results[0].get("key") or self._config.space_key
        return self._space_id

    @property
    def space_key(self) -> str | None:
        return getattr(self, "_space_key", None) or self._config.space_key

    def get_page(self, page_id: str) -> dict[str, Any]:
        response = self._client.get(
            f"/api/v2/pages/{page_id}",
            params={"body-format": "atlas_doc_format"},
        )
        response.raise_for_status()
        return response.json()

    def create_page(
        self,
        *,
        title: str,
        adf_body: dict[str, Any],
        parent_page_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "spaceId": self.resolve_space_id(),
            "status": "current",
            "title": title,
            "body": {
                "representation": "atlas_doc_format",
                "value": json.dumps(adf_body),
            },
        }
        if parent_page_id:
            payload["parentId"] = parent_page_id
        response = self._client.post("/api/v2/pages", json=payload)
        response.raise_for_status()
        return response.json()

    def update_page(
        self,
        *,
        page_id: str,
        title: str,
        adf_body: dict[str, Any],
        version_number: int,
        version_message: str,
    ) -> dict[str, Any]:
        payload = {
            "id": page_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "atlas_doc_format",
                "value": json.dumps(adf_body),
            },
            "version": {
                "number": version_number,
                "message": version_message,
            },
        }
        response = self._client.put(f"/api/v2/pages/{page_id}", json=payload)
        response.raise_for_status()
        return response.json()


def load_env() -> None:
    if load_dotenv is None:
        return
    env_path = REPO_ROOT / "backend" / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def parse_config(args: argparse.Namespace) -> ConfluenceConfig:
    base_url = (args.base_url or os.environ.get("CONFLUENCE_BASE_URL", "")).strip()
    email = (args.email or os.environ.get("CONFLUENCE_EMAIL", "")).strip()
    api_token = (args.api_token or os.environ.get("CONFLUENCE_API_TOKEN", "")).strip()
    space_key = (args.space_key or os.environ.get("CONFLUENCE_SPACE_KEY", "")).strip() or None
    space_id = (args.space_id or os.environ.get("CONFLUENCE_SPACE_ID", "")).strip() or None
    parent_page_id = (
        args.parent_page_id or os.environ.get("CONFLUENCE_PARENT_PAGE_ID", "")
    ).strip() or None

    missing = [
        name
        for name, value in [
            ("CONFLUENCE_BASE_URL", base_url),
            ("CONFLUENCE_EMAIL", email),
            ("CONFLUENCE_API_TOKEN", api_token),
        ]
        if not value
    ]
    if missing and not args.dry_run:
        raise SystemExit(
            "Missing required Confluence settings: "
            + ", ".join(missing)
            + ". Set env vars or pass CLI flags. Use --dry-run to preview without credentials."
        )
    if not space_key and not space_id and not args.dry_run:
        raise SystemExit("Set CONFLUENCE_SPACE_KEY or CONFLUENCE_SPACE_ID")

    return ConfluenceConfig(
        base_url=base_url,
        email=email,
        api_token=api_token,
        space_key=space_key,
        space_id=space_id,
        parent_page_id=parent_page_id,
    )


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {"pages": {}}
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_manifest(manifest: dict[str, Any]) -> None:
    manifest.setdefault("pages", {})
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_docs(selected_file: str | None) -> list[Path]:
    if selected_file:
        path = Path(selected_file)
        if not path.is_absolute():
            path = DOCS_DIR / path.name
        if not path.is_file():
            raise SystemExit(f"Doc not found: {path}")
        return [path]
    return sorted(DOCS_DIR.glob("*.md"))


def title_from_markdown(markdown: str, filename: str) -> str:
    match = LEADING_H1.search(markdown)
    if match:
        return match.group(1).strip()
    stem = Path(filename).stem.replace("_", " ")
    return stem


def strip_leading_h1(markdown: str) -> str:
    """Avoid duplicating the page title as the first heading in Confluence."""
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        remainder = lines[1:]
        while remainder and not remainder[0].strip():
            remainder = remainder[1:]
        return "\n".join(remainder)
    return markdown


def rewrite_internal_links(
    markdown: str,
    *,
    base_url: str,
    space_key: str | None,
    manifest_pages: dict[str, Any],
) -> str:
    def replace(match: re.Match[str]) -> str:
        filename = Path(match.group(2)).name
        anchor = match.group(3) or ""
        entry = manifest_pages.get(filename)
        if not entry or not entry.get("page_id"):
            return match.group(0)
        page_id = entry["page_id"]
        title = entry.get("title", Path(filename).stem)
        slug = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "+")
        space_segment = f"{space_key}/" if space_key else ""
        url = f"{base_url.rstrip('/')}/spaces/{space_segment}pages/{page_id}/{slug}{anchor}"
        prefix = match.group(0).split("(")[0]
        return f"{prefix}({url})"

    return RELATIVE_MD_LINK.sub(replace, markdown)


def markdown_to_confluence_body(markdown: str) -> dict[str, Any]:
    return markdown_to_adf(markdown)


def file_has_internal_links(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return bool(RELATIVE_MD_LINK.search(text))


def sync_file(
    path: Path,
    *,
    client: ConfluenceClient | None,
    config: ConfluenceConfig,
    manifest: dict[str, Any],
    dry_run: bool,
    strip_h1: bool,
    rewrite_links: bool,
    space_key: str | None = None,
) -> bool:
    rel_name = path.name
    raw_markdown = path.read_text(encoding="utf-8")
    title = title_from_markdown(raw_markdown, rel_name)
    markdown = raw_markdown
    if rewrite_links:
        markdown = rewrite_internal_links(
            raw_markdown,
            base_url=config.base_url,
            space_key=space_key or config.space_key or manifest.get("space_key"),
            manifest_pages=manifest.get("pages", {}),
        )
    if strip_h1:
        markdown = strip_leading_h1(markdown)
    digest = content_hash(markdown)

    pages: dict[str, Any] = manifest.setdefault("pages", {})
    entry = pages.get(rel_name, {})
    if entry.get("content_hash") == digest and entry.get("page_id"):
        logger.info("Unchanged, skipping %s", rel_name)
        return False

    action = "update" if entry.get("page_id") else "create"
    logger.info("%s %s -> %r", action.upper(), rel_name, title)

    if dry_run:
        return True

    assert client is not None
    adf_body = markdown_to_confluence_body(markdown)

    if entry.get("page_id"):
        page_id = str(entry["page_id"])
        current = client.get_page(page_id)
        next_version = int(current["version"]["number"]) + 1
        result = client.update_page(
            page_id=page_id,
            title=title,
            adf_body=adf_body,
            version_number=next_version,
            version_message=f"Sync from {rel_name}",
        )
    else:
        result = client.create_page(
            title=title,
            adf_body=adf_body,
            parent_page_id=config.parent_page_id,
        )
        page_id = str(result["id"])

    pages[rel_name] = {
        "page_id": page_id,
        "title": title,
        "content_hash": digest,
        "source_mtime": path.stat().st_mtime,
    }
    manifest["space_id"] = client.resolve_space_id()
    if client.space_key:
        manifest["space_key"] = client.space_key
    if config.parent_page_id:
        manifest["parent_page_id"] = config.parent_page_id
    return True


def run_sync_pass(
    docs: list[Path],
    *,
    client: ConfluenceClient | None,
    config: ConfluenceConfig,
    manifest: dict[str, Any],
    dry_run: bool,
    strip_h1: bool,
    rewrite_links: bool,
    only_with_links: bool,
    space_key: str | None = None,
) -> bool:
    changed = False
    for path in docs:
        if only_with_links and not file_has_internal_links(path):
            continue
        if sync_file(
            path,
            client=client,
            config=config,
            manifest=manifest,
            dry_run=dry_run,
            strip_h1=strip_h1,
            rewrite_links=rewrite_links,
            space_key=space_key,
        ):
            changed = True
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview sync without writing to Confluence")
    parser.add_argument("--file", help="Sync only this file under docs/ (basename or path)")
    parser.add_argument("--keep-leading-h1", action="store_true", help="Keep the first # heading in page body")
    parser.add_argument("--base-url", help="Override CONFLUENCE_BASE_URL")
    parser.add_argument("--email", help="Override CONFLUENCE_EMAIL")
    parser.add_argument("--api-token", help="Override CONFLUENCE_API_TOKEN")
    parser.add_argument("--space-key", help="Override CONFLUENCE_SPACE_KEY")
    parser.add_argument("--space-id", help="Override CONFLUENCE_SPACE_ID")
    parser.add_argument("--parent-page-id", help="Override CONFLUENCE_PARENT_PAGE_ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_env()
    args = build_parser().parse_args(argv)
    config = parse_config(args)
    manifest = load_manifest()

    docs = discover_docs(args.file)
    if not docs:
        logger.warning("No markdown files found in %s", DOCS_DIR)
        return 0

    logger.info("Syncing %d doc(s) from %s", len(docs), DOCS_DIR)

    strip_h1 = not args.keep_leading_h1

    try:
        if args.dry_run:
            run_sync_pass(
                docs,
                client=None,
                config=config,
                manifest=manifest,
                dry_run=True,
                strip_h1=strip_h1,
                rewrite_links=False,
                only_with_links=False,
            )
            run_sync_pass(
                docs,
                client=None,
                config=config,
                manifest=manifest,
                dry_run=True,
                strip_h1=strip_h1,
                rewrite_links=True,
                only_with_links=True,
            )
            return 0

        with ConfluenceClient(config) as client:
            client.resolve_space_id()
            run_sync_pass(
                docs,
                client=client,
                config=config,
                manifest=manifest,
                dry_run=False,
                strip_h1=strip_h1,
                rewrite_links=False,
                only_with_links=False,
                space_key=client.space_key,
            )
            run_sync_pass(
                docs,
                client=client,
                config=config,
                manifest=manifest,
                dry_run=False,
                strip_h1=strip_h1,
                rewrite_links=True,
                only_with_links=True,
                space_key=client.space_key,
            )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        logger.error("Confluence API error %s: %s", exc.response.status_code, detail)
        return 1
    except httpx.RequestError as exc:
        logger.error("Confluence request failed: %s", exc)
        return 1

    save_manifest(manifest)
    logger.info("Wrote manifest %s", MANIFEST_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
